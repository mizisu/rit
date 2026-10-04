import asyncio
from types import SimpleNamespace

import pytest
from textual.widgets import Tree

from rit.app import RitApp
from rit.core.diff import parse_patch
from rit.core.types import FileDiff
from rit.state.models import PR, FileViewedState, LoadingState, PRFile
from rit.state.store import GitHubError, PRStore
from rit.ui.messages import Flash
from rit.ui.screens.main import MainScreen
from tests.conftest import wait_until


class CaptureFileChanges:
    def __init__(self) -> None:
        self.updated: list[str] = []

    def update_file_view_state(self, filename: str) -> None:
        self.updated.append(filename)


@pytest.mark.parametrize("state", list(FileViewedState))
@pytest.mark.parametrize("diff_focused", [False, True])
def test_toggle_file_viewed_advances_only_when_marking_in_diff(
    state: FileViewedState,
    diff_focused: bool,
) -> None:
    file = PRFile(filename="one.py", viewer_viewed_state=state)
    updates: list[str] = []
    collapsed: list[str] = []
    advanced: list[str] = []

    class DiffView:
        has_focus = diff_focused
        current_file = "All files"

        def collapse_viewed_file(self, filename: str) -> None:
            collapsed.append(filename)

    class FileChanges:
        diff_view = DiffView()
        file_tree = SimpleNamespace(
            query_one=lambda *_args: SimpleNamespace(
                has_focus=True, cursor_node=SimpleNamespace(data="one.py")
            )
        )

        def select_file_after(self, filename: str) -> None:
            advanced.append(filename)

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
    screen.store.state.files = [file]
    screen.store.state.pr = PR(number=123, node_id="PR_123")
    screen.store.state.selected_file = "two.py"

    screen.action_toggle_file_viewed()

    marking = state != FileViewedState.VIEWED
    assert file.viewer_viewed_state == (
        FileViewedState.VIEWED if marking else FileViewedState.UNVIEWED
    )
    assert collapsed == (["one.py"] if marking else [])
    assert updates == ["one.py"]
    assert advanced == (["one.py"] if marking and diff_focused else [])


@pytest.mark.asyncio
async def test_toggle_targets_new_file_when_focus_moves_during_viewed_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def skip_initial_load(_store: PRStore) -> None:
        return None

    monkeypatch.setattr(PRStore, "load_overview", skip_initial_load)

    patch = "@@ -1 +1 @@\n-old\n+new"
    files = [
        PRFile(filename=name, status="modified", patch=patch)
        for name in ("one.py", "two.py", "three.py", "four.py")
    ]
    files[1].viewer_viewed_state = FileViewedState.VIEWED
    files[3].viewer_viewed_state = FileViewedState.VIEWED
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

        fail_first = asyncio.Event()

        async def mark_file_as_viewed(_pr_id: str, filename: str) -> None:
            if filename == "one.py":
                await fail_first.wait()
                raise GitHubError("failed")

        async def unmark_file_as_viewed(_pr_id: str, _filename: str) -> None:
            return None

        monkeypatch.setattr(store._service, "mark_file_as_viewed", mark_file_as_viewed)
        monkeypatch.setattr(
            store._service, "unmark_file_as_viewed", unmark_file_as_viewed
        )

        diff_view = screen.file_changes.diff_view
        diff_view._manually_folded_files.add("three.py")
        diff_view._expanded_viewed_files.add("four.py")
        screen.switch_tab(1)
        screen.file_changes.refresh_files()
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
            _fold_refresh: bool = False,
        ) -> None:
            refresh_started.set()
            await continue_refresh.wait()
            await original_show_diff(
                filename,
                diff,
                preserve_full_file_state=preserve_full_file_state,
                _fold_refresh=_fold_refresh,
            )

        monkeypatch.setattr(diff_view, "show_diff", blocked_show_diff)

        screen.action_toggle_file_viewed()
        assert screen.file_changes.current_diff_file_target() == "two.py"
        assert diff_view.selected_file_header_path() == "two.py"
        assert screen.file_changes.file_tree.selected_file == "two.py"
        assert "two.py" in diff_view._folded_file_paths
        assert diff_view.scroll_y == diff_view._hunk_header_top_offsets[1]
        assert diff_view.has_focus
        await asyncio.wait_for(refresh_started.wait(), timeout=2.0)
        refresh_worker = next(
            worker
            for worker in diff_view.workers
            if worker.name == "diff-viewed-fold-refresh"
        )

        screen.file_changes.open_file("three.py", focus_diff=True, expand=False)
        assert diff_view.selected_file_header_path() == "three.py"
        assert "three.py" in diff_view._folded_file_paths

        continue_refresh.set()
        await refresh_worker.wait()
        assert screen.file_changes.current_diff_file_target() == "three.py"

        screen.action_toggle_file_viewed()
        assert files[2].viewer_viewed_state == FileViewedState.VIEWED
        assert screen.file_changes.current_diff_file_target() == "four.py"
        assert "four.py" not in diff_view._folded_file_paths
        assert "four.py" in diff_view._expanded_viewed_files
        assert diff_view.scroll_y == diff_view._hunk_header_top_offsets[3]

        screen.action_toggle_file_viewed()
        assert files[3].viewer_viewed_state == FileViewedState.UNVIEWED
        assert screen.file_changes.current_diff_file_target() == "four.py"
        screen.action_toggle_file_viewed()
        assert files[3].viewer_viewed_state == FileViewedState.VIEWED
        assert screen.file_changes.current_diff_file_target() == "four.py"
        await wait_until(lambda: not diff_view._fold_worker_active)
        assert diff_view.selected_file_header_path() == "four.py"
        assert diff_view.scroll_y == diff_view._hunk_header_top_offsets[3]

        fail_first.set()
        await wait_until(
            lambda: (
                files[0].viewer_viewed_state == FileViewedState.UNVIEWED
                and not diff_view._fold_worker_active
            )
        )
        assert screen.file_changes.current_diff_file_target() == "four.py"
        assert diff_view.scroll_y == diff_view._hunk_header_top_offsets[3]

        file_tree = screen.file_changes.file_tree
        file_tree.select_file("two.py", emit_message=False)
        file_tree.query_one("#file-tree", Tree).focus()
        await pilot.pause()
        screen.action_toggle_file_viewed()
        assert files[1].viewer_viewed_state == FileViewedState.UNVIEWED
        assert screen.file_changes.current_diff_file_target() == "four.py"
        assert file_tree.query_one("#file-tree", Tree).has_focus


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_sync", [False, True])
async def test_sync_file_viewed_reraises_unexpected_flash_errors(
    fail_sync: bool,
) -> None:
    file = PRFile(filename="src/app.py")
    file_changes = CaptureFileChanges()
    calls: list[tuple[str, bool]] = []
    messages: list[Flash] = []

    class Store(PRStore):
        async def _persist_file_viewed(self, filename: str, viewed: bool) -> bool:
            calls.append((filename, viewed))
            if fail_sync:
                raise GitHubError("failed")
            return True

    class TestScreen(MainScreen):
        @property
        def file_changes(self) -> CaptureFileChanges:
            return file_changes

        def post_message(self, message: Flash) -> None:
            messages.append(message)
            raise RuntimeError("flash dispatch failed")

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.store = Store()
    screen.store.state.files = [file]
    assert screen.store.viewed_files.toggle(file.filename)

    with pytest.raises(RuntimeError, match="flash dispatch failed"):
        await screen._sync_file_viewed(file.filename)

    assert calls == [(file.filename, True)]
    assert file.viewer_viewed_state == (
        FileViewedState.UNVIEWED if fail_sync else FileViewedState.VIEWED
    )
    assert file_changes.updated == ([file.filename] if fail_sync else [])
    assert [(message.content, message.style) for message in messages] == [
        ("Failed to update viewed state", "error")
        if fail_sync
        else ("Marked Viewed", "success")
    ]
