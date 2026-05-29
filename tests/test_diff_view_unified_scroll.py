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
async def test_cursor_move_brings_widget_into_view_with_pending_drafts() -> None:
    """Cursor reveal should use widget positions when drafts make height estimates drift."""

    from rit.state.store import PRStore

    line_count = 80
    lines = [f" line{i}" for i in range(1, line_count + 1)]
    patch = f"@@ -1,{line_count} +1,{line_count} @@\n" + "\n".join(lines)

    store = PRStore()
    for early_line in (1, 5, 10, 20):
        store.save_pending_inline_comment(
            "draft body line one\ndraft body line two\ndraft body line three\n"
            "draft body line four\ndraft body line five",
            path="test.py",
            line=early_line,
            side="RIGHT",
        )

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 8)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 60
        await pilot.pause()
        await pilot.pause()

        widget = diff_view._line_widgets_by_index.get(60)
        assert widget is not None and widget.is_mounted
        viewport = diff_view.region
        assert viewport.contains_region(widget.region)


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
