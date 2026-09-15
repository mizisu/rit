"""Tests for DiffView in-diff search navigation."""

import threading

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from rit.core.diff import parse_patch
from rit.ui.widgets import diff_search
from rit.ui.widgets.diff_search import SearchResult
from rit.ui.widgets.diff_search_types import SearchActivationUpdate
from rit.ui.widgets.diff_types import DiffSearchMatch
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


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

        await pilot.press("/")
        assert search_input.value == "match"
        assert search_input.has_focus


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

        match = diff_view._search.matches[diff_view._search.active_index]
        line_index = match.line_index
        line_top = diff_view._line_top_offsets[line_index]
        line_bottom = diff_view._line_bottom_offsets[line_index]
        scroll_y = int(diff_view.scroll_y)
        viewport_height = diff_view.scrollable_content_region.height
        assert scroll_y <= line_top and line_bottom <= scroll_y + viewport_height, (
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
        assert diff_view._search.query == ""


@pytest.mark.asyncio
async def test_large_search_builds_match_index_in_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -1,2 +1,2 @@\n alpha\n beta"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()

        threads: list[int] = []
        entered = threading.Event()
        release = threading.Event()
        original_build = diff_search.build_matches_from_rows

        def tracked_build(*args, **kwargs):
            threads.append(threading.get_ident())
            entered.set()
            assert release.wait(timeout=5)
            return original_build(*args, **kwargs)

        monkeypatch.setattr(diff_search, "_ASYNC_SEARCH_ROW_THRESHOLD", 1)
        monkeypatch.setattr(diff_search, "_SEARCH_DEBOUNCE_SECONDS", 0)
        monkeypatch.setattr(diff_search, "build_matches_from_rows", tracked_build)

        diff_view.action_start_search()
        search_input = diff_view.query_one("#diff-search-input", Input)
        search_input.value = "alpha"
        try:
            await wait_until(entered.is_set)
            await pilot.press("end", "left")
            assert search_input.has_focus
            assert search_input.cursor_position == 4
            assert diff_view.cursor_line == 0
        finally:
            release.set()
        await wait_until(lambda: diff_view._search.query == "alpha")

        assert threads and all(thread != threading.get_ident() for thread in threads)


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

        assert len(diff_view._search.matches) == 2
        assert diff_view._search.query == "foo"

        # Escape should clear the search
        await pilot.press("escape")
        await pilot.pause()

        assert diff_view._search.query == ""
        assert diff_view._search.matches == []
        assert diff_view._search.active_index == -1


def test_activate_search_match_reuses_dirty_lines_without_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dirty_lines = frozenset({2, 4})
    match = DiffSearchMatch(row_index=0, line_index=4, side="auto", column=3)
    view = DiffView(mode="unified")
    view.set_reactive(DiffView.visual_mode, False)
    invalidated: list[object] = []
    moved: list[dict[str, object]] = []
    scrolled: list[bool] = []
    monkeypatch.setattr(view, "_invalidate_base_code_content_cache", invalidated.append)
    monkeypatch.setattr(view, "_move_cursor", lambda **kwargs: moved.append(kwargs))
    monkeypatch.setattr(view, "_half_page_step", lambda: 10)
    monkeypatch.setattr(
        view, "_scroll_to_cursor_horizontal", lambda: scrolled.append(True)
    )

    view._activate_search_match(
        SearchActivationUpdate(
            match=match,
            dirty_lines=dirty_lines,
            pane=None,
            update_active_pane=False,
        )
    )

    assert invalidated[0] is dirty_lines
    assert moved == [
        {
            "line": 4,
            "pane": None,
            "column": 3,
            "scroll_in_visual": False,
            "update_active_pane": False,
        }
    ]
    assert scrolled == [True]


def test_display_search_result_reuses_dirty_lines_for_grouped_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rit.ui.widgets import diff_blocks

    dirty_lines = frozenset({1, 3})
    refreshed: list[object] = []

    view = DiffView(mode="unified")
    invalidated: list[object] = []
    monkeypatch.setattr(view, "_invalidate_base_code_content_cache", invalidated.append)

    def unexpected_line_refresh(_line: int) -> None:
        raise AssertionError("grouped refresh should handle dirty lines")

    monkeypatch.setattr(view, "_update_line_cursor", unexpected_line_refresh)
    monkeypatch.setattr(
        diff_blocks,
        "_refresh_grouped_blocks_for_lines",
        lambda _view, lines: refreshed.append(lines) or True,
    )
    view._display_search_result(SearchResult(dirty_lines=dirty_lines))
    assert invalidated[0] is dirty_lines
    assert refreshed == [dirty_lines]
    assert refreshed[0] is dirty_lines
