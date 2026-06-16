"""Tests for FileTree slash-search behavior."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Tree

from rit.state.models import PRFile
from rit.ui.widgets.file_tree import FileTree


@pytest.fixture
def sample_files() -> list[PRFile]:
    """Provide a small file list for tree search tests."""
    return [
        PRFile(filename="src/app.py", additions=10, deletions=2),
        PRFile(filename="src/utils/file_tree.py", additions=5, deletions=1),
        PRFile(filename="tests/test_app.py", additions=3, deletions=0),
        PRFile(filename="docs/usage.md", additions=7, deletions=4),
    ]


@pytest.mark.asyncio
async def test_slash_opens_file_search_input(sample_files: list[PRFile]) -> None:
    """Pressing `/` on the file tree should open and focus search input."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is True
        assert search.has_focus


@pytest.mark.asyncio
async def test_search_enter_selects_highlighted_result(
    sample_files: list[PRFile],
) -> None:
    """Enter in search mode should directly select the highlighted result."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        await pilot.press("u", "t", "i", "l", "s")
        await pilot.pause()

        assert file_tree.file_count == 1
        assert set(file_tree._file_nodes.keys()) == {"src/utils/file_tree.py"}

        await pilot.press("enter")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is False
        assert tree.has_focus
        assert file_tree.selected_file == "src/utils/file_tree.py"

        # Query clears after selection, so full list is shown again.
        assert file_tree.file_count == len(sample_files)


@pytest.mark.asyncio
async def test_escape_cancels_search_and_restores_list(
    sample_files: list[PRFile],
) -> None:
    """Escape should close search mode and restore the full file list."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        await pilot.press("d", "o", "c", "s")
        await pilot.pause()

        assert file_tree.file_count == 1

        await pilot.press("escape")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is False
        assert tree.has_focus
        assert file_tree.file_count == len(sample_files)


@pytest.mark.asyncio
async def test_j_and_k_type_in_search_instead_of_moving_tree(
    sample_files: list[PRFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """j/k should belong to the search input while file filtering is active."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()

        calls: list[str] = []
        monkeypatch.setattr(
            file_tree,
            "action_cursor_down",
            lambda: calls.append("down"),
        )
        monkeypatch.setattr(
            file_tree,
            "action_cursor_up",
            lambda: calls.append("up"),
        )

        await pilot.press("j", "k")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.value == "jk"
        assert calls == []


@pytest.mark.asyncio
async def test_y_copies_basename_of_current_file(sample_files: list[PRFile]) -> None:
    """Pressing `y` should copy the basename of the current file."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        tree.select_node(file_tree._file_nodes["src/utils/file_tree.py"])
        await pilot.pause()

        await pilot.press("y")
        await pilot.pause()

        assert app.clipboard == "file_tree.py"


@pytest.mark.asyncio
async def test_Y_copies_repo_relative_path_of_current_file(
    sample_files: list[PRFile],
) -> None:
    """Pressing `Y` should copy the repo-relative path of the current file."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        tree.select_node(file_tree._file_nodes["src/utils/file_tree.py"])
        await pilot.pause()

        await pilot.press("Y")
        await pilot.pause()

        assert app.clipboard == "src/utils/file_tree.py"


@pytest.mark.asyncio
async def test_g_and_G_move_file_tree_cursor_to_top_and_bottom(
    sample_files: list[PRFile],
) -> None:
    """Pressing `g`/`G` should move the file tree cursor to top/bottom."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("G")
        await pilot.pause()

        assert tree.cursor_line == tree.last_line

        await pilot.press("g")
        await pilot.pause()

        assert tree.cursor_line == 0


@pytest.mark.asyncio
async def test_refresh_preserves_focused_tree_cursor(
    sample_files: list[PRFile],
) -> None:
    """Incremental file refresh should not jump tree navigation back to selection."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        file_tree.select_file("src/app.py", emit_message=False)
        tree.focus()
        tree.move_cursor(file_tree._file_nodes["tests/test_app.py"])
        await pilot.pause()

        file_tree.refresh_files(sample_files)
        await pilot.pause()
        await pilot.pause()

        assert tree.cursor_node is file_tree._file_nodes["tests/test_app.py"]
        assert file_tree.selected_file == "src/app.py"


@pytest.mark.asyncio
async def test_search_input_keeps_y_text_instead_of_copying(
    sample_files: list[PRFile],
) -> None:
    """Typing `y` in search mode should update the input instead of copying."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        tree.select_node(file_tree._file_nodes["src/utils/file_tree.py"])
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.value == "y"
        assert app.clipboard == ""
