"""Tests for unified-mode horizontal scrolling behavior."""

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView


_LONG_LINE = "long_" * 30


async def _wait_until_state(predicate, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.01)


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
async def test_single_line_move_reveals_block_row_when_drafts_exist() -> None:
    """Block-rendered rows should still follow the cursor when drafts add height drift."""

    from rit.state.store import PRStore

    line_count = 267
    lines = [f" line{i}" for i in range(139, 139 + line_count)]
    patch = f"@@ -139,{line_count} +65,{line_count} @@\n" + "\n".join(lines)

    store = PRStore()
    store.save_pending_inline_comment(
        "draft body",
        path="openapi.py",
        line=200,
        side="RIGHT",
    )

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(120, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "openapi.py")

        await diff_view.show_diff("openapi.py", diff)
        await pilot.pause()
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 17
        await pilot.pause()
        await pilot.pause()

        row = diff_view._current_row()
        assert row is not None
        _, bottom = diff_view._row_vertical_bounds(row) or (None, None)
        assert bottom is not None
        diff_view.scroll_y = max(
            0,
            bottom - max(1, diff_view.scrollable_content_region.height),
        )
        await pilot.pause()
        await pilot.pause()

        before_scroll_y = int(diff_view.scroll_y)

        await pilot.press("j")
        await pilot.pause()
        await pilot.pause()

        row = diff_view._current_row()
        assert row is not None
        top, bottom = diff_view._row_vertical_bounds(row) or (None, None)
        assert top is not None and bottom is not None
        viewport_top = int(diff_view.scroll_y)
        viewport_bottom = viewport_top + diff_view.scrollable_content_region.height

        assert diff_view.cursor_line == 18
        assert int(diff_view.scroll_y) > before_scroll_y
        assert top >= viewport_top
        assert bottom <= viewport_bottom


@pytest.mark.asyncio
async def test_cursor_reveal_keeps_vim_scrolloff_context() -> None:
    """Cursor reveal should keep context below the cursor before the viewport edge."""

    line_count = 80
    lines = [f" line{i}" for i in range(1, line_count + 1)]
    patch = f"@@ -1,{line_count} +1,{line_count} @@\n" + "\n".join(lines)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await _wait_until_state(
            lambda: (
                (row := diff_view._current_row()) is not None
                and diff_view._row_vertical_bounds(row) is not None
            ),
            timeout=5.0,
        )
        await pilot.pause()
        diff_view.focus()

        diff_view.cursor_line = 10
        await pilot.pause()
        await pilot.pause()

        row = diff_view._current_row()
        assert row is not None
        _, bottom = diff_view._row_vertical_bounds(row) or (None, None)
        assert bottom is not None

        viewport_bottom = (
            int(diff_view.scroll_y) + diff_view.scrollable_content_region.height
        )
        assert bottom <= viewport_bottom - diff_view.LAYOUT.vertical_scrolloff


@pytest.mark.asyncio
async def test_single_line_cursor_move_repaints_immediately() -> None:
    """One-line cursor moves should not leave the cursor repaint queued."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"

    class TestApp(App[None]):
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

        diff_view.action_scroll_down()

        assert diff_view.cursor_line == 1
        assert diff_view._cursor_ui.flush_pending is False
        line = diff_view.query_one("#line-1 .code-content")
        assert line.has_class("-cursor")


@pytest.mark.asyncio
async def test_show_diff_new_file_resets_vertical_scroll_to_cursor() -> None:
    """Opening a new file should not keep the previous file's vertical scroll."""

    line_count = 80
    lines = [f" line{i}" for i in range(1, line_count + 1)]
    patch = f"@@ -1,{line_count} +1,{line_count} @@\n" + "\n".join(lines)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("first.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("G")
        await pilot.pause()
        await pilot.pause()
        assert diff_view.scroll_y > 0

        await diff_view.show_diff("second.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert diff_view.cursor_line == 0
        assert diff_view.scroll_y == 0


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
