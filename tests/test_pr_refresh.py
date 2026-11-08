import asyncio
import json
from typing import cast

import pytest
from textual.screen import ModalScreen
from textual.widgets import Button, Static, Tooltip

from rit.app import RitApp
from rit.services.github import GitHubError, GitHubService
from rit.services.pr_discussion import PRDiscussion
from rit.state.models import (
    PR,
    LoadingState,
    NodeList,
    PendingReviewComment,
    PRComment,
    PRFile,
    ReviewThread,
)
from rit.state.review_scope import ReviewHistory, ReviewScope
from rit.state.store import PRStore
from rit.ui.screens.main import MainScreen
from tests.conftest import wait_until
from tests.test_review_scope import BASE, A, B, C, file, history


def loaded_store() -> PRStore:
    store = PRStore(owner="o", repo="r", pr_number=1)
    store.state.pr = PR(number=1, base_sha=BASE, head_sha=A, changed_files=1)
    store.state.files = [file()]
    store.state.files_by_filename = {"a.py": store.state.files[0]}
    store.state.selected_file = "a.py"
    store.state.files_loading = LoadingState.LOADED
    store._service.accept_pr_revision(1, (BASE, A))
    return store


def stage_refresh(monkeypatch: pytest.MonkeyPatch, store: PRStore) -> None:
    async def summary(number: int, *, pin_revision: bool = True) -> PR:
        assert not pin_revision
        return PR(number=number, base_sha=BASE, head_sha=B, changed_files=1)

    async def files(number: int, *, expected_refs: tuple[str, str]) -> list[PRFile]:
        assert expected_refs == (BASE, B)
        return [file("refreshed")]

    async def refs(number: int) -> tuple[str, str]:
        return BASE, B

    async def discussion(number: int) -> PRDiscussion:
        return PRDiscussion(body="", reviews=[], issue_comments=[], review_threads=[])

    monkeypatch.setattr(store._service, "get_pr_discussion", discussion)
    monkeypatch.setattr(store._service, "get_pr_summary", summary)
    monkeypatch.setattr(store._service, "get_pr_files", files)
    monkeypatch.setattr(store._service, "get_pr_refs", refs)


@pytest.mark.parametrize("invalid", [None, "short", 12, "g" * 40])
async def test_probe_validates_external_refs_without_changing_service_pin(
    monkeypatch: pytest.MonkeyPatch, invalid: object
) -> None:
    service = GitHubService(owner="o", repo="r")
    service.accept_pr_revision(1, (BASE, A))
    value: object = B

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        assert json.loads(input_text)["variables"]["number"] == 1
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "baseRefOid": BASE,
                            "headRefOid": value,
                        }
                    }
                }
            }
        )

    monkeypatch.setattr(service, "_run_gh", runner)
    assert await service.get_pr_refs(1) == (BASE, B)
    assert service._pr_revision == (1, BASE, A)
    value = invalid
    with pytest.raises(GitHubError, match="invalid PR revisions"):
        await service.get_pr_refs(1)
    assert service._pr_revision == (1, BASE, A)


async def test_detection_preserves_workspace_and_known_availability_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    pr, files = store.state.pr, store.state.files
    draft = store.save_pending_file_comment("keep me", path="a.py")
    await store.check_for_updates()
    assert store.state.observed_refs == (BASE, B)
    assert store.state.pr is pr and store.state.files is files
    assert store.state.selected_file == "a.py"
    assert store.state.pending_review.comments == [draft]
    assert store._service._pr_revision == (1, BASE, A)

    async def fail(number: int) -> tuple[str, str]:
        raise GitHubError("offline")

    monkeypatch.setattr(store._service, "get_pr_refs", fail)
    await store.check_for_updates()
    assert store.state.observed_refs == (BASE, B)
    assert store.state.probe_error == "offline"
    assert store.state.pr is pr and store.state.files is files


async def test_refresh_publishes_once_and_keeps_selection_and_file_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    draft = store.save_pending_file_comment("keep me", path="a.py")
    store.state.pending_review.set_body("review body")
    messages = []
    store.set_message_sink(messages.append)
    assert await store.refresh_files()
    assert store.state.pr is not None and store.state.pr.head_sha == B
    assert "refreshed" in store.state.files[0].patch
    assert store.state.selected_file == "a.py"
    assert store.state.pending_review.comments == [draft]
    assert store.state.pending_review.body == "review body"
    assert store._service._pr_revision == (1, BASE, B)
    assert store.state.file_revision == 1
    loaded = [message for message in messages if isinstance(message, PRStore.PRLoaded)]
    assert len(loaded) == 1 and loaded[0].files_staged
    assert not await store.refresh_files()
    assert store.state.file_revision == 1


async def test_refresh_retries_failed_workspace_without_new_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    assert store.state.pr is not None
    store.state.pr.head_sha = B
    store._service.accept_pr_revision(1, (BASE, B))
    store.state.files_loading = LoadingState.ERROR
    store.state.error = "Previous load failed"
    draft = PendingReviewComment(path="a.py", line=1, body="same revision")
    store.state.pending_review.comments = [draft]

    assert await store.refresh_files()
    assert store.state.files_loading == LoadingState.LOADED
    assert store.state.refresh_loading == LoadingState.LOADED
    assert store.state.pr.head_sha == B
    assert store.state.error is None
    assert "refreshed" in store.state.files[0].patch
    assert store.state.pending_review.comments == [draft]


@pytest.mark.parametrize("failure", ["summary", "files", "discussion", "push"])
async def test_refresh_failure_retains_old_workspace_and_pin(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    pr, files = store.state.pr, store.state.files
    draft = store.save_pending_file_comment("keep me", path="a.py")

    async def fail(*args: object, **kwargs: object) -> object:
        raise GitHubError("offline")

    async def pushed(number: int) -> tuple[str, str]:
        return BASE, C

    if failure == "push":
        monkeypatch.setattr(store._service, "get_pr_refs", pushed)
    else:
        monkeypatch.setattr(store._service, f"get_pr_{failure}", fail)
    assert not await store.refresh_files()
    assert store.state.refresh_error
    assert store.state.refresh_loading == LoadingState.ERROR
    assert store.state.pr is pr and store.state.files is files
    assert store.state.selected_file == "a.py"
    assert store.state.pending_review.comments == [draft]
    assert store.state.file_revision == 0
    assert store._service._pr_revision == (1, BASE, A)


async def test_late_probe_cannot_restore_stale_status_after_refresh_or_hide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(number: int) -> tuple[str, str]:
        started.set()
        await release.wait()
        return BASE, C

    monkeypatch.setattr(store._service, "get_pr_refs", slow)
    probe = asyncio.create_task(store.check_for_updates())
    await started.wait()
    stage_refresh(monkeypatch, store)
    assert await store.refresh_files()
    release.set()
    await probe
    assert store.state.observed_refs == (BASE, B)
    started.clear()
    release.clear()
    monkeypatch.setattr(store._service, "get_pr_refs", slow)
    probe = asyncio.create_task(store.check_for_updates())
    await started.wait()
    store.discard_revision_probe()
    release.set()
    await probe
    assert store.state.observed_refs == (BASE, B)


async def test_refresh_serializes_scope_loading_and_rejects_double_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    started, release = asyncio.Event(), asyncio.Event()

    async def files(number: int, *, expected_refs: tuple[str, str]) -> list[PRFile]:
        started.set()
        await release.wait()
        return [file("new")]

    monkeypatch.setattr(store._service, "get_pr_files", files)
    refresh = asyncio.create_task(store.refresh_files())
    await started.wait()
    assert not await store.refresh_files()
    assert not await store.select_review_scope(history().select_commits(B))
    assert store.state.pr is not None and store.state.pr.head_sha == A
    release.set()
    assert await refresh
    store.state.scope_loading = True
    assert not await store.refresh_files()


@pytest.mark.parametrize("remaining", [True, False])
async def test_removed_selection_falls_back_to_first_or_empty(
    monkeypatch: pytest.MonkeyPatch, remaining: bool
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)

    async def files(number: int, *, expected_refs: tuple[str, str]) -> list[PRFile]:
        return [PRFile(filename="b.py")] if remaining else []

    monkeypatch.setattr(store._service, "get_pr_files", files)
    assert await store.refresh_files()
    assert store.state.selected_file == ("b.py" if remaining else None)


@pytest.mark.parametrize("kind", ["commit", "range", "since"])
async def test_refresh_keeps_explicit_endpoints_but_recomputes_since_review(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    data = history()
    original = (
        data.since_review()
        if kind == "since"
        else data.select_commits(B, C if kind == "range" else None)
    )
    store.state.scope = original
    newer = data.__class__(BASE, B, data.commits[:2], data.last_review)

    async def review_history(number: int) -> ReviewHistory:
        return newer

    async def comparison(scope: ReviewScope) -> list[PRFile]:
        assert scope == (newer.since_review() if kind == "since" else original)
        return [file("comparison")]

    monkeypatch.setattr(store._service, "get_review_history", review_history)
    monkeypatch.setattr(store._service, "get_comparison_files", comparison)
    assert await store.refresh_files()
    assert store.state.scope == (newer.since_review() if kind == "since" else original)


@pytest.mark.parametrize("push_during_discussion", [False, True])
async def test_refresh_stages_updated_submitted_thread_anchors(
    monkeypatch: pytest.MonkeyPatch, push_during_discussion: bool
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    old = PRComment(id=1, path="a.py", line=1, side="RIGHT", body="submitted")
    old_thread = ReviewThread(
        id="thread", path="a.py", line=1, comments_connection=NodeList.from_nodes([old])
    )
    store.state.comments = [old]
    store.state.comments_by_file = {"a.py": [old]}
    store.state.review_threads = [old_thread]
    updated = old.model_copy(update={"line": 9})
    new_thread = old_thread.model_copy(
        update={"line": 9, "comments_connection": NodeList.from_nodes([updated])}
    )
    started, release = asyncio.Event(), asyncio.Event()

    async def discussion(number: int) -> PRDiscussion:
        started.set()
        await release.wait()
        return PRDiscussion("", [], [], [new_thread])

    async def refs(number: int) -> tuple[str, str]:
        return BASE, C if push_during_discussion else B

    monkeypatch.setattr(store._service, "get_pr_discussion", discussion)
    monkeypatch.setattr(store._service, "get_pr_refs", refs)
    refresh = asyncio.create_task(store.refresh_files())
    await started.wait()
    assert store.state.comments == [old]
    assert store.state.review_threads == [old_thread]
    release.set()
    assert await refresh is not push_during_discussion
    assert store.state.comments == ([old] if push_during_discussion else [updated])
    assert store.state.review_threads == (
        [old_thread] if push_during_discussion else [new_thread]
    )
    if not push_during_discussion:
        assert store.state.files[0].comments == [updated]


async def test_refresh_does_not_rebind_saved_line_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = loaded_store()
    stage_refresh(monkeypatch, store)
    draft = PendingReviewComment(path="a.py", line=1, body="old coordinate")
    store.state.pending_review.comments = [draft]
    assert not await store.refresh_files()
    assert store.state.pr is not None and store.state.pr.head_sha == A
    assert store.state.pending_review.comments == [draft]
    assert "line drafts" in (store.state.refresh_error or "")


async def test_files_inline_refresh_visibility_focus_editor_and_compact_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes = 0
    file_requests = 0
    remote = B
    started, release = asyncio.Event(), asyncio.Event()

    async def overview(store: PRStore) -> None:
        store.state.pr = PR(number=1, base_sha=BASE, head_sha=A, changed_files=1)

    async def sidebar(store: PRStore, **kwargs: object) -> None:
        pass

    async def summary(service: GitHubService, number: int, *, pin_revision=True) -> PR:
        started.set()
        await release.wait()
        return PR(number=1, base_sha=BASE, head_sha=remote, changed_files=1)

    async def files(
        service: GitHubService, number: int, **kwargs: object
    ) -> list[PRFile]:
        nonlocal file_requests
        file_requests += 1
        return [file("new" if kwargs.get("expected_refs") else "old")]

    async def refs(service: GitHubService, number: int) -> tuple[str, str]:
        nonlocal probes
        probes += 1
        return BASE, remote

    async def discussion(service: GitHubService, number: int) -> PRDiscussion:
        return PRDiscussion(body="", reviews=[], issue_comments=[], review_threads=[])

    monkeypatch.setattr(GitHubService, "get_pr_discussion", discussion)
    monkeypatch.setattr(PRStore, "load_overview", overview)
    monkeypatch.setattr(PRStore, "load_pr_overview", sidebar)
    monkeypatch.setattr(PRStore, "load_pr_merge_status", sidebar)
    monkeypatch.setattr(GitHubService, "get_pr_summary", summary)
    monkeypatch.setattr(GitHubService, "get_pr_files", files)
    monkeypatch.setattr(GitHubService, "get_pr_refs", refs)
    app = RitApp(owner="o", repo="r", pr_number=1)
    app.TOOLTIP_DELAY = 0.01
    async with app.run_test(size=(110, 32), tooltips=True) as pilot:
        main = cast(MainScreen, app.screen)
        await wait_until(lambda: main.store.state.pr is not None, timeout=2)
        changes = main.file_changes
        changes.poll_updates()
        await pilot.pause()
        assert probes == 0
        main.switch_tab(1)
        await wait_until(lambda: changes.workspace_ready, timeout=3)
        status = changes.query_one("#files-refresh-status", Static)
        await wait_until(
            lambda: str(status.content) == "New commits available", timeout=2
        )
        changes.diff_view.focus()
        await pilot.pause()
        focused = main.focused
        changes.poll_updates()
        await pilot.pause()
        assert main.focused is focused
        assert main.store.state.selected_file == "a.py"
        await pilot.hover("#review-scope-open")
        tooltip = app.screen.query_one(Tooltip)
        await wait_until(lambda: tooltip.display, timeout=2)
        refresh_region = changes.query_one("#files-refresh", Button).region
        await pilot.hover("#files-refresh")
        await wait_until(lambda: not tooltip.display, timeout=2)
        assert changes.query_one("#files-refresh", Button).region == refresh_region
        for width in (70, 110, 150):
            await pilot.resize_terminal(width, 32)
            await pilot.pause()
            button = changes.query_one("#files-refresh", Button)
            assert button.region.right <= width
            assert button.region.height == 1
            assert status.region.right <= button.region.x
            assert changes.query_one("#review-scope-bar").region.height == 3
        await pilot.resize_terminal(110, 32)
        modal = ModalScreen()
        app.push_screen(modal)
        await pilot.pause()
        before = probes
        changes.poll_updates()
        await pilot.pause()
        assert probes == before
        app.pop_screen()
        await pilot.pause()
        main.switch_tab(0)
        await pilot.pause()
        before = probes
        changes.poll_updates()
        await pilot.press("ctrl+r")
        assert probes == before and not started.is_set()
        main.switch_tab(1)
        await wait_until(lambda: changes.diff_view.has_focus, timeout=2)
        assert await changes.diff_view.open_inline_comment_editor()
        await pilot.pause()
        await pilot.press("k", "e", "e", "p")
        await changes.diff_view._composer.suspend()
        session = changes.diff_view._composer.session
        assert session is not None and session.suspended
        changes.refresh_workspace()
        assert str(status.content) == "Save or cancel your comment first"
        assert not started.is_set()
        assert changes.diff_view._composer.session is session
        await changes.diff_view.close_inline_comment_editor()
        draft = main.store.save_pending_file_comment("keep", path="a.py")
        notifications: list[object] = []
        monkeypatch.setattr(
            app, "notify", lambda *args, **kwargs: notifications.append(args)
        )
        await pilot.click("#files-refresh")
        await started.wait()
        await wait_until(lambda: str(status.content) == "Refreshing...", timeout=2)
        assert main.store.state.pr is not None and main.store.state.pr.head_sha == A
        release.set()
        await wait_until(
            lambda: (
                main.store.state.pr is not None
                and main.store.state.pr.head_sha == B
                and changes.workspace_ready
            ),
            timeout=3,
        )
        await pilot.pause()
        assert not status.display
        assert main.store.state.selected_file == "a.py"
        assert main.store.state.pending_review.comments == [draft]
        assert file_requests == 2
        assert not notifications
        changes.diff_view.focus()
        await pilot.press("ctrl+r")
        await wait_until(
            lambda: (
                changes._refresh_worker is not None
                and changes._refresh_worker.is_finished
            ),
            timeout=2,
        )
        assert changes.diff_view.has_focus
        assert file_requests == 2
        main._files_load_requested = False
        main.store.state.files_loading = LoadingState.ERROR
        await pilot.press("ctrl+r")
        await wait_until(
            lambda: file_requests == 3 and changes.workspace_ready,
            timeout=3,
        )
        assert main._files_load_requested
        assert not notifications
