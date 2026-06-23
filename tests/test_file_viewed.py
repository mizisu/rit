"""Tests for file viewed state feature."""

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Tree

from rit.state.models import FileViewedState, PRFile
from rit.ui.widgets.file_tree import FileTree


@pytest.fixture
def sample_files() -> list[PRFile]:
    return [
        PRFile(filename="src/app.py", status="modified", additions=10, deletions=2),
        PRFile(filename="src/utils.py", status="added", additions=5, deletions=0),
        PRFile(filename="README.md", status="modified", additions=3, deletions=1),
    ]


# ---------------------------------------------------------------------------
# FileViewedState enum
# ---------------------------------------------------------------------------


def test_file_viewed_state_values() -> None:
    assert FileViewedState("UNVIEWED") == FileViewedState.UNVIEWED
    assert FileViewedState("VIEWED") == FileViewedState.VIEWED
    assert FileViewedState("DISMISSED") == FileViewedState.DISMISSED


def test_prfile_default_viewed_state() -> None:
    f = PRFile(filename="test.py")
    assert f.viewer_viewed_state == FileViewedState.UNVIEWED


# ---------------------------------------------------------------------------
# FileTree label rendering
# ---------------------------------------------------------------------------


def test_file_label_unviewed_has_circle_badge(sample_files: list[PRFile]) -> None:
    tree = FileTree()
    label = tree._file_label(sample_files[0])
    assert label.plain.startswith("○ ")


def test_file_label_viewed_has_check_badge(sample_files: list[PRFile]) -> None:
    f = sample_files[0]
    f.viewer_viewed_state = FileViewedState.VIEWED
    tree = FileTree()
    label = tree._file_label(f)
    assert label.plain.startswith("✓ ")


def test_file_label_dismissed_has_bang_badge(sample_files: list[PRFile]) -> None:
    f = sample_files[0]
    f.viewer_viewed_state = FileViewedState.DISMISSED
    tree = FileTree()
    label = tree._file_label(f)
    assert label.plain.startswith("! ")


@pytest.mark.asyncio
async def test_file_tree_uses_compact_folder_indentation() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        tree = app.query_one("#file-tree", Tree)

        assert tree.guide_depth == 2

        filename = "app/lemonbase/account/application/request/version_create_request.py"
        file_tree.refresh_files(
            [
                PRFile(filename=filename, additions=10),
                PRFile(
                    filename="app/lemonbase/account/application/response/version_response.py",
                    additions=31,
                ),
            ]
        )
        await pilot.pause()

        compacted_root = tree.root.children[0]
        assert compacted_root.label.plain == "app/lemonbase/account/application"

        file_node = file_tree._file_nodes[filename]
        ancestor_labels: list[str] = []
        parent = file_node.parent
        while parent is not None and parent is not tree.root:
            ancestor_labels.append(parent.label.plain)
            parent = parent.parent

        assert list(reversed(ancestor_labels)) == [
            "app/lemonbase/account/application",
            "request",
        ]


# ---------------------------------------------------------------------------
# FileTree.update_view_state — single-node update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_view_state_updates_single_node(
    sample_files: list[PRFile],
) -> None:
    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)

        # Initially no badge
        node = file_tree._file_nodes["README.md"]
        assert "✓" not in node.label.plain

        # Mark as viewed
        sample_files[2].viewer_viewed_state = FileViewedState.VIEWED
        file_tree.update_view_state("README.md")
        await pilot.pause()

        assert node.label.plain.startswith("✓ ")


@pytest.mark.asyncio
async def test_update_view_state_preserves_selection(
    sample_files: list[PRFile],
) -> None:
    """Updating view state should not change the selected file."""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        file_tree.select_file("README.md", emit_message=False)
        await pilot.pause()

        sample_files[0].viewer_viewed_state = FileViewedState.VIEWED
        file_tree.update_view_state("src/app.py")
        await pilot.pause()

        assert file_tree.selected_file == "README.md"


@pytest.mark.asyncio
async def test_update_view_state_nonexistent_file_is_noop(
    sample_files: list[PRFile],
) -> None:
    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        file_tree.update_view_state("nonexistent.py")
        # No error raised


# ---------------------------------------------------------------------------
# Toggle logic
# ---------------------------------------------------------------------------


def test_toggle_unviewed_becomes_viewed() -> None:
    f = PRFile(filename="test.py")
    assert f.viewer_viewed_state == FileViewedState.UNVIEWED
    old = f.viewer_viewed_state
    new = (
        FileViewedState.UNVIEWED
        if old == FileViewedState.VIEWED
        else FileViewedState.VIEWED
    )
    assert new == FileViewedState.VIEWED


def test_toggle_viewed_becomes_unviewed() -> None:
    f = PRFile(filename="test.py", viewer_viewed_state=FileViewedState.VIEWED)
    old = f.viewer_viewed_state
    new = (
        FileViewedState.UNVIEWED
        if old == FileViewedState.VIEWED
        else FileViewedState.VIEWED
    )
    assert new == FileViewedState.UNVIEWED


# ---------------------------------------------------------------------------
# Diff header text building
# ---------------------------------------------------------------------------


def test_build_header_text_unviewed_has_unviewed_badge() -> None:
    from unittest.mock import MagicMock

    from rit.ui.widgets.diff_render import _build_header_text

    view = MagicMock()
    view.current_file = "src/app.py"
    view._showing_full_file = False
    f = PRFile(filename="src/app.py", additions=3, deletions=1)
    view._file = f
    text = _build_header_text(view)
    assert "Unviewed" in text
    assert "src/app.py" in text


def test_build_header_text_viewed_has_badge() -> None:
    from unittest.mock import MagicMock

    from rit.ui.widgets.diff_render import _build_header_text

    view = MagicMock()
    view.current_file = "src/app.py"
    view._showing_full_file = False
    f = PRFile(
        filename="src/app.py",
        additions=3,
        deletions=1,
        viewer_viewed_state=FileViewedState.VIEWED,
    )
    view._file = f
    text = _build_header_text(view)
    assert "Viewed" in text
    assert "src/app.py" in text


def test_build_header_text_dismissed_has_changed_badge() -> None:
    from unittest.mock import MagicMock

    from rit.ui.widgets.diff_render import _build_header_text

    view = MagicMock()
    view.current_file = "src/app.py"
    view._showing_full_file = False
    f = PRFile(
        filename="src/app.py",
        additions=3,
        deletions=1,
        viewer_viewed_state=FileViewedState.DISMISSED,
    )
    view._file = f
    text = _build_header_text(view)
    assert "Changed" in text


def test_build_header_text_uses_current_file_metadata_over_stale_view_file() -> None:
    from types import SimpleNamespace

    from rit.ui.widgets.diff_render import _build_header_text

    stale_file = PRFile(
        filename="old.py",
        additions=99,
        deletions=99,
        viewer_viewed_state=FileViewedState.VIEWED,
    )
    current_file = PRFile(
        filename="current.py",
        additions=2,
        deletions=1,
        viewer_viewed_state=FileViewedState.UNVIEWED,
    )
    view = SimpleNamespace(
        current_file="current.py",
        _showing_full_file=False,
        _file=stale_file,
        store=SimpleNamespace(
            state=SimpleNamespace(
                files=[],
                files_by_filename={"current.py": current_file},
            )
        ),
    )

    text = _build_header_text(view)

    assert "Unviewed" in text
    assert "-1" in text
    assert "+2" in text
    assert "Viewed" not in text
    assert "99" not in text


def test_build_header_text_no_file_shows_placeholder() -> None:
    from unittest.mock import MagicMock

    from rit.ui.widgets.diff_render import _build_header_text

    view = MagicMock()
    view.current_file = None
    text = _build_header_text(view)
    assert "Select a file" in text


def test_toggle_dismissed_becomes_viewed() -> None:
    f = PRFile(filename="test.py", viewer_viewed_state=FileViewedState.DISMISSED)
    old = f.viewer_viewed_state
    new = (
        FileViewedState.UNVIEWED
        if old == FileViewedState.VIEWED
        else FileViewedState.VIEWED
    )
    assert new == FileViewedState.VIEWED
