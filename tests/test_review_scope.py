"""Comparison endpoints, stale responses, and the keyboard scope workflow."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from typing import cast

import pytest
from rich.console import Console
from rich.text import Text
from textual.geometry import Region
from textual.widgets import Button, Input, OptionList, Static

from rit.app import RitApp
from rit.services.github import GitHubError, GitHubService
from rit.services.pr_comparison import fetch_comparison_files, fetch_review_history
from rit.state import store as store_module
from rit.state.models import PR, FileViewedState, PRFile, PRReview, PRUser, ReviewState
from rit.state.review_scope import PRCommit, ReviewHistory, ReviewScope
from rit.state.store import PRStore
from rit.ui.screens.main import MainScreen
from rit.ui.screens.review_scope import ReviewScopePicker
from tests.conftest import wait_until

BASE, A, B, C = (character * 40 for character in "0abc")


def history() -> ReviewHistory:
    return ReviewHistory(
        BASE,
        C,
        tuple(
            PRCommit(sha=sha, title=title, parent_sha=parent, parent_count=1)
            for sha, parent, title in (
                (A, BASE, "Baseline"),
                (B, A, "Fix anchors"),
                (C, B, "Tests"),
            )
        ),
        PRReview(
            id=1,
            user=PRUser(login="me"),
            state=ReviewState.DISMISSED,
            commit_sha=A,
            submitted_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def patch(value: str) -> str:
    return f"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+{value}\n"


def file(value: str = "current") -> PRFile:
    return PRFile(filename="a.py", patch=patch(value), additions=1, deletions=1)


def test_review_scope_uses_real_parent_and_rejects_disconnected_spans() -> None:
    data = history()
    assert data.since_review().base_sha == A
    assert data.select_commits(B).base_sha == A
    selected = data.select_commits(C, B)
    assert (selected.base_sha, selected.head_sha, selected.commit_shas) == (
        A,
        C,
        (B, C),
    )
    assert data.adjacent(data.select_commits(B), 1) == data.select_commits(C)
    assert data.adjacent(data.select_commits(C), 1) is None
    assert data.adjacent(selected, 1) is None
    branch = data.commits[-1].model_copy(update={"parent_sha": A, "parent_count": 2})
    divergent = ReviewHistory(BASE, C, (*data.commits[:-1], branch))
    assert divergent.select_commits(C).base_sha == A
    assert "merge" in divergent.select_commits(C).detail.lower()
    with pytest.raises(ValueError, match="first-parent"):
        divergent.select_commits(B, C)
    with pytest.raises(ValueError, match="No submitted"):
        divergent.since_review()
    with pytest.raises(ValueError, match="no available"):
        ReviewHistory(BASE, C, (), PRReview()).since_review()
    assert PRReview.model_validate({"commit": {"oid": None}}).commit_sha == ""


async def test_history_paginates_both_connections_and_uses_submission_time() -> None:
    calls: list[tuple[str, str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text
        request = json.loads(input_text)
        variables = request["variables"]
        cursor = variables.get("after")
        reviews = "reviews(first:" in request["query"]
        field = "reviews" if reviews else "commits"
        calls.append((field, cursor))
        if reviews:
            assert variables["author"] == "me"
            nodes = [
                {
                    "databaseId": 2 if cursor else 1,
                    "state": "DISMISSED" if cursor else "APPROVED",
                    "author": {"login": "me"},
                    "submittedAt": "2026-02-01T00:00:00Z"
                    if cursor
                    else "2026-01-01T00:00:00Z",
                    "commit": {"oid": B if cursor else A},
                },
            ]
            if cursor:
                nodes.append(
                    {
                        "databaseId": 3,
                        "state": "PENDING",
                        "author": {"login": "me"},
                        "commit": {"oid": C},
                    }
                )
        else:
            nodes = [
                {
                    "commit": {
                        "oid": B if cursor else A,
                        "messageHeadline": "Commit",
                        "parents": {
                            "nodes": [{"oid": A if cursor else BASE}],
                            "totalCount": 1,
                        },
                    }
                }
            ]
        return json.dumps(
            {
                "data": {
                    "viewer": {"login": "me"},
                    "repository": {
                        "pullRequest": {
                            "baseRefOid": BASE,
                            "headRefOid": C,
                            field: {
                                "totalCount": 3 if reviews else 2,
                                "nodes": nodes,
                                "pageInfo": {
                                    "hasNextPage": not bool(cursor),
                                    "endCursor": "next",
                                },
                            },
                        }
                    },
                }
            }
        )

    data = await fetch_review_history("o", "r", 1, runner=runner)
    assert calls == [
        ("commits", None),
        ("commits", "next"),
        ("reviews", None),
        ("reviews", "next"),
    ]
    assert data.last_review is not None and data.last_review.id == 2
    assert data.since_review().base_sha == B


@pytest.mark.parametrize(
    "case", ["valid", "diverged", "truncated", "limit", "empty", "same"]
)
async def test_comparison_requires_complete_ancestor_diff(case: str) -> None:
    calls: list[list[str]] = []
    scope = ReviewScope("since", A, A if case == "same" else C)

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append(args)
        if "-H" in args:
            return "" if case == "truncated" else patch("changed")
        metadata = {"filename": "a.py", "additions": 1, "deletions": 1}
        return json.dumps(
            {
                "status": "diverged" if case == "diverged" else "ahead",
                "merge_base_commit": {"sha": A},
                "files": []
                if case == "empty"
                else [metadata] * (300 if case == "limit" else 1),
            }
        )

    if case in {"diverged", "truncated", "limit"}:
        with pytest.raises(ValueError):
            await fetch_comparison_files("o", "r", scope, runner=runner)
    else:
        result = await fetch_comparison_files("o", "r", scope, runner=runner)
        assert len(result) == (1 if case == "valid" else 0)
    if case == "same":
        assert calls == []
    if case == "valid":
        assert result[0].filename == "a.py"
        assert "+changed" in result[0].patch


async def test_scope_switch_rejects_stale_result_preserves_drafts_and_old_view_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(base_sha=BASE, head_sha=C)
    store.state.files = [file()]
    store.state.files_by_filename = {"a.py": store.state.files[0]}
    draft = store.save_pending_file_comment("keep me", path="a.py")
    slow_started, release = asyncio.Event(), asyncio.Event()
    first, second = history().select_commits(B), history().select_commits(C)

    async def compare(scope: ReviewScope) -> list[PRFile]:
        if scope == first:
            slow_started.set()
            await release.wait()
        return [file(scope.head_sha)]

    monkeypatch.setattr(store._service, "get_comparison_files", compare)
    pending = asyncio.create_task(store.select_review_scope(first))
    await slow_started.wait()
    assert await store.select_review_scope(second)
    release.set()
    assert not await pending
    assert store.state.scope == second
    assert store.state.pending_review.comments == [draft]
    assert C in store.state.files[0].patch
    with pytest.raises(GitHubError, match="All changes"):
        await store.submit_inline_comment(
            "wrong coordinates", path="a.py", line=1, side="RIGHT"
        )
    with pytest.raises(GitHubError, match="All changes"):
        await store.submit_review("APPROVE")
    assert not store.viewed_files.toggle("a.py")

    async def fail(scope: ReviewScope) -> list[PRFile]:
        raise GitHubError("offline")

    monkeypatch.setattr(store._service, "get_comparison_files", fail)
    assert not await store.select_review_scope(first)
    assert store.state.scope == second and store.state.scope_error == "offline"
    assert store.state.requested_scope == first
    assert not store.state.scope_loading


async def test_scoped_preview_uses_endpoint_and_rejects_late_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(head_sha=C)
    store.state.scope = history().select_commits(B)
    removed = PRFile(filename="gone.py", status="removed")
    store.state.files = [removed]
    store.state.files_by_filename = {removed.filename: removed}
    refs: list[str] = []

    async def content(path: str, ref: str) -> str:
        refs.append(ref)
        return "old file"

    monkeypatch.setattr(store._service, "get_file_content", content)
    assert await store.get_file_content("a.py") == "old file"
    assert await store.get_file_content("gone.py") == "old file"
    assert refs == [B, A]
    started, release = asyncio.Event(), asyncio.Event()

    async def late_content(path: str, ref: str) -> str:
        started.set()
        await release.wait()
        return "stale"

    monkeypatch.setattr(store._service, "get_file_content", late_content)
    pending = asyncio.create_task(store.get_file_content("uncached.py"))
    await started.wait()
    store.state.scope = history().select_commits(C)
    release.set()
    assert await pending is None


async def test_late_parse_does_not_populate_another_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    original = file("old comparison")
    store.state.files = [original]
    store.state.files_by_filename = {original.filename: original}
    started, release = Event(), Event()
    parse = store_module.diff_from_file_patch

    def slow_parse(source: PRFile):
        started.set()
        assert release.wait(3)
        return parse(source)

    monkeypatch.setattr(store_module, "diff_from_file_patch", slow_parse)
    pending = asyncio.create_task(store.get_file_diff_async(original.filename))
    try:
        await wait_until(started.is_set, timeout=2)
        store.state.file_diffs = {}
        store.state.files_by_filename = {"a.py": file("new comparison")}
    finally:
        release.set()
    assert await pending is None
    assert store.state.file_diffs == {}


async def test_inflight_viewed_write_does_not_mark_comparison_viewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=1, node_id="pr", base_sha=BASE, head_sha=C)
    store.state.files = [file()]
    store.state.files_by_filename = {"a.py": store.state.files[0]}
    started, release = asyncio.Event(), asyncio.Event()

    async def mark(pr: str, path: str) -> None:
        started.set()
        await release.wait()

    async def compare(scope: ReviewScope) -> list[PRFile]:
        return [file("range")]

    monkeypatch.setattr(store._service, "mark_file_as_viewed", mark)
    monkeypatch.setattr(store._service, "get_comparison_files", compare)
    pending = asyncio.create_task(store.set_file_viewed("a.py", viewed=True))
    await started.wait()
    assert await store.select_review_scope(history().select_commits(B))
    release.set()
    await pending
    assert store.state.files[0].viewer_viewed_state == FileViewedState.UNVIEWED


@pytest.mark.parametrize("width", [40, 70])
def test_scope_commit_rows_never_wrap_and_keep_checkpoint_badges(width: int) -> None:
    data = history()
    assert data.last_review is not None
    data = replace(
        data, last_review=data.last_review.model_copy(update={"commit_sha": C})
    )
    picker = ReviewScopePicker(PRStore(), Region(10, 6, 18, 1))
    picker.history = data
    commit = data.commits[-1].model_copy(
        update={"title": "Long commit title with Unicode 수정 사항 " * 8}
    )
    console = Console(width=width)
    lines = console.render_lines(picker._commit_row(commit, True), console.options)
    assert len(lines) == 1
    text = "".join(segment.text for segment in lines[0])
    assert C[:7] in text and "HEAD · LAST REVIEW" in text
    assert "…" in text


async def test_compact_scope_picker_keyboard_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = history()

    async def overview(store: PRStore) -> None:
        store.state.pr = PR(number=1, base_sha=BASE, head_sha=C, changed_files=1)

    async def files(
        service: GitHubService, number: int, *, total_count: int | None = None
    ) -> list[PRFile]:
        return [file()]

    async def review_history(service: GitHubService, number: int) -> ReviewHistory:
        return data

    async def compare(service: GitHubService, scope: ReviewScope) -> list[PRFile]:
        return [] if scope.kind == "since" else [file(scope.head_sha)]

    monkeypatch.setattr(PRStore, "load_overview", overview)
    monkeypatch.setattr(GitHubService, "get_pr_files", files)
    monkeypatch.setattr(GitHubService, "get_review_history", review_history)
    monkeypatch.setattr(GitHubService, "get_comparison_files", compare)
    app = RitApp(owner="o", repo="r", pr_number=1)
    async with app.run_test(size=(110, 32)) as pilot:
        main = cast(MainScreen, app.screen)
        await wait_until(lambda: main.store.state.pr is not None, timeout=2)
        main.switch_tab(1)
        await wait_until(lambda: main.file_changes.workspace_ready, timeout=3)
        main.file_changes.diff_view.focus()
        await pilot.pause()
        workspace_region = main.file_changes.diff_view.region
        scope_button = main.file_changes.query_one("#review-scope-open", Button)
        anchor = scope_button.region
        scope_bar = main.file_changes.query_one("#review-scope-bar")
        assert scope_bar.region.height == 3
        assert anchor.y == scope_bar.region.y + 1
        assert anchor.bottom == scope_bar.region.bottom - 1
        assert scope_button.styles.padding.left == 2
        assert scope_button.styles.padding.right == 2
        stats = main.file_changes.query_one("#review-scope-stats", Static).content
        assert isinstance(stats, Text)
        assert [
            (stats.plain[span.start : span.end], span.style) for span in stats.spans
        ] == [("+1", "green"), ("−1", "red")]
        await pilot.press("s")
        picker = await wait_until(
            lambda: (
                app.screen
                if isinstance(app.screen, ReviewScopePicker) and app.screen.history
                else None
            ),
            timeout=2,
        )
        await pilot.pause()
        dialog = picker.query_one("#review-scope-dialog")
        assert picker.styles.background.a == 0
        assert main in app._background_screens
        assert "Files" in app.export_screenshot()
        assert main.file_changes.diff_view.region == workspace_region
        assert dialog.region.y == anchor.bottom
        assert dialog.region.x == anchor.x
        assert dialog.region.right <= 110
        assert dialog.region.height == picker.query_one(OptionList).option_count + 8
        assert set(picker.query_one(OptionList)._heights.values()) == {1}
        await pilot.resize_terminal(70, 20)
        await pilot.pause()
        assert dialog.region.right < 70 and dialog.region.bottom < 20
        assert set(picker.query_one(OptionList)._heights.values()) == {1}
        await pilot.resize_terminal(110, 32)
        picker.query_one(OptionList).highlighted = picker.query_one(
            OptionList
        ).get_option_index(B)
        await pilot.press("enter")
        await wait_until(
            lambda: (
                main.store.state.scope.head_sha == B
                and main.file_changes.workspace_ready
            ),
            timeout=3,
        )
        assert "Read-only" in str(
            main.file_changes.query_one("#review-scope-detail", Static).content
        )
        assert B in main.store.state.files[0].patch
        await wait_until(lambda: main.file_changes.diff_view.has_focus, timeout=2)
        assert scope_button.content_size.width >= len(str(scope_button.label))
        assert not await main.file_changes.diff_view.open_inline_comment_editor()
        await pilot.press(".")
        await wait_until(
            lambda: (
                main.store.state.scope.head_sha == C
                and main.file_changes.workspace_ready
            ),
            timeout=3,
        )
        assert main.file_changes.query_one("#review-scope-next", Button).disabled
        await pilot.press(",")
        await wait_until(
            lambda: (
                main.store.state.scope.head_sha == B
                and main.file_changes.workspace_ready
            ),
            timeout=3,
        )
        await pilot.press("s")
        picker = await wait_until(
            lambda: (
                app.screen
                if isinstance(app.screen, ReviewScopePicker) and app.screen.history
                else None
            ),
            timeout=2,
        )
        picker.query_one(OptionList).highlighted = picker.query_one(
            OptionList
        ).get_option_index(C)
        await pilot.press("shift+down")
        assert "2 commits" in str(
            picker.query_one("#review-scope-preview", Static).content
        )
        picker = await wait_until(
            lambda: (
                app.screen
                if isinstance(app.screen, ReviewScopePicker) and app.screen.history
                else None
            ),
            timeout=2,
        )
        picker.query_one(OptionList).highlighted = picker.query_one(
            OptionList
        ).get_option_index(C)
        await pilot.press("shift+down")
        assert "2 commits" in str(
            picker.query_one("#review-scope-preview", Static).content
        )
        await pilot.press("enter")
        await wait_until(
            lambda: (
                main.store.state.scope.kind == "range"
                and main.file_changes.workspace_ready
            ),
            timeout=3,
        )
        assert main.store.state.scope.commit_shas == (B, C)
        assert main.store.state.scope.base_sha == A
        await pilot.press("s")
        picker = await wait_until(
            lambda: (
                app.screen
                if isinstance(app.screen, ReviewScopePicker) and app.screen.history
                else None
            ),
            timeout=2,
        )
        await pilot.press("/")
        assert isinstance(picker.focused, Input)
        picker.query_one(Input).value = "Fix anchors"
        await pilot.pause()
        assert picker.query_one(OptionList).get_option(B)
        await pilot.press("escape")
        assert main.store.state.scope.kind == "range"
        await wait_until(lambda: main.file_changes.diff_view.has_focus, timeout=2)
        await pilot.click("#review-scope-open")
        picker = await wait_until(
            lambda: (
                app.screen
                if isinstance(app.screen, ReviewScopePicker) and app.screen.history
                else None
            ),
            timeout=2,
        )
        picker.query_one(OptionList).highlighted = picker.query_one(
            OptionList
        ).get_option_index("since")
        await pilot.press("enter")
        await wait_until(
            lambda: (
                main.store.state.scope.kind == "since"
                and not main.store.state.scope_loading
            ),
            timeout=2,
        )
        assert not main.store.state.files
        assert main.file_changes.query_one("#review-scope-empty").display
        assert not main.file_changes.diff_view.display
        await pilot.click("#review-scope-all")
        await wait_until(
            lambda: (
                main.store.state.scope.kind == "all"
                and main.file_changes.workspace_ready
            ),
            timeout=3,
        )
        assert main.store.review_writable
        assert "current" in main.store.state.files[0].patch
