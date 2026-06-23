import pytest

from rit.state.models import FileViewedState, PR, PRFile
from rit.state.store import PRStore


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
    store.state.pr = PR(id="PR_node", number=123)
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
    store.state.pr = PR(id="PR_node", number=123)
    store.state.files = [file]
    store.state.files_by_filename = {file.filename: file}

    await store.set_file_viewed("src/app.py", viewed=False)

    assert service.marked == []
    assert service.unmarked == [("PR_node", "src/app.py")]
    assert file.viewer_viewed_state == FileViewedState.UNVIEWED
