import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from rit.app import RitApp
from rit.core.diff import parse_patch
from rit.core.types import FileDiff
from rit.state.models import PR, FileViewedState, LoadingState, PRFile
from rit.state.store import PRStore
from rit.ui.messages import Flash
from rit.ui.screens.main import MainScreen
from tests.conftest import wait_until


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
async def test_toggle_targets_new_file_when_focus_moves_during_viewed_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def skip_initial_load(_store: PRStore) -> None:
        return None

    monkeypatch.setattr(PRStore, "load_all", skip_initial_load)

    patch = "@@ -1 +1 @@\n-old\n+new"
    files = [
        PRFile(filename="one.py", status="modified", patch=patch),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]
    app = RitApp(owner="test", repo="repo", pr_number=123)

    async with app.run_test(size=(120, 30)) as pilot:
        screen = app.screen
        assert isinstance(screen, MainScreen)
        store = screen.store
        store.state.pr = PR(number=123, node_id="PR_123")
        store.state.files_loading = LoadingState.LOADED
        store.state.files = files
        store.state.file_diffs = {
            file.filename: parse_patch(patch, file.filename) for file in files
        }

        async def set_file_viewed(_filename: str, *, viewed: bool) -> None:
            assert isinstance(viewed, bool)

        monkeypatch.setattr(store, "set_file_viewed", set_file_viewed)

        screen.switch_tab(1)
        screen.file_changes.refresh_files()
        diff_view = screen.file_changes.diff_view
        await wait_until(
            lambda: diff_view.current_file == "All files",
            timeout=2.0,
        )
        await pilot.pause()

        first_line = diff_view.file_start_line_index("one.py")
        assert first_line is not None
        diff_view.jump_to_line_index(first_line, side="RIGHT", focus=True)

        refresh_started = asyncio.Event()
        continue_refresh = asyncio.Event()
        original_show_diff = diff_view.show_diff

        async def blocked_show_diff(
            filename: str,
            diff: FileDiff,
            *,
            preserve_full_file_state: bool = False,
        ) -> None:
            refresh_started.set()
            await continue_refresh.wait()
            await original_show_diff(
                filename,
                diff,
                preserve_full_file_state=preserve_full_file_state,
            )

        monkeypatch.setattr(diff_view, "show_diff", blocked_show_diff)

        screen.action_toggle_file_viewed()
        await asyncio.wait_for(refresh_started.wait(), timeout=2.0)
        refresh_worker = next(
            worker
            for worker in diff_view.workers
            if worker.name == "diff-viewed-fold-refresh"
        )

        second_line = diff_view.file_start_line_index("two.py")
        assert second_line is not None
        diff_view.jump_to_line_index(second_line, side="RIGHT", focus=True)
        assert screen.file_changes.current_diff_file_target() == "two.py"

        continue_refresh.set()
        await refresh_worker.wait()
        assert screen.file_changes.current_diff_file_target() == "two.py"

        screen.action_toggle_file_viewed()

        assert files[0].viewer_viewed_state == FileViewedState.VIEWED
        assert files[1].viewer_viewed_state == FileViewedState.VIEWED


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
