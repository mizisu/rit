from types import SimpleNamespace
from typing import Any, cast

import pytest

from rit.state.models import FileViewedState, PRFile
from rit.ui.messages import Flash
from rit.ui.screens.main import MainScreen


class CaptureFileChanges:
    def __init__(self) -> None:
        self.updated: list[str] = []

    def update_file_view_state(self, filename: str) -> None:
        self.updated.append(filename)


def test_toggle_file_viewed_uses_combined_diff_cursor_file() -> None:
    file = PRFile(filename="one.py")
    updates: list[str] = []
    collapsed: list[str] = []

    class DiffView:
        has_focus = True
        current_file = "All files"

        def collapse_viewed_file(self, filename: str) -> None:
            collapsed.append(filename)

    class FileChanges:
        diff_view = DiffView()

        def current_diff_file_target(self) -> str:
            return "one.py"

        def update_file_view_state(self, filename: str) -> None:
            updates.append(filename)

    class TestScreen(MainScreen):
        @property
        def file_changes(self) -> FileChanges:
            return FileChanges()

        def run_worker(self, coro, *args: object, **kwargs: object) -> None:
            coro.close()

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.current_tab = 1
    screen.store = cast(
        Any,
        SimpleNamespace(
            state=SimpleNamespace(files=[file], pr=object(), selected_file="two.py")
        ),
    )

    screen.action_toggle_file_viewed()

    assert file.viewer_viewed_state == FileViewedState.VIEWED
    assert collapsed == ["one.py"]
    assert updates == ["one.py"]


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
    screen.store = cast(Any, Store())

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
