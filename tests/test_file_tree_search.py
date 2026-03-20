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

    class TestApp(App):
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

    class TestApp(App):
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
async def test_search_allows_arrow_navigation_then_enter_selects_current_item(
    sample_files: list[PRFile],
) -> None:
    """Up/Down in search mode should move tree cursor and Enter selects that item."""

    class TestApp(App):
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
        await pilot.press(".", "p", "y")
        await pilot.pause()

        target = "src/utils/file_tree.py"
        for _ in range(8):
            if tree.cursor_node and tree.cursor_node.data == target:
                break
            await pilot.press("down")
            await pilot.pause()

        assert tree.cursor_node is not None
        assert tree.cursor_node.data == target

        await pilot.press("enter")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is False
        assert tree.has_focus
        assert file_tree.selected_file == target


@pytest.mark.asyncio
async def test_search_enter_on_directory_toggles_expand_state(
    sample_files: list[PRFile],
) -> None:
    """Enter on a directory cursor should toggle fold state, not open a file."""

    class TestApp(App):
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
        await pilot.press("s", "r", "c")
        await pilot.pause()

        src_dir = tree.root.children[0]
        assert src_dir.allow_expand
        tree.move_cursor(src_dir)
        await pilot.pause()

        assert src_dir.is_expanded is True

        await pilot.press("enter")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is True
        assert src_dir.is_expanded is False
        assert file_tree.selected_file is None


@pytest.mark.asyncio
async def test_search_enter_with_no_cursor_does_not_auto_select_first_match(
    sample_files: list[PRFile],
) -> None:
    """Enter without a tree cursor should not auto-open the first filtered file."""

    class TestApp(App):
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
        await pilot.press(".", "p", "y")
        await pilot.pause()

        tree.move_cursor(None)
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is True
        assert search.has_focus
        assert file_tree.selected_file is None


@pytest.mark.asyncio
async def test_escape_cancels_search_and_restores_list(sample_files: list[PRFile]) -> None:
    """Escape should close search mode and restore the full file list."""

    class TestApp(App):
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
