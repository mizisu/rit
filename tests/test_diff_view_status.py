"""Tests for DiffView hunk navigation and layout behavior."""

import threading

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView


def _as_plain(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


@pytest.mark.asyncio
async def test_status_line_tracks_current_hunk_and_cursor() -> None:
    """Hunk navigation should move the cursor to the correct hunk."""

    patch = """@@ -1,3 +1,3 @@
 line1
-old1
+new1
 line3
@@ -10,3 +10,4 @@
 line10
+added
 line11
 line12"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        assert diff_view.current_hunk_index == 0

        diff_view.next_hunk()
        await pilot.pause()
        await pilot.pause()

        assert diff_view.current_hunk_index == 1
        assert diff_view.cursor_line == 3


@pytest.mark.asyncio
async def test_virtualized_diff_uses_windowed_highlight_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large virtualized diffs should highlight visible windows, not the full diff."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,41 @@\n{context_lines}\n+added_line"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_view_module

    range_calls = {"count": 0}
    full_calls = {"count": 0}
    highlighted = threading.Event()
    original_range = diff_view_module.highlight_lines_for_diff_range

    def counted_range(*args, **kwargs):
        range_calls["count"] += 1
        result = original_range(*args, **kwargs)
        highlighted.set()
        return result

    def counted_full(*args, **kwargs):
        full_calls["count"] += 1
        return None

    monkeypatch.setattr(
        diff_view_module, "highlight_lines_for_diff_range", counted_range
    )
    monkeypatch.setattr(diff_view_module, "highlight_lines_for_diff", counted_full)

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert highlighted.wait(timeout=1.0) is True
        assert diff_view._virtualized is True
        assert range_calls["count"] >= 1
        assert full_calls["count"] == 0


@pytest.mark.asyncio
async def test_medium_block_diff_uses_windowed_highlight_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Medium block-rendered diffs should highlight the visible window first."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_view_module

    range_calls = {"count": 0}
    full_calls = {"count": 0}
    highlighted = threading.Event()
    original_range = diff_view_module.highlight_lines_for_diff_range

    def counted_range(*args, **kwargs):
        range_calls["count"] += 1
        result = original_range(*args, **kwargs)
        highlighted.set()
        return result

    def counted_full(*args, **kwargs):
        full_calls["count"] += 1
        return None

    monkeypatch.setattr(
        diff_view_module, "highlight_lines_for_diff_range", counted_range
    )
    monkeypatch.setattr(diff_view_module, "highlight_lines_for_diff", counted_full)

    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert highlighted.wait(timeout=1.0) is True
        assert diff_view._virtualized is False
        assert len(diff_view._all_lines) >= diff_view.BLOCK_RENDER_LINE_THRESHOLD
        assert range_calls["count"] >= 1
        assert full_calls["count"] == 0


@pytest.mark.asyncio
async def test_hunk_jump_places_target_near_top_of_viewport() -> None:
    """Hunk jumps should anchor the destination near the top of the viewport."""

    first_hunk = "\n".join(f" line{i}" for i in range(1, 17))
    second_hunk = "\n".join(f" line{i}" for i in range(40, 56))
    patch = f"@@ -1,16 +1,16 @@\n{first_hunk}\n@@ -40,16 +40,16 @@\n{second_hunk}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 8)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.next_hunk()
        await pilot.pause()
        await pilot.pause()

        row = diff_view._current_row()
        assert row is not None
        top, _ = diff_view._row_vertical_bounds(row) or (None, None)
        assert top is not None
        assert abs(top - int(diff_view.scroll_y)) <= 1


@pytest.mark.asyncio
async def test_unified_modified_line_navigation_uses_rendered_rows() -> None:
    """Unified mode should stop on old/new rows of a modified line separately."""

    patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

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

        await pilot.press("j")
        await pilot.pause()

        old_code = diff_view.query_one("#line-1-old .code-content", Static)
        new_code = diff_view.query_one("#line-1-new .code-content", Static)
        assert diff_view.cursor_line == 1
        assert diff_view.active_pane == "old"
        assert old_code.has_class("-cursor")
        assert not new_code.has_class("-cursor")

        await pilot.press("j")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.active_pane == "new"
        assert not old_code.has_class("-cursor")
        assert new_code.has_class("-cursor")

        await pilot.press("j")
        await pilot.pause()

        assert diff_view.cursor_line == 2


@pytest.mark.asyncio
async def test_added_only_file_forces_unified_even_in_split_mode() -> None:
    """Added-only files should render unified because there is no old side to compare."""

    patch = """@@ -0,0 +1,3 @@
+line1
+line2
+line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        code = diff_view.query_one("#line-0 .code-content", Static)

        assert diff_view.split is False
        assert "line1" in _as_plain(code)


@pytest.mark.asyncio
async def test_deleted_only_file_forces_unified_even_in_auto_split_layout() -> None:
    """Deleted-only files should also stay unified even when auto layout is wide enough."""

    patch = """@@ -1,3 +0,0 @@
-line1
-line2
-line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        code = diff_view.query_one("#line-0 .code-content", Static)

        assert diff_view.split is False
        assert "line1" in _as_plain(code)


@pytest.mark.asyncio
async def test_split_mode_switches_active_pane() -> None:
    """Split mode should keep one active pane and allow switching it."""

    patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

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

        await pilot.press("j")
        await pilot.pause()

        old_code = diff_view.query_one("#line-1-old .code-content", Static)
        new_code = diff_view.query_one("#line-1-new .code-content", Static)

        assert diff_view.active_pane == "new"
        assert not old_code.has_class("-cursor")
        assert new_code.has_class("-cursor")

        diff_view.action_cycle_active_pane()
        await pilot.pause()

        assert diff_view.active_pane == "old"
        assert old_code.has_class("-cursor")
        assert not new_code.has_class("-cursor")
