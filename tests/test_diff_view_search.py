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
async def test_split_search_targets_matching_pane_for_modified_line() -> None:
    """Split-mode search should move the active pane to the side containing the hit."""

    patch = """@@ -1,2 +1,2 @@
-old_value
+new_value
 unchanged"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

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
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "old"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert diff_view.cursor_line == 0
        assert diff_view.active_pane == "old"
        assert diff_view.cursor_column == 0

        await pilot.press("/")
        await pilot.pause()
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "new"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert diff_view.cursor_line == 0
        assert diff_view.active_pane == "new"
        assert diff_view.cursor_column == 0


@pytest.mark.asyncio
async def test_search_highlights_all_matches() -> None:
    """Search should highlight all matches and distinguish the active one."""

    patch = """@@ -1,3 +1,3 @@
 foo bar foo
 baz
 foo end"""

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

        # Search for "foo" — should find 3 matches (line 0 col 0, line 0 col 8, line 2 col 0)
        await pilot.press("/")
        await pilot.pause()
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "foo"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert len(diff_view._search_matches) == 3
        # cursor was at col 0, so first match after cursor is col 8
        assert diff_view._search_match_index == 1
        assert diff_view.cursor_column == 8

        # Navigate to next match (different line)
        await pilot.press("n")
        await pilot.pause()
        assert diff_view._search_match_index == 2
        assert diff_view.cursor_line == 2

        # Wrap around to first match
        await pilot.press("n")
        await pilot.pause()
        assert diff_view._search_match_index == 0
        assert diff_view.cursor_line == 0
        assert diff_view.cursor_column == 0

        # Clear search
        await pilot.press("/")
        await pilot.pause()
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = ""
        await pilot.press("enter")
        await pilot.pause()

        assert diff_view._search_matches == []
        assert diff_view._search_match_index == -1


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
