"""Tests for split-mode horizontal scrolling behavior."""

import pytest
from textual.app import App, ComposeResult
from textual.containers import HorizontalScroll
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView


def _as_plain(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


@pytest.mark.asyncio
async def test_split_mode_syncs_horizontal_scroll_between_row_panes() -> None:
    """Scrolling one split pane should keep the other pane at the same x position."""

    patch = (
        "@@ -1,1 +1,1 @@\n"
        "-old_value_old_value_old_value_old_value_old_value\n"
        "+new_value_new_value_new_value_new_value_new_value"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        left_scroll = diff_view.query_one(
            "#line-0-old .split-code-scroll",
            HorizontalScroll,
        )
        right_scroll = diff_view.query_one(
            "#line-0-new .split-code-scroll",
            HorizontalScroll,
        )

        left_scroll.scroll_x = 12
        await pilot.pause()
        await pilot.pause()

        assert right_scroll.scroll_x == 12
        assert diff_view.scroll_x == 0


@pytest.mark.asyncio
async def test_split_cursor_horizontal_reveal_scrolls_code_panes_not_view() -> None:
    """Cursor reveal in split mode should move code panes while keeping the outer view fixed."""

    patch = (
        "@@ -1,1 +1,1 @@\n"
        "-old_value_old_value_old_value_old_value_old_value\n"
        "+new_value_new_value_new_value_new_value_new_value"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_column = 24
        await pilot.pause()
        await pilot.pause()

        left_scroll = diff_view.query_one(
            "#line-0-old .split-code-scroll",
            HorizontalScroll,
        )
        right_scroll = diff_view.query_one(
            "#line-0-new .split-code-scroll",
            HorizontalScroll,
        )

        assert diff_view.scroll_x == 0
        assert right_scroll.scroll_x > 0
        assert left_scroll.scroll_x == right_scroll.scroll_x


@pytest.mark.asyncio
async def test_split_mode_uses_explicit_placeholders_for_missing_side() -> None:
    """Added/deleted rows in split mode should show visible placeholder content."""

    patch = "@@ -1,3 +1,3 @@\n line1\n-deleted_value\n+added_value\n line2"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        old_code = diff_view.query_one("#line-1-old .code-content", Static)
        new_code = diff_view.query_one("#line-1-new .code-content", Static)

        assert "deleted_value" in _as_plain(old_code)
        assert "added_value" in _as_plain(new_code)

        add_patch = "@@ -1,2 +1,3 @@\n line1\n+added_only\n line2"
        add_diff = parse_patch(add_patch, "test.py")
        await diff_view.show_diff("test.py", add_diff)
        await pilot.pause()

        placeholder = diff_view.query_one("#line-1-old .code-content", Static)
        assert "╲" in _as_plain(placeholder)


@pytest.mark.asyncio
async def test_split_mode_distinguishes_blank_lines_from_missing_sides() -> None:
    """Blank added/deleted lines should not render as missing-side placeholders."""

    patch = "@@ -1,1 +1,1 @@\n-\n+"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        old_code = diff_view.query_one("#line-0-old .code-content", Static)
        new_code = diff_view.query_one("#line-0-new .code-content", Static)

        assert "╲" not in _as_plain(old_code)
        assert "╲" not in _as_plain(new_code)

        add_patch = "@@ -1,1 +1,2 @@\n line\n+"
        await diff_view.show_diff("test.py", parse_patch(add_patch, "test.py"))
        await pilot.pause()

        missing_old = diff_view.query_one("#line-1-old .code-content", Static)
        blank_new = diff_view.query_one("#line-1-new .code-content", Static)

        assert "╲" in _as_plain(missing_old)
        assert "╲" not in _as_plain(blank_new)


@pytest.mark.asyncio
async def test_split_non_block_rows_share_horizontal_scroll_width() -> None:
    """Non-block split rows should keep a shared pane width so shorter rows still scroll."""

    patch = (
        "@@ -1,2 +1,2 @@\n"
        "-old_value_old_value_old_value_old_value_old_value\n"
        "+new_value_new_value_new_value_new_value_new_value\n"
        " short"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        first_left_scroll = diff_view.query_one(
            "#line-0-old .split-code-scroll",
            HorizontalScroll,
        )
        second_left_scroll = diff_view.query_one(
            "#line-1-old .split-code-scroll",
            HorizontalScroll,
        )

        first_left_scroll.scroll_x = 10
        await pilot.pause()
        await pilot.pause()

        assert second_left_scroll.scroll_x == 10


@pytest.mark.asyncio
async def test_split_rows_across_hunks_share_horizontal_scroll_width() -> None:
    """Rows from different hunks should still share pane scroll width."""

    patch = (
        "@@ -1,2 +1,2 @@\n"
        " short\n"
        " line2\n"
        "@@ -10,2 +10,2 @@\n"
        "-old_value_old_value_old_value_old_value_old_value\n"
        "+new_value_new_value_new_value_new_value_new_value\n"
        " line12"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        short_row_scroll = diff_view.query_one(
            "#line-0-old .split-code-scroll",
            HorizontalScroll,
        )
        long_row_scroll = diff_view.query_one(
            "#line-2-old .split-code-scroll",
            HorizontalScroll,
        )

        long_row_scroll.scroll_x = 10
        await pilot.pause()
        await pilot.pause()

        assert short_row_scroll.max_scroll_x >= 10
        assert short_row_scroll.scroll_x == 10


@pytest.mark.asyncio
async def test_split_hunk_header_scrolls_with_code_panes() -> None:
    """Split hunk headers should follow the shared horizontal scroll position."""

    patch = (
        "@@ -1,1 +1,1 @@ def get_pdf_convert_status(cls, command: AnalysisReportPdfConvertStatusCommand)\n"
        "-old_value_old_value_old_value_old_value_old_value\n"
        "+new_value_new_value_new_value_new_value_new_value"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        code_scroll = diff_view.query_one(
            "#line-0-new .split-code-scroll",
            HorizontalScroll,
        )
        header_scroll = diff_view.query_one(
            ".split-hunk-header-scroll",
            HorizontalScroll,
        )

        code_scroll.scroll_x = 12
        await pilot.pause()
        await pilot.pause()

        assert header_scroll.scroll_x == 12


@pytest.mark.asyncio
async def test_split_block_renderer_syncs_horizontal_scroll_between_panes() -> None:
    """Block-rendered split diffs should also keep both code panes horizontally aligned."""

    patch = (
        "@@ -1,2 +1,2 @@\n"
        "-old_value_old_value_old_value_old_value_old_value\n"
        "+new_value_new_value_new_value_new_value_new_value\n"
        " line2"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.BLOCK_RENDER_LINE_THRESHOLD = 1
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        left_scroll = diff_view.query_one(
            ".split-block .split-code-scroll.-old-side",
            HorizontalScroll,
        )
        right_scroll = diff_view.query_one(
            ".split-block .split-code-scroll.-new-side",
            HorizontalScroll,
        )

        right_scroll.scroll_x = 9
        await pilot.pause()
        await pilot.pause()

        assert left_scroll.scroll_x == 9
        assert diff_view.scroll_x == 0
