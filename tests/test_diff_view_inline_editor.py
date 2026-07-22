import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static, TextArea

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


def _combined_two_file_diff() -> FileDiff:
    return FileDiff(
        filename="All files",
        show_hunk_headers=False,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(1, 1, "one", "one")],
                starts_file=True,
                file_path="one.py",
            ),
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(1, 1, "two", "two")],
                starts_file=True,
                file_path="two.py",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_file_headers_are_cursor_targets_for_file_comments() -> None:
    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("All files", _combined_two_file_diff())
        await pilot.pause()
        diff_view.focus()

        await pilot.press("k")
        await pilot.pause()

        first_header = app.query_one("#file-header-0", Static)
        assert diff_view.selected_file_header_path() == "one.py"
        assert first_header.has_class("-selected")
        assert all(
            "-cursor" not in widget.classes for widget in diff_view._get_code_widgets(0)
        )

        assert await diff_view.open_file_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        editor = app.query_one("#diff-file-comment-editor")
        context = editor.query_one(".comment-editor-context", Static)
        first_line = app.query_one("#line-0")
        assert first_header.region.y < editor.region.y < first_line.region.y
        assert str(context.content) == "Entire file: one.py"
        assert diff_view.file_comment_target() == "one.py"
        assert diff_view._file_comment_editor_height() == (
            editor.region.height + editor.styles.margin.height
        )

        await diff_view.close_file_comment_editor()
        await pilot.pause()
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()
        assert app.screen.focused is diff_view
        await pilot.press("j")
        await pilot.pause()
        assert diff_view.selected_file_header_path() is None
        assert all(
            "-cursor" in widget.classes for widget in diff_view._get_code_widgets(0)
        )

        await pilot.press("j")
        await pilot.pause()
        second_header = app.query_one("#file-header-1", Static)
        assert diff_view.selected_file_header_path() == "two.py"
        assert second_header.has_class("-selected")
        assert diff_view.cursor_line == 1


@pytest.mark.asyncio
async def test_selecting_visible_file_header_preserves_scroll_position() -> None:
    first_lines = [
        DiffLine(line, line, f"one {line}", f"one {line}") for line in range(1, 13)
    ]
    second_lines = [
        DiffLine(line, line, f"two {line}", f"two {line}") for line in range(1, 21)
    ]
    diff = FileDiff(
        filename="All files",
        show_hunk_headers=False,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=len(first_lines),
                new_start=1,
                new_count=len(first_lines),
                lines=first_lines,
                starts_file=True,
                file_path="one.py",
            ),
            DiffHunk(
                old_start=1,
                old_count=len(second_lines),
                new_start=1,
                new_count=len(second_lines),
                lines=second_lines,
                starts_file=True,
                file_path="two.py",
            ),
        ],
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(80, 8)) as pilot:
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("All files", diff)
        await pilot.pause()
        diff_view.jump_to_line_index(11, side="RIGHT", focus=True)
        await pilot.pause()
        await pilot.pause()
        before = int(diff_view.scroll_y)

        diff_view.action_scroll_down()
        await pilot.pause()

        assert diff_view.selected_file_header_path() == "two.py"
        assert int(diff_view.scroll_y) == before


@pytest.mark.asyncio
async def test_open_inline_comment_editor_mounts_below_current_line() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        line_widget = app.query_one("#line-0")
        editor = app.query_one("#diff-inline-comment-editor")
        body = editor.query_one("#comment-editor-body", TextArea)
        context = editor.query_one(".comment-editor-context", Static)

        assert editor.region.y > line_widget.region.y
        assert body.region.height >= 5
        assert diff_view.inline_comment_target() == ("test.py", 1, "LEFT")
        assert str(context.content) == "Selected: test.py:1 (old)"


@pytest.mark.asyncio
async def test_virtualized_diff_tracks_growing_inline_editor_height() -> None:
    patch = "@@ -1,40 +1,40 @@\n" + "\n".join(
        f" line {line}" for line in range(1, 41)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 24)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        assert diff_view._virt.active is True

        diff_view.cursor_line = 1
        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        layout = diff_view._inline_comment_editor_layout_widget
        assert layout is not None
        await wait_until(lambda: layout.region.height > 0)
        initial_layout_height = layout.region.height
        initial_virtual_height = diff_view._virtual_content_height
        initial_next_line_top = diff_view._line_top_offsets[2]

        body = app.query_one("#comment-editor-body", TextArea)
        body.text = "\n".join(f"comment line {line}" for line in range(20))
        await wait_until(lambda: layout.region.height > initial_layout_height)
        await pilot.pause()

        height_delta = layout.region.height - initial_layout_height
        assert diff_view._inline_comment_editor_height() == layout.region.height
        assert diff_view._virtual_content_height - initial_virtual_height == height_delta
        assert diff_view._line_top_offsets[2] - initial_next_line_top == height_delta
        assert body.text.endswith("comment line 19")
        assert body.has_focus is True


@pytest.mark.asyncio
async def test_open_inline_comment_editor_uses_visual_selection_range() -> None:
    patch = """@@ -1,3 +1,4 @@
 line1
 line2
+line2.5
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.visual_mode = True
        diff_view.visual_type = "line"
        diff_view.visual_anchor_line = 0
        diff_view.cursor_line = 3
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()

        editor = app.query_one("#diff-inline-comment-editor")
        context = editor.query_one(".comment-editor-context", Static)

        assert diff_view.inline_comment_target() == ("test.py", 4, "RIGHT")
        assert diff_view.inline_comment_start_line() == 1
        assert diff_view.inline_comment_start_side() == "RIGHT"
        assert str(context.content) == "Selected: test.py:1-4 (new)"


@pytest.mark.asyncio
async def test_open_inline_comment_editor_uses_left_side_for_deleted_line() -> None:
    patch = "@@ -5,1 +5,0 @@\n-old"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.active_pane = "old"
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        assert diff_view.inline_comment_target() == ("test.py", 5, "LEFT")
