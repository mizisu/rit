"""Tests for DiffView in-diff search navigation."""

from textual.app import App, ComposeResult
from textual.widgets import Input
import pytest

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView


@pytest.mark.asyncio
async def test_search_bar_moves_between_matches_with_n_and_N() -> None:
    """`/`, `n`, and `N` should search the current diff in row-space order."""

    patch = """@@ -1,4 +1,4 @@
 alpha
 match here
 beta
 second match"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        search_bar = diff_view.query_one("#diff-search-bar")
        assert search_bar.display is True

        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "match"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert search_bar.display is False
        assert diff_view.cursor_line == 1
        assert diff_view.cursor_column == 0

        await pilot.press("n")
        await pilot.pause()

        assert diff_view.cursor_line == 3

        await pilot.press("N")
        await pilot.pause()

        assert diff_view.cursor_line == 1


@pytest.mark.asyncio
async def test_far_search_jump_anchors_match_near_top_of_viewport() -> None:
    """Far search jumps should place the destination near the top of the viewport."""

    lines = [f" line{i}" for i in range(1, 81)]
    lines[2] = " alpha match"
    lines[59] = " beta match"
    patch = "@@ -1,80 +1,80 @@\n" + "\n".join(lines)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 8)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "match"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()

        row = diff_view._current_row()
        assert row is not None
        top, _ = diff_view._row_vertical_bounds(row) or (None, None)
        assert top is not None
        assert abs(top - int(diff_view.scroll_y)) <= 1


@pytest.mark.asyncio
async def test_search_n_navigation_brings_bottom_match_into_view() -> None:
    """Pressing `n` to navigate to a bottom match should scroll viewport to it."""

    line_count = 300
    lines = [f" line{i}" for i in range(1, line_count + 1)]
    lines[10] = " Answer one"
    lines[280] = " Answer two"
    lines[290] = " Answer three"
    patch = f"@@ -1,{line_count} +1,{line_count} @@\n" + "\n".join(lines)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "Answer"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        await pilot.pause()

        match = diff_view._search_matches[diff_view._search_match_index]
        line_index = match.line_index
        line_top = diff_view._line_top_offsets[line_index]
        line_bottom = diff_view._line_bottom_offsets[line_index]
        scroll_y = int(diff_view.scroll_y)
        viewport_height = diff_view.scrollable_content_region.height
        assert (
            scroll_y <= line_top and line_bottom <= scroll_y + viewport_height
        ), (
            f"line_top={line_top} line_bottom={line_bottom} "
            f"scroll_y={scroll_y} viewport_h={viewport_height} "
            f"match line={line_index}"
        )


@pytest.mark.asyncio
async def test_search_bar_escape_dismisses_without_searching() -> None:
    """Pressing Escape in the search bar should dismiss it without searching."""

    patch = """@@ -1,2 +1,2 @@
 alpha
 beta"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        search_bar = diff_view.query_one("#diff-search-bar")
        assert search_bar.display is True

        await pilot.press("escape")
        await pilot.pause()

        assert search_bar.display is False
        assert diff_view._search_query == ""


@pytest.mark.asyncio
async def test_search_input_keeps_text_keys_and_escape_precedes_visual_exit() -> None:
    """Search input owns typing and Escape before visual mode sees Escape."""

    patch = """@@ -1,2 +1,2 @@
 alpha1
 beta2"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

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
        assert diff_view.visual_mode is True

        await pilot.press("/")
        await pilot.pause()
        search_bar = diff_view.query_one("#diff-search-bar")
        search_input = diff_view.query_one("#diff-search-input", Input)

        await pilot.press("1", "j", "k")
        await pilot.pause()

        assert search_input.value == "1jk"
        assert diff_view._cursor_ui.pending_count == ""

        await pilot.press("escape")
        await pilot.pause()

        assert search_bar.display is False
        assert diff_view.visual_mode is True
        assert diff_view.has_focus

        await pilot.press("escape")
        await pilot.pause()

        assert diff_view.visual_mode is False


@pytest.mark.asyncio
async def test_escape_clears_search_highlights() -> None:
    """Pressing Escape outside the search bar should clear active search."""

    patch = """@@ -1,2 +1,2 @@
 foo bar
 baz foo"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        # Perform a search
        await pilot.press("/")
        await pilot.pause()
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "foo"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert len(diff_view._search_matches) == 2
        assert diff_view._search_query == "foo"

        # Escape should clear the search
        await pilot.press("escape")
        await pilot.pause()

        assert diff_view._search_query == ""
        assert diff_view._search_matches == []
        assert diff_view._search_match_index == -1
