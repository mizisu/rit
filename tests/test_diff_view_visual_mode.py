"""Tests for DiffView visual mode behavior (Vim-like v/V)."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.ui.widgets import diff_cursor as _cursor
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


@pytest.fixture
def sample_patch() -> str:
    """Sample patch for testing."""
    return """@@ -1,3 +1,4 @@
 line1
 line2
+new line
 line3"""


class TestDiffViewVisualMode:
    """Test Vim-like visual mode behavior."""

    @pytest.mark.asyncio
    async def test_v_toggles_character_visual_mode(self, sample_patch: str) -> None:
        """`v` should toggle character-wise visual mode."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("v")
            await pilot.pause()

            assert diff_view.visual_mode is True
            assert diff_view.visual_type == "char"
            assert app.sub_title == "-- VISUAL --"

            await pilot.press("v")
            await pilot.pause()

            assert diff_view.visual_mode is False
            assert app.sub_title == ""

    @pytest.mark.asyncio
    async def test_V_toggles_line_visual_mode(self, sample_patch: str) -> None:
        """`V` should toggle line-wise visual mode."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("V")
            await pilot.pause()

            assert diff_view.visual_mode is True
            assert diff_view.visual_type == "line"
            assert app.sub_title == "-- VISUAL LINE --"

            await pilot.press("V")
            await pilot.pause()

            assert diff_view.visual_mode is False
            assert app.sub_title == ""

    @pytest.mark.asyncio
    async def test_can_switch_between_v_and_V_modes(self, sample_patch: str) -> None:
        """In visual mode, pressing v/V should switch visual type."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("v")
            await pilot.pause()
            assert diff_view.visual_mode is True
            assert diff_view.visual_type == "char"

            await pilot.press("V")
            await pilot.pause()
            assert diff_view.visual_mode is True
            assert diff_view.visual_type == "line"

            await pilot.press("v")
            await pilot.pause()
            assert diff_view.visual_mode is True
            assert diff_view.visual_type == "char"

    @pytest.mark.asyncio
    async def test_line_visual_yank_copies_whole_lines(self, sample_patch: str) -> None:
        """Line-wise visual yank should copy entire selected lines."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            copied: dict[str, str] = {}

            def fake_copy(text: str) -> None:
                copied["text"] = text

            diff_view._copy_to_clipboard = fake_copy  # type: ignore[method-assign]

            await pilot.press("V")
            await pilot.pause()
            await pilot.press("j")  # Select one more line
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()

            assert copied["text"] == "line1\nline2\n"
            assert diff_view.visual_mode is False
            assert app.sub_title == ""

    @pytest.mark.asyncio
    async def test_normal_mode_yank_copies_current_line(
        self, sample_patch: str
    ) -> None:
        """In normal mode, `y` should copy the current line."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            copied: dict[str, str] = {}

            def fake_copy(text: str) -> None:
                copied["text"] = text

            diff_view._copy_to_clipboard = fake_copy  # type: ignore[method-assign]

            await pilot.press("j")  # Move to line2
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()

            assert copied["text"] == "line2\n"
            assert diff_view.visual_mode is False

    @pytest.mark.asyncio
    async def test_normal_mode_yank_uses_app_clipboard(
        self, sample_patch: str
    ) -> None:
        """Normal yank should use the same app clipboard path as other copy actions."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("y")
            await pilot.pause()

            assert app.clipboard == "line1\n"

    @pytest.mark.asyncio
    async def test_ctrl_d_keeps_cursor_near_same_viewport_row(self) -> None:
        """`Ctrl-D` should keep the cursor near the same screen row."""

        patch = "@@ -1,80 +1,80 @@\n" + "\n".join(f" line{i}" for i in range(1, 81))

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test(size=(100, 12)) as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            diff_view.cursor_line = 20
            await pilot.pause()
            await pilot.pause()

            before = diff_view._current_cursor_viewport_offset()
            assert before is not None

            await pilot.press("ctrl+d")
            await pilot.pause()
            await pilot.pause()

            after = diff_view._current_cursor_viewport_offset()
            assert after is not None
            assert diff_view.cursor_line > 20
            assert abs(after - before) <= 1

    @pytest.mark.asyncio
    async def test_ctrl_d_jumps_without_walking_each_intermediate_row(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`Ctrl-D` should jump once instead of moving through each cursor row."""

        patch = "@@ -1,80 +1,80 @@\n" + "\n".join(f" line{i}" for i in range(1, 81))
        move_calls = 0
        original_move_rows = _cursor._move_cursor_rows

        def count_move_rows(*args, **kwargs) -> bool:
            nonlocal move_calls
            move_calls += 1
            return original_move_rows(*args, **kwargs)

        monkeypatch.setattr(_cursor, "_move_cursor_rows", count_move_rows)

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test(size=(100, 12)) as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            diff_view.cursor_line = 20
            await pilot.pause()

            await pilot.press("ctrl+d")
            await pilot.pause()

            assert diff_view.cursor_line > 20
            assert move_calls <= 1

    @pytest.mark.asyncio
    async def test_ctrl_u_keeps_cursor_near_same_viewport_row(self) -> None:
        """`Ctrl-U` should keep the cursor near the same screen row."""

        patch = "@@ -1,80 +1,80 @@\n" + "\n".join(f" line{i}" for i in range(1, 81))

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test(size=(100, 12)) as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            diff_view.cursor_line = 40
            await pilot.pause()
            await pilot.pause()

            before = diff_view._current_cursor_viewport_offset()
            assert before is not None

            await pilot.press("ctrl+u")
            await pilot.pause()
            await pilot.pause()

            after = diff_view._current_cursor_viewport_offset()
            assert after is not None
            assert diff_view.cursor_line < 40
            assert abs(after - before) <= 1

    @pytest.mark.asyncio
    async def test_G_scrolls_to_bottom_of_diff(self) -> None:
        """`G` should move to the last row and scroll fully to the bottom."""

        patch = "@@ -1,80 +1,80 @@\n" + "\n".join(f" line{i}" for i in range(1, 81))

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test(size=(100, 12)) as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("G")
            await pilot.pause()
            await pilot.pause()

            assert diff_view.cursor_line == len(diff_view._all_lines) - 1
            assert abs(int(diff_view.scroll_y) - int(diff_view.max_scroll_y)) <= 1

    @pytest.mark.asyncio
    async def test_virtualized_G_reveals_last_line_after_window_shift(self) -> None:
        """`G` should reveal the last line after a virtual window jump."""

        line_count = 200
        patch = f"@@ -1,{line_count} +1,{line_count} @@\n" + "\n".join(
            f" line{i}" for i in range(1, line_count + 1)
        )

        class TestApp(App):
            def compose(self) -> ComposeResult:
                diff_view = DiffView(id="diff-view")
                diff_view.VIRTUALIZE_LINE_THRESHOLD = 20
                yield diff_view

        app = TestApp()
        async with app.run_test(size=(100, 12)) as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("G")
            await wait_until(
                lambda: (
                    diff_view.cursor_line == len(diff_view._all_lines) - 1
                    and diff_view._is_line_rendered(diff_view.cursor_line)
                    and (current_row := diff_view._current_row()) is not None
                    and diff_view._row_is_visible(current_row)
                    and not diff_view._cursor_ui.flush_pending
                    and not diff_view._virt.render_pending
                ),
                timeout=5.0,
            )

            current_row = diff_view._current_row()
            assert current_row is not None
            assert diff_view.cursor_line == len(diff_view._all_lines) - 1
            assert diff_view._is_line_rendered(diff_view.cursor_line)
            assert diff_view._row_is_visible(current_row)

    @pytest.mark.asyncio
    async def test_g_and_G_move_cursor(self, sample_patch: str) -> None:
        """`g`/`G` should move cursor to first/last line."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("G")
            await pilot.pause()
            assert diff_view.cursor_line == len(diff_view._all_lines) - 1

            await pilot.press("g")
            await pilot.pause()
            assert diff_view.cursor_line == 0

    @pytest.mark.asyncio
    async def test_visual_line_G_selects_to_last_line(self, sample_patch: str) -> None:
        """In Visual Line mode, `G` should extend selection to the last line."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("V")
            await pilot.pause()
            await pilot.press("G")
            await pilot.pause()

            last_line = len(diff_view._all_lines) - 1
            line0 = diff_view.query_one("#line-0")
            line_last = diff_view.query_one(f"#line-{last_line}")
            code0 = next(iter(line0.query(".code-content")))
            code_last = next(iter(line_last.query(".code-content")))

            assert code0.has_class("-selected")
            assert code_last.has_class("-selected")

    @pytest.mark.asyncio
    async def test_dollar_moves_cursor_to_end_of_line(self, sample_patch: str) -> None:
        """`$` should move cursor to end of current line."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("$")
            await pilot.pause()

            assert diff_view.cursor_column == len("line1") - 1

    @pytest.mark.asyncio
    async def test_zero_and_caret_move_to_line_start_positions(self) -> None:
        """`0` and `^` should behave like Vim start-of-line motions."""

        patch = """@@ -1,2 +1,3 @@
     indented line
+added
 line2"""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("$")
            await pilot.pause()
            assert diff_view.cursor_column == len("    indented line") - 1

            await pilot.press("0")
            await pilot.pause()
            assert diff_view.cursor_column == 0

            await pilot.press("$")
            await pilot.pause()
            await pilot.press("^")
            await pilot.pause()
            assert diff_view.cursor_column == 4

    @pytest.mark.asyncio
    async def test_char_visual_dollar_keeps_no_line_highlight(
        self, sample_patch: str
    ) -> None:
        """In `v` mode, `$` should extend char selection without line highlight class."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("v")
            await pilot.pause()
            await pilot.press("$")
            await pilot.pause()

            line0 = diff_view.query_one("#line-0")
            code0 = next(iter(line0.query(".code-content")))

            assert not code0.has_class("-selected")
            assert not code0.has_class("-anchor")
            assert diff_view.cursor_column == len("line1") - 1

    @pytest.mark.asyncio
    async def test_char_visual_has_no_line_highlight_class(
        self, sample_patch: str
    ) -> None:
        """`v` mode should not apply line-level highlight classes."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("v")
            await pilot.pause()
            await pilot.press("j")  # multi-line selection
            await pilot.pause()

            line0 = diff_view.query_one("#line-0")
            line1 = diff_view.query_one("#line-1")
            code0 = next(iter(line0.query(".code-content")))
            code1 = next(iter(line1.query(".code-content")))

            assert not code0.has_class("-selected")
            assert not code0.has_class("-anchor")
            assert not code1.has_class("-selected")
            assert not code1.has_class("-anchor")

    @pytest.mark.asyncio
    async def test_line_visual_keeps_line_highlight_class(
        self, sample_patch: str
    ) -> None:
        """`V` mode should still apply line-level highlight classes."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("V")
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()

            line0 = diff_view.query_one("#line-0")
            line1 = diff_view.query_one("#line-1")
            code0 = next(iter(line0.query(".code-content")))
            code1 = next(iter(line1.query(".code-content")))

            assert code0.has_class("-selected")
            assert code0.has_class("-anchor")
            assert code1.has_class("-selected")

    @pytest.mark.asyncio
    async def test_modified_line_keeps_old_new_text_when_cursor_moves(self) -> None:
        """Cursor movement must not overwrite modified old-line text with new text."""

        patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            # Move cursor onto the modified line (line index 1)
            await pilot.press("j")
            await pilot.pause()

            old_code = diff_view.query_one("#line-1-old .code-content", Static)
            new_code = diff_view.query_one("#line-1-new .code-content", Static)

            old_text = getattr(old_code.content, "plain", str(old_code.content))
            new_text = getattr(new_code.content, "plain", str(new_code.content))

            assert old_text == "old content here"
            assert new_text == "new content here"

    @pytest.mark.asyncio
    async def test_modified_line_keeps_old_new_text_in_visual_char(self) -> None:
        """Visual-char updates must keep old/new panes distinct for modified lines."""

        patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("v")
            await pilot.pause()
            await pilot.press("j")  # selection update path
            await pilot.pause()

            old_code = diff_view.query_one("#line-1-old .code-content", Static)
            new_code = diff_view.query_one("#line-1-new .code-content", Static)

            old_text = getattr(old_code.content, "plain", str(old_code.content))
            new_text = getattr(new_code.content, "plain", str(new_code.content))

            assert old_text == "old content here"
            assert new_text == "new content here"

    @pytest.mark.asyncio
    async def test_vertical_motion_preserves_goal_column_across_short_rows(
        self,
    ) -> None:
        """`j`/`k` should restore the preferred column after crossing short rows."""

        patch = """@@ -1,3 +1,3 @@
 long_column
 x
 long_column"""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("$")
            await pilot.press("j")
            await pilot.press("j")
            await pilot.pause()

            assert diff_view.cursor_line == 2
            assert diff_view.cursor_column == len("long_column") - 1

    @pytest.mark.asyncio
    async def test_count_prefix_applies_to_word_motion(self) -> None:
        """Count prefixes should work for word motions, not only j/k."""

        patch = """@@ -1,1 +1,1 @@
 one two three four"""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("2")
            await pilot.press("w")
            await pilot.pause()

            assert diff_view.cursor_column == len("one two ")

    @pytest.mark.asyncio
    async def test_prev_word_cross_line_lands_on_word_start(self) -> None:
        """`b` from a line start should land on the previous word start."""

        patch = """@@ -1,2 +1,2 @@
 hello world
 second line"""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("j")
            await pilot.press("b")
            await pilot.pause()

            assert diff_view.cursor_line == 0
            assert diff_view.cursor_column == len("hello ")

    @pytest.mark.asyncio
    async def test_next_word_skips_whitespace_only_rows(self) -> None:
        """`w` should skip whitespace-only rows when searching for a word."""

        patch = "@@ -1,3 +1,3 @@\n foo\n    \n bar"

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "test.py")

            await diff_view.show_diff("test.py", diff)
            await pilot.pause()
            diff_view.focus()
            await pilot.pause()

            await pilot.press("$")
            await pilot.press("w")
            await pilot.pause()

            assert diff_view.cursor_line == 2
            assert diff_view.cursor_column == 0
