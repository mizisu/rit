"""Tests for DiffView hunk navigation and layout behavior."""

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.geometry import Region
from textual.widget import Widget
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.core.types import DiffLine, FileDiff
from rit.state.models import PRFile
from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_render as _render
from rit.ui.widgets import diff_view as _diff_view
from rit.ui.widgets.diff_view import DiffView
from rit.ui.widgets.diff_visual import MISSING_SIDE_HATCH_STYLE, MISSING_SIDE_STYLE


def _as_plain(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


def test_cycle_diff_mode_uses_shared_mode_label_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = DiffView()
    view.mode = "auto"
    messages: list[object] = []
    monkeypatch.setattr(view, "post_message", messages.append)
    monkeypatch.setattr(
        _diff_view,
        "_DIFF_MODE_LABELS",
        {"split": "Split from map"},
        raising=False,
    )

    view.action_cycle_diff_mode()

    assert view.mode == "split"
    assert messages[0].content == "Diff mode: Split from map"


def test_change_background_styles_remain_subtle() -> None:
    """Change markers should stay readable over syntax-highlighted code."""
    view = SimpleNamespace(_showing_full_file=False)
    added = DiffLine(old_line_no=None, new_line_no=1, new_content="new", is_added=True)
    deleted = DiffLine(
        old_line_no=1,
        new_line_no=None,
        old_content="old",
        is_deleted=True,
    )
    modified = DiffLine(
        old_line_no=1,
        new_line_no=1,
        old_content="old",
        new_content="new",
        is_modified=True,
    )

    assert _render._unified_line_style(view, added) == "on $success 6%"
    assert _render._unified_line_style(view, deleted) == "on $error 6%"
    assert _render._unified_line_style(view, modified, side="old") == "on $error 6%"
    assert _render._unified_line_style(view, modified, side="new") == "on $success 6%"
    assert _render._split_line_style(view, modified, side="old") == "on $error 6%"
    assert _render._split_line_style(view, modified, side="new") == "on $success 6%"
    assert _blocks._cursor_block_line_style("on $success 6%") == "on $success 18%"
    assert _blocks._cursor_block_line_style("on $error 6%") == "on $error 18%"
    assert MISSING_SIDE_STYLE == "on $background"
    assert MISSING_SIDE_HATCH_STYLE == "$text-disabled 9% on $background"

    css = Path("src/rit/ui/widgets/diff_view.tcss").read_text()
    for expected in (
        "background: $success 6%;",
        "background: $error 6%;",
        "background: $success 18%;",
        "background: $error 18%;",
        "background: $background;",
        "color: $text-disabled 9%;",
    ):
        assert expected in css

    resize_css = Path("src/rit/ui/widgets/resize_handle.py").read_text()
    assert "background: $panel;" in resize_css


def test_split_forcing_ignores_stale_file_metadata_from_previous_file() -> None:
    """Single-sided file metadata should apply only to the current file."""
    view = DiffView(mode="split")
    view.current_file = "target.py"
    view._file = PRFile(
        filename="previous.py",
        status="added",
        additions=1,
        deletions=0,
    )
    view._diff = FileDiff(filename="target.py")
    view._all_lines = [
        DiffLine(old_line_no=1, new_line_no=1, is_modified=True),
    ]

    assert _render._should_force_unified_for_current_file(view) is False


def test_cursor_line_refresh_uses_singleton_tuple_for_grouped_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        is_mounted = True
        _all_lines = [DiffLine(old_line_no=1, new_line_no=1)]

    calls: list[tuple[int, ...]] = []

    def refresh_grouped_blocks(_view: View, line_indices) -> bool:
        assert not isinstance(line_indices, set)
        calls.append(tuple(line_indices))
        return True

    monkeypatch.setattr(
        _render._blocks,
        "_refresh_grouped_blocks_for_lines",
        refresh_grouped_blocks,
    )

    _render._update_line_cursor(View(), 0)

    assert calls == [(0,)]


def test_scrollable_content_region_tolerates_unmounted_content_widget() -> None:
    view = DiffView()
    view._content_widget = Widget()

    assert view.scrollable_content_region == Region(0, 0, 0, 0)


def test_scrollable_content_region_reraises_unexpected_dock_gutter_errors() -> None:
    class BrokenContentWidget:
        @property
        def dock_gutter(self) -> tuple[int, int, int, int]:
            raise RuntimeError("dock gutter failed")

    view = DiffView()
    view._content_widget = BrokenContentWidget()

    with pytest.raises(RuntimeError, match="dock gutter failed"):
        _ = view.scrollable_content_region


def test_base_content_cache_invalidation_pops_indexed_line_keys_only() -> None:
    class RecordingCache(dict):
        def __init__(self) -> None:
            super().__init__({(7, "new", ""): Content("cached")})
            self.popped: list[tuple[int, str, str]] = []

        def pop(self, key, default=None):
            self.popped.append(key)
            return super().pop(key, default)

    view = DiffView()
    cache = RecordingCache()
    view._base_code_content_cache = cache
    view._base_code_content_cache_keys_by_line = {7: {(7, "new", "")}}

    _render._invalidate_base_code_content_cache(view, {7})

    assert cache.popped == [(7, "new", "")]
    assert cache == {}
    assert view._base_code_content_cache_keys_by_line == {}


@pytest.mark.asyncio
async def test_split_modified_word_diff_keeps_whole_line_change_classes() -> None:
    """Split modified rows should layer word and whole-line backgrounds."""

    patch = (
        "@@ -64,1 +64,1 @@\n"
        "-    def retrieve(self, request: UserAuthorizedRequest, "
        "review_cycle_entity_id: str):\n"
        "+    def retrieve(self, request: UserAuthorizedRequest, "
        "review_cycle_entity_id: str) -> Response:"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        old_code = diff_view.query_one("#line-0-old .code-content", Static)
        new_code = diff_view.query_one("#line-0-new .code-content", Static)

        assert diff.hunks[0].lines[0].has_word_diff
        assert old_code.has_class("-removed")
        assert new_code.has_class("-added")


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
        assert diff_view._virt.active is True
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
        assert diff_view._virt.active is False
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
async def test_app_static_style_keeps_only_cursor_line_number_bright() -> None:
    class TestApp(App):
        CSS_PATH: ClassVar[Path] = Path(__file__).parents[1] / "src/rit/rit.tcss"

        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch("@@ -1,2 +1,2 @@\n line1\n line2", "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        first_prefix = diff_view.query_one("#line-0 .line-prefix", Static)
        second_prefix = diff_view.query_one("#line-1 .line-prefix", Static)
        dim_color = second_prefix.styles.color
        assert first_prefix.styles.color != dim_color

        await pilot.press("j")
        await pilot.pause()

        assert first_prefix.styles.color == dim_color
        assert second_prefix.styles.color != dim_color


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

        first_prefix = diff_view.query_one("#line-0 .line-prefix", Static)
        assert first_prefix.has_class("-cursor")

        await pilot.press("j")
        await pilot.pause()

        old_prefix = diff_view.query_one("#line-1-old .line-prefix", Static)
        new_prefix = diff_view.query_one("#line-1-new .line-prefix", Static)
        old_code = diff_view.query_one("#line-1-old .code-content", Static)
        new_code = diff_view.query_one("#line-1-new .code-content", Static)
        assert diff_view.cursor_line == 1
        assert diff_view.cursor_pane == "old"
        assert diff_view.active_pane == "new"
        assert not first_prefix.has_class("-cursor")
        assert old_prefix.has_class("-cursor")
        assert not new_prefix.has_class("-cursor")
        assert old_code.has_class("-cursor")
        assert not new_code.has_class("-cursor")

        await pilot.press("j")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.cursor_pane == "new"
        assert diff_view.active_pane == "new"
        assert not old_prefix.has_class("-cursor")
        assert new_prefix.has_class("-cursor")
        assert not old_code.has_class("-cursor")
        assert new_code.has_class("-cursor")

        await pilot.press("j")
        await pilot.pause()

        last_prefix = diff_view.query_one("#line-2 .line-prefix", Static)
        assert diff_view.cursor_line == 2
        assert not new_prefix.has_class("-cursor")
        assert last_prefix.has_class("-cursor")


@pytest.mark.asyncio
async def test_grouped_unified_line_numbers_follow_the_cursor_row() -> None:
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
        diff_view.BLOCK_RENDER_LINE_THRESHOLD = 1
        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.focus()

        block = diff_view._unified_blocks_by_line[0]
        assert block._annotations._active_rows == {0}

        await pilot.press("j")
        await pilot.pause()

        modified_start, modified_end = block._row_ranges_by_line[1]
        assert block._annotations._active_rows == {modified_start}

        await pilot.press("j")
        await pilot.pause()

        assert block._annotations._active_rows == {modified_end - 1}


@pytest.mark.asyncio
async def test_grouped_split_line_numbers_follow_the_active_pane() -> None:
    patch = """@@ -1,3 +1,3 @@
 line1
 line2
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.BLOCK_RENDER_LINE_THRESHOLD = 1
        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.focus()

        block = diff_view._split_blocks_by_line[0]
        assert block._left_annotations._active_rows == set()
        assert block._right_annotations._active_rows == {0}

        diff_view.action_cycle_active_pane()
        await pilot.pause()

        assert block._left_annotations._active_rows == {0}
        assert block._right_annotations._active_rows == set()

        await pilot.press("j")
        await pilot.pause()

        assert block._left_annotations._active_rows == {1}
        assert block._right_annotations._active_rows == set()


@pytest.mark.asyncio
async def test_split_cursor_movement_preserves_selected_pane_across_missing_sides() -> (
    None
):
    """Moving through added/deleted lines should not rewrite the selected pane."""

    patch = """@@ -1,3 +1,4 @@
 line1
+added only
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

        diff_view.action_cycle_active_pane()
        await pilot.pause()
        assert diff_view.active_pane == "old"
        diff_view._move_cursor(column=3)
        assert diff_view.cursor_column == 3

        await pilot.press("j")
        await pilot.pause()

        added_old_code = diff_view.query_one("#line-1-old .code-content", Static)
        added_new_code = diff_view.query_one("#line-1-new .code-content", Static)
        assert diff_view.cursor_line == 1
        assert diff_view.active_pane == "old"
        assert diff_view.cursor_pane == "old"
        assert diff_view.cursor_column == 0
        assert added_old_code.has_class("-cursor")
        assert not added_new_code.has_class("-cursor")

        await pilot.press("v")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert diff_view.visual_mode is False
        assert added_old_code.has_class("-cursor")
        assert not added_new_code.has_class("-cursor")

        await pilot.press("j")
        await pilot.pause()

        modified_old_code = diff_view.query_one("#line-2-old .code-content", Static)
        modified_new_code = diff_view.query_one("#line-2-new .code-content", Static)
        assert diff_view.cursor_line == 2
        assert diff_view.active_pane == "old"
        assert diff_view.cursor_column == 3
        assert modified_old_code.has_class("-cursor")
        assert not modified_new_code.has_class("-cursor")


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
async def test_added_only_change_in_modified_file_stays_split() -> None:
    """One-sided additions in a modified file should follow split mode."""

    patch = """@@ -1,2 +1,3 @@
 line1
+line2
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

        code = diff_view.query_one("#line-1-new .code-content", Static)

        assert diff_view.split is True
        assert "line2" in _as_plain(code)


@pytest.mark.asyncio
async def test_deleted_only_change_in_modified_file_stays_split() -> None:
    """One-sided deletions in a modified file should follow split mode."""

    patch = """@@ -1,3 +1,2 @@
 line1
-line2
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

        code = diff_view.query_one("#line-1-old .code-content", Static)

        assert diff_view.split is True
        assert "line2" in _as_plain(code)


@pytest.mark.asyncio
async def test_unpaired_add_delete_change_stays_split() -> None:
    """Unpaired add/delete rows in a modified file should follow split mode."""

    patch = """@@ -1,3 +1,3 @@
 line1
-aaaaaaa
+zzzzzzz
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

        deleted_code = diff_view.query_one("#line-1-old .code-content", Static)
        added_code = diff_view.query_one("#line-2-new .code-content", Static)

        assert diff_view.split is True
        assert "aaaaaaa" in _as_plain(deleted_code)
        assert "zzzzzzz" in _as_plain(added_code)


@pytest.mark.asyncio
async def test_auto_mode_uses_split_when_lines_overflow() -> None:
    """Auto mode should let split panes scroll when the viewport is wide enough."""

    patch = (
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-old_value_old_value_old_value_old_value_old_value_old_value_old_value\n"
        "+new_value_new_value_new_value_new_value_new_value_new_value_new_value\n"
        " line2"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(120, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        assert diff_view.size.width == 120
        assert diff_view.split is True
        assert diff_view.query_one("#line-1-old .code-content", Static) is not None
        assert diff_view.query_one("#line-1-new .code-content", Static) is not None


@pytest.mark.asyncio
async def test_auto_mode_uses_unified_below_viewport_threshold() -> None:
    """Auto mode should depend on viewport width rather than content width."""

    patch = "@@ -1 +1 @@\n-old value\n+new value"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(119, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()

        assert diff_view.size.width == 119
        assert diff_view.split is False


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
