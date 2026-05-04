"""Tests for unified-mode horizontal scrolling behavior."""

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView


_LONG_LINE = "long_" * 30


@pytest.mark.asyncio
async def test_unified_non_block_rows_share_horizontal_scroll_width() -> None:
    """Short unified rows should span the same horizontal scroll width as long rows."""

    patch = f"@@ -1,4 +1,4 @@\n short\n-removed\n+added\n {_LONG_LINE}\n short2"

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        content = diff_view.query_one("#diff-content", VerticalScroll)
        short_row = diff_view.query_one("#line-0")
        removed_row = diff_view.query_one("#line-1-old")
        added_row = diff_view.query_one("#line-1-new")
        long_row = diff_view.query_one("#line-2")

        assert content.max_scroll_x > 0
        assert short_row.size.width == long_row.size.width
        assert removed_row.size.width == long_row.size.width
        assert added_row.size.width == long_row.size.width


@pytest.mark.asyncio
async def test_unified_cursor_reveal_scrolls_diff_content() -> None:
    """Cursor reveal should move the unified diff content scroller."""

    patch = f"@@ -1,1 +1,1 @@\n {_LONG_LINE}"

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        content = diff_view.query_one("#diff-content", VerticalScroll)
        diff_view.cursor_column = 80
        await pilot.pause()

        assert content.scroll_x > 0
        assert diff_view.scroll_x == 0


@pytest.mark.asyncio
async def test_unified_hunk_header_spans_block_scroll_width() -> None:
    """Unified hunk headers should cover the block renderer's horizontal width."""

    patch = f"@@ -1,4 +1,4 @@\n short\n-removed\n+added\n {_LONG_LINE}\n short2"

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(40, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.BLOCK_RENDER_LINE_THRESHOLD = 1
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        block = diff_view.query_one(".diff-block")
        hunk_header = diff_view.query_one("#hunk-0")

        assert hunk_header.virtual_size.width >= block.size.width
