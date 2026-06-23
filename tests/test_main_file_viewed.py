from types import SimpleNamespace

import pytest

from rit.state.models import FileViewedState, PRFile
from rit.ui.messages import Flash
from rit.ui.screens.main import MainScreen


class CaptureFileChanges:
    def __init__(self) -> None:
        self.updated: list[str] = []

    def update_file_view_state(self, filename: str) -> None:
        self.updated.append(filename)


@pytest.mark.asyncio
async def test_sync_file_viewed_reraises_unexpected_success_flash_errors() -> None:
    file = PRFile(filename="src/app.py", viewer_viewed_state=FileViewedState.VIEWED)
    file_changes = CaptureFileChanges()
    calls: list[tuple[str, bool]] = []
    messages: list[Flash] = []

    class Store:
        state = SimpleNamespace(files=[file])

        async def set_file_viewed(self, filename: str, *, viewed: bool) -> None:
            calls.append((filename, viewed))

    class TestScreen(MainScreen):
        @property
        def file_changes(self) -> CaptureFileChanges:
            return file_changes

        def post_message(self, message: Flash) -> None:
            messages.append(message)
            if message.style == "success":
                raise RuntimeError("flash dispatch failed")

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.store = Store()

    with pytest.raises(RuntimeError, match="flash dispatch failed"):
        await screen._sync_file_viewed(
            "src/app.py",
            FileViewedState.UNVIEWED,
            FileViewedState.VIEWED,
        )

    assert calls == [("src/app.py", True)]
    assert file.viewer_viewed_state == FileViewedState.VIEWED
    assert file_changes.updated == []
    assert [(message.content, message.style) for message in messages] == [
        ("Marked Viewed", "success")
    ]
