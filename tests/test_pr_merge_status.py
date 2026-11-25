"""Merge status follows GitHub's verdict, not a local protection-policy engine."""

import asyncio
import json
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from textual.widgets import Button, Static

from rit.app import RitApp
from rit.services.github import GitHubError, GitHubService
from rit.state.models import PR, LoadingState
from rit.state.pr_overview import PRAction, PRMergeSnapshot
from rit.state.store import PRStore
from rit.ui.components.pr_overview import PRMerge
from rit.ui.screens.main import MainScreen
from tests.conftest import wait_until

_MERGE_DATA = {
    "baseRefOid": "base",
    "headRefOid": "head",
    "baseRefName": "main",
    "state": "OPEN",
    "isDraft": False,
    "mergeable": "MERGEABLE",
    "mergeStateStatus": "CLEAN",
    "reviewDecision": None,
    "isInMergeQueue": False,
    "repository": {"mergeCommitAllowed": True, "squashMergeAllowed": True},
}


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, ("Ready", ())),
        ({"isDraft": True}, ("Blocked", ("Draft",))),
        ({"mergeStateStatus": "DRAFT"}, ("Blocked", ("Draft",))),
        ({"mergeable": "CONFLICTING"}, ("Blocked", ("Conflicts with main",))),
        ({"mergeStateStatus": "DIRTY"}, ("Blocked", ("Conflicts with main",))),
        ({"mergeStateStatus": "BLOCKED"}, ("Blocked", ("Reason unavailable",))),
        (
            {"mergeStateStatus": "BLOCKED", "reviewDecision": "REVIEW_REQUIRED"},
            ("Blocked", ("Approval required",)),
        ),
        (
            {"mergeStateStatus": "BLOCKED", "reviewDecision": "CHANGES_REQUESTED"},
            ("Blocked", ("Changes requested",)),
        ),
        (
            {
                "isDraft": True,
                "mergeable": "CONFLICTING",
                "reviewDecision": "REVIEW_REQUIRED",
            },
            ("Blocked", ("Draft", "Conflicts with main", "Approval required")),
        ),
        ({"mergeStateStatus": "BEHIND"}, ("Blocked", ("Behind base",))),
        ({"mergeStateStatus": "UNSTABLE"}, ("Warning", ("Checks not passing",))),
        ({"mergeStateStatus": "HAS_HOOKS"}, ("Warning", ("Pre-receive hooks",))),
        ({"mergeStateStatus": "UNKNOWN"}, ("Checking", ())),
        ({"mergeable": "UNKNOWN"}, ("Checking", ())),
        ({"mergeStateStatus": "FUTURE_STATE"}, ("Unknown", ("Reason unavailable",))),
        ({"mergeable": "FUTURE_STATE"}, ("Unknown", ("Reason unavailable",))),
        ({"isInMergeQueue": True, "mergeStateStatus": "BLOCKED"}, ("Queued", ())),
    ],
)
def test_native_merge_verdict_and_confirmed_reasons(
    changes: dict[str, object],
    expected: tuple[str, tuple[str, ...]],
) -> None:
    assert PRMergeSnapshot.model_validate(_MERGE_DATA | changes).summary == expected


async def test_merge_api_validates_data_and_keeps_failures_out_of_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = GitHubService(owner="owner", repo="repo")
    response: dict[str, object] = {"data": {"repository": {"pullRequest": _MERGE_DATA}}}

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        request = json.loads(input_text)
        assert request["variables"] == {"owner": "owner", "repo": "repo", "number": 1}
        assert (
            "mergeCommitAllowed" in request["query"]
            and "squashMergeAllowed" in request["query"]
        )
        for field in _MERGE_DATA:
            assert field in request["query"]
        return json.dumps(response)

    monkeypatch.setattr(service, "_run_gh", runner)
    assert (await service.get_pr_merge_status(1)).summary == ("Ready", ())
    for invalid in (
        None,
        {},
        _MERGE_DATA | {"mergeable": None},
        _MERGE_DATA | {"isInMergeQueue": "false"},
    ):
        response = {"data": {"repository": {"pullRequest": invalid}}}
        with pytest.raises(GitHubError):
            await service.get_pr_merge_status(1)
    response = {
        "data": {"repository": {"pullRequest": _MERGE_DATA}},
        "errors": [{"message": "permission denied"}],
    }
    with pytest.raises(GitHubError, match="permission denied"):
        await service.get_pr_merge_status(1)


async def test_merge_loading_refresh_and_revision_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore(pr_number=1)
    store.state.pr = PR(number=1, base_sha="base", head_sha="old")
    resource = store.state.merge_status
    started, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []
    fail = False

    async def fetch(_number: int) -> PRMergeSnapshot:
        assert store.state.pr is not None
        head = store.state.pr.head_sha
        calls.append(head)
        if head == "old":
            started.set()
            await release.wait()
        if fail:
            raise GitHubError("permission denied")
        return PRMergeSnapshot.model_validate(_MERGE_DATA | {"headRefOid": head})

    monkeypatch.setattr(store._service, "get_pr_merge_status", fetch)
    monkeypatch.setattr(store, "load_pr_checks", AsyncMock())
    monkeypatch.setattr(store, "load_pr_file_summary", AsyncMock())
    old = asyncio.create_task(store.load_pr_overview())
    await asyncio.wait_for(started.wait(), timeout=2)
    store.state.pr.head_sha = "head"
    await store.load_pr_merge_status()
    release.set()
    await old
    assert (
        resource.loaded_value is not None and resource.loaded_value.head_sha == "head"
    )
    await store.load_pr_merge_status()
    assert calls == ["old", "head"]
    fail = True
    await store.load_pr_merge_status(refresh=True)
    assert resource.loading == LoadingState.ERROR and resource.loaded_value is None
    assert store.state.error is None and store.state.files == []
    fail = False
    await store.load_pr_merge_status(refresh=True)
    assert resource.loaded_value is not None

    monkeypatch.setattr(
        store._service,
        "get_pr_merge_status",
        AsyncMock(return_value=resource.loaded_value),
    )
    store.state.pr.base_sha = "new-base"
    await store.load_pr_merge_status()
    assert resource.loading == LoadingState.ERROR
    assert "PR changed" in (resource.error or "")
    fetch_mock = AsyncMock(
        side_effect=AssertionError("closed PR must not fetch merge status")
    )
    monkeypatch.setattr(store._service, "get_pr_merge_status", fetch_mock)
    for state in ("MERGED", "CLOSED"):
        store.state.pr.state = state
        await store.load_pr_merge_status(refresh=True)
    fetch_mock.assert_not_called()


async def test_merge_sidebar_refresh_reasons_and_terminal_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PRStore, "load_overview", AsyncMock())
    app = RitApp(owner="owner", repo="repo", pr_number=1)
    async with app.run_test(size=(150, 40)) as pilot:
        screen = cast(MainScreen, app.screen)
        store = screen.store
        store.state.pr = PR(number=1, title="Review", base_sha="base", head_sha="head")
        screen._pr_overview_refs = ("base", "head")
        resource = store.state.merge_status
        resource.value = PRMergeSnapshot.model_validate(_MERGE_DATA)
        resource.loading = LoadingState.LOADED
        screen.pr_info.refresh_pr_data()
        screen.pr_info.refresh_overview()
        section = screen.query_one(PRMerge)
        status = screen.query_one("#pr-merge-status", Static)
        button = screen.query_one("#refresh-merge", Button)
        await pilot.pause()
        assert "Ready" in str(status.content)
        assert section.region.bottom <= screen.query_one("#pr-checks-section").region.y
        assert section.query_one("#apply-merge", Button).disabled
        assert section.query_one("#close-pr", Button).disabled
        assert not screen.query("#pr-status")
        store.state.pr.head_sha = "new-head"
        screen.pr_info.refresh_overview()
        assert "Checking" in str(status.content) and "Ready" not in str(status.content)
        store.state.pr.head_sha = "head"

        started, release = asyncio.Event(), asyncio.Event()

        async def fetch(_number: int) -> PRMergeSnapshot:
            started.set()
            await release.wait()
            return PRMergeSnapshot.model_validate(
                _MERGE_DATA
                | {
                    "isDraft": True,
                    "mergeable": "CONFLICTING",
                    "reviewDecision": "REVIEW_REQUIRED",
                }
            )

        monkeypatch.setattr(
            store._service, "get_pr_summary", AsyncMock(return_value=store.state.pr)
        )
        monkeypatch.setattr(store._service, "get_pr_merge_status", fetch)
        await pilot.click("#refresh-merge")
        await asyncio.wait_for(started.wait(), timeout=2)
        await wait_until(lambda: button.disabled)
        assert "Checking" in str(status.content) and "Ready" not in str(status.content)
        release.set()
        await wait_until(lambda: "Approval required" in str(status.content))
        assert not button.disabled
        for text in ("Blocked", "Draft", "Conflicts with main", "Approval required"):
            assert text in str(status.content)
        for width in (100, 40):
            await pilot.resize_terminal(width, 30)
            await pilot.pause()
            assert status.region.width <= section.content_region.width
            assert status.size.height >= 4
        monkeypatch.setattr(
            store._service,
            "get_pr_merge_status",
            AsyncMock(return_value=PRMergeSnapshot.model_validate(_MERGE_DATA)),
        )
        store._post_message(store.ThreadResolved("thread", 1, True))
        await wait_until(lambda: "Ready" in str(status.content))
        resource.loading = LoadingState.ERROR
        resource.error = "permission denied"
        screen.pr_info.refresh_overview()
        assert "Unknown" in str(status.content) and "Reason unavailable" in str(
            status.content
        )
        assert "permission denied" in str(status.tooltip)
        for state in ("MERGED", "CLOSED", "OPEN"):
            store.state.pr.state = state
            screen.pr_info.refresh_summary()
            assert section.display == (state == "OPEN")


@pytest.mark.parametrize("action", list(PRAction))
async def test_pr_mutation_requires_confirmed_target_state(
    action: PRAction, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = GitHubService()
    close = action is PRAction.CLOSE
    name = "closePullRequest" if close else "mergePullRequest"
    state = "CLOSED" if close else "MERGED"
    result = {"data": {name: {"pullRequest": {"id": "pr-id", "state": state}}}}
    runner = AsyncMock(return_value=json.dumps(result))
    monkeypatch.setattr(service, "_run_gh", runner)
    assert await service.apply_pr_action("pr-id", action, head_sha="head") == state
    call = runner.await_args_list[0]
    assert call.args == (["api", "graphql", "--input", "-"],)
    request = json.loads(call.kwargs["input_text"])
    payload = {"pullRequestId": "pr-id"}
    if not close:
        payload.update(mergeMethod=action.value, expectedHeadOid="head")
    assert name in request["query"] and request["variables"] == {"input": payload}
    for invalid in (
        {"errors": [{"message": "Not permitted"}]},
        {"data": {name: {"pullRequest": {"id": "wrong", "state": state}}}},
        {"data": {name: {"pullRequest": {"id": "pr-id", "state": "OPEN"}}}},
    ):
        runner.return_value = json.dumps(invalid)
        with pytest.raises(GitHubError):
            await service.apply_pr_action("pr-id", action, head_sha="head")


async def test_action_guards_and_failure_leave_pr_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore(pr_number=1)
    pr = PR(number=1, node_id="id", base_sha="base", head_sha="head")
    store.state.pr = pr
    resource = store.state.merge_status
    resource.loading = LoadingState.LOADED
    resource.value = PRMergeSnapshot.model_validate(
        _MERGE_DATA | {"repository": {"squashMergeAllowed": True}}
    )
    write = AsyncMock(side_effect=GitHubError("Not permitted"))
    monkeypatch.setattr(store._service, "apply_pr_action", write)
    assert store.can_apply_pr_action(PRAction.SQUASH)
    assert not store.can_apply_pr_action(PRAction.MERGE)
    with pytest.raises(GitHubError, match="refresh"):
        await store.apply_pr_action(
            pr.model_copy(update={"head_sha": "old"}), PRAction.SQUASH
        )
    resource.value = resource.value.model_copy(update={"is_draft": True})
    with pytest.raises(GitHubError, match="unavailable"):
        await store.apply_pr_action(pr, PRAction.SQUASH)
    write.assert_not_awaited()
    assert store.can_apply_pr_action(PRAction.CLOSE)
    with pytest.raises(GitHubError, match="Not permitted"):
        await store.apply_pr_action(pr, PRAction.CLOSE)
    assert store.state.pr.state == "OPEN" and not store.state.pr_action_pending
    write.side_effect = None
    write.return_value = "CLOSED"
    await store.apply_pr_action(pr, PRAction.CLOSE)
    assert store.state.pr.state == "CLOSED" and not store.can_apply_pr_action(
        PRAction.CLOSE
    )


async def test_sidebar_actions_require_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PRStore, "load_overview", AsyncMock())
    monkeypatch.setattr(PRStore, "check_for_updates", AsyncMock())
    app = RitApp(owner="owner", repo="repo", pr_number=1)
    refresh = Mock()
    monkeypatch.setattr(app, "refresh_pr_list", refresh)
    async with app.run_test(size=(150, 40)) as pilot:
        screen = cast(MainScreen, app.screen)
        store = screen.store
        pr = PR(
            number=1,
            node_id="id",
            head_sha="head",
            base_sha="base",
            head_ref="feature",
            base_ref="main",
        )
        store.state.pr = pr
        screen._pr_overview_refs = ("base", "head")
        store.state.merge_status.value = PRMergeSnapshot.model_validate(_MERGE_DATA)
        store.state.merge_status.loading = LoadingState.LOADED
        screen.pr_info.refresh_pr_data()
        release = asyncio.Event()

        async def apply(node_id: str, action: PRAction, *, head_sha: str) -> str:
            assert (node_id, action, head_sha) == ("id", PRAction.SQUASH, "head")
            await release.wait()
            return "MERGED"

        write = AsyncMock(side_effect=apply)
        monkeypatch.setattr(store._service, "apply_pr_action", write)
        screen.query_one("#close-pr", Button).press()
        await wait_until(lambda: app.focused and app.focused.id == "pr-action-cancel")
        await pilot.press("enter")
        await wait_until(lambda: app.screen is screen)
        await pilot.click("#apply-merge")
        await wait_until(lambda: app.focused and app.focused.id == "pr-action-cancel")
        assert (
            str(app.screen.query_one("#pr-action-confirm", Button).label)
            == PRAction.MERGE.label
        )
        await pilot.press("escape")
        await wait_until(lambda: app.screen is screen)
        write.assert_not_awaited()
        merge = screen.query_one("#apply-squash", Button)
        merge.focus()
        await pilot.press("enter")
        await wait_until(lambda: app.focused and app.focused.id == "pr-action-cancel")
        assert (
            str(app.screen.query_one("#pr-action-confirm", Button).label)
            == PRAction.SQUASH.label
        )
        write.assert_not_awaited()
        await pilot.press("tab", "enter")
        await wait_until(lambda: store.state.pr_action_pending and merge.disabled)
        assert screen.query_one("#close-pr", Button).disabled
        with pytest.raises(GitHubError):
            await store.apply_pr_action(pr, PRAction.CLOSE)
        assert pr.state == "OPEN"
        release.set()
        await wait_until(lambda: screen.header.pr_status == "Merged")
        assert not screen.query_one(PRMerge).display
        write.assert_awaited_once()
        refresh.assert_called_once()
