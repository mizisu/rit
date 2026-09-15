import asyncio

import pytest

from rit.state.models import PR, FileViewedState, PRFile
from rit.state.store import GitHubError, PRStore, PRStoreState, ViewedFiles


class CaptureViewedStateService:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str]] = []
        self.unmarked: list[tuple[str, str]] = []

    async def mark_file_as_viewed(self, pull_request_id: str, path: str) -> None:
        self.marked.append((pull_request_id, path))

    async def unmark_file_as_viewed(self, pull_request_id: str, path: str) -> None:
        self.unmarked.append((pull_request_id, path))


@pytest.mark.asyncio
async def test_set_file_viewed_updates_local_file_state_after_marking() -> None:
    store = PRStore(pr_number=123)
    service = CaptureViewedStateService()
    file = PRFile(filename="src/app.py")
    store._service = service  # type: ignore[assignment]
    store.state.pr = PR(node_id="PR_node", number=123)
    store.state.files = [file]
    store.state.files_by_filename = {file.filename: file}

    await store.set_file_viewed("src/app.py", viewed=True)

    assert service.marked == [("PR_node", "src/app.py")]
    assert service.unmarked == []
    assert file.viewer_viewed_state == FileViewedState.VIEWED


@pytest.mark.asyncio
async def test_set_file_viewed_updates_local_file_state_after_unmarking() -> None:
    store = PRStore(pr_number=123)
    service = CaptureViewedStateService()
    file = PRFile(
        filename="src/app.py",
        viewer_viewed_state=FileViewedState.VIEWED,
    )
    store._service = service  # type: ignore[assignment]
    store.state.pr = PR(node_id="PR_node", number=123)
    store.state.files = [file]
    store.state.files_by_filename = {file.filename: file}

    await store.set_file_viewed("src/app.py", viewed=False)

    assert service.marked == []
    assert service.unmarked == [("PR_node", "src/app.py")]
    assert file.viewer_viewed_state == FileViewedState.UNVIEWED


@pytest.mark.asyncio
async def test_current_viewed_failure_rolls_back_confirmed_state() -> None:
    file = PRFile(filename="src/app.py")
    updates: list[str] = []

    async def fail(_filename: str, _viewed: bool) -> bool:
        raise GitHubError("failed")

    viewed = ViewedFiles(PRStoreState(files=[file]), fail)
    assert viewed.toggle(file.filename)
    assert file.viewer_viewed_state == FileViewedState.VIEWED
    with pytest.raises(GitHubError, match="failed"):
        await viewed.sync(file.filename, updates.append)

    assert file.viewer_viewed_state == FileViewedState.UNVIEWED
    assert updates == [file.filename]
    assert viewed.toggle(file.filename)


@pytest.mark.asyncio
@pytest.mark.parametrize("newer_toggles", [1, 2])
async def test_rapid_viewed_toggles_retry_latest_intent_after_stale_failure(
    newer_toggles: int,
) -> None:
    file = PRFile(filename="src/app.py")
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[bool] = []
    updates: list[str] = []

    async def persist(_filename: str, state: bool) -> bool:
        calls.append(state)
        if len(calls) == 1:
            started.set()
            await release.wait()
            raise GitHubError("delayed failure")
        return True

    viewed = ViewedFiles(PRStoreState(files=[file]), persist)
    assert viewed.toggle(file.filename)
    task = asyncio.create_task(viewed.sync(file.filename, updates.append))
    await asyncio.wait_for(started.wait(), timeout=1)
    desired = FileViewedState.VIEWED
    for _ in range(newer_toggles):
        desired = (
            FileViewedState.UNVIEWED
            if desired == FileViewedState.VIEWED
            else FileViewedState.VIEWED
        )
        assert not viewed.toggle(file.filename)
    assert file.viewer_viewed_state == desired

    release.set()
    assert await asyncio.wait_for(task, timeout=1) == desired
    assert calls == [True, desired == FileViewedState.VIEWED]
    assert file.viewer_viewed_state == desired
    assert updates == []
    assert viewed.toggle(file.filename)


@pytest.mark.asyncio
async def test_viewed_files_reconcile_independently() -> None:
    first = PRFile(filename="one.py")
    second = PRFile(filename="two.py")
    started = asyncio.Event()
    release = asyncio.Event()

    async def persist(filename: str, _viewed: bool) -> bool:
        if filename == first.filename:
            started.set()
            await release.wait()
        return True

    viewed = ViewedFiles(PRStoreState(files=[first, second]), persist)
    updates: list[str] = []
    assert viewed.toggle(first.filename)
    task = asyncio.create_task(viewed.sync(first.filename, updates.append))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert viewed.toggle(second.filename)
    assert (
        await asyncio.wait_for(viewed.sync(second.filename, updates.append), timeout=1)
        == FileViewedState.VIEWED
    )
    assert not task.done()
    release.set()
    assert await asyncio.wait_for(task, timeout=1) == FileViewedState.VIEWED


@pytest.mark.asyncio
async def test_failed_viewed_feedback_does_not_claim_a_sync_worker() -> None:
    file = PRFile(filename="one.py")
    calls: list[bool] = []

    async def persist(_filename: str, state: bool) -> bool:
        calls.append(state)
        return True

    def fail_feedback(_filename: str, _state: FileViewedState) -> None:
        raise RuntimeError("paint failed")

    viewed = ViewedFiles(PRStoreState(files=[file]), persist)
    with pytest.raises(RuntimeError, match="paint failed"):
        viewed.toggle(file.filename, fail_feedback)
    assert file.viewer_viewed_state == FileViewedState.VIEWED
    assert viewed.toggle(file.filename)
    assert await viewed.sync(file.filename, lambda _filename: None) is None
    assert file.viewer_viewed_state == FileViewedState.UNVIEWED
    assert calls == []
