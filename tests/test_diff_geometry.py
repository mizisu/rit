"""Tests for DiffView geometry helpers."""

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine
from rit.ui.widgets.diff_geometry import (
    FILE_DIFF_HEADER_HEIGHT,
    ViewportGeometry,
    build_diff_geometry,
    cursor_viewport_offset,
    hunk_lines_for_window,
    line_index_at_vertical_offset,
    merge_line_ranges,
    row_vertical_bounds,
    row_is_visible,
    scroll_target_for_row_viewport_offset,
    scroll_target_for_span,
    should_render_hunk_header,
    virtual_bottom_buffer_height,
    virtual_top_buffer_height,
    viewport_center_line,
)
from rit.ui.widgets.diff_plan import build_diff_plan


def _planned_diff(patch: str):
    diff = parse_patch(patch, "test.py")
    build_diff_plan(diff)
    return diff


def test_build_diff_geometry_accounts_for_modified_rows_and_inline_extras() -> None:
    patch = """@@ -1,3 +1,3 @@
 context
-old value
+new value
 tail"""
    diff = _planned_diff(patch)

    geometry = build_diff_geometry(
        diff,
        split=False,
        extra_heights_by_line={1: 5},
        inline_editor_line_index=1,
        inline_editor_height=8,
    )

    assert geometry.hunk_header_top_offsets == [0]
    assert geometry.line_top_offsets == [1, 2, 17]
    assert geometry.line_heights == [1, 2, 1]
    assert geometry.line_bottom_offsets == [2, 17, 18]
    assert geometry.virtual_content_height == 18
    assert geometry.total_line_render_height == 4

    assert (
        line_index_at_vertical_offset(
            line_top_offsets=geometry.line_top_offsets,
            line_bottom_offsets=geometry.line_bottom_offsets,
            virtual_content_height=geometry.virtual_content_height,
            offset=16,
        )
        == 1
    )
    assert (
        viewport_center_line(
            line_top_offsets=geometry.line_top_offsets,
            line_bottom_offsets=geometry.line_bottom_offsets,
            virtual_content_height=geometry.virtual_content_height,
            scroll_y=10,
            dock_header_height=1,
            viewport_height=8,
        )
        == 1
    )


def test_build_diff_geometry_accounts_for_large_file_headers() -> None:
    patch = """@@ -1,2 +1,2 @@
 line1
 line2"""
    diff = _planned_diff(patch)
    diff.hunks[0].starts_file = True
    diff.show_hunk_headers = False

    geometry = build_diff_geometry(diff, split=True)

    assert FILE_DIFF_HEADER_HEIGHT == 1
    assert geometry.hunk_header_top_offsets == [0]
    assert geometry.line_top_offsets == [1, 2]
    assert geometry.virtual_content_height == 3


def test_virtual_buffer_geometry_respects_visible_hunk_headers() -> None:
    patch = """@@ -1,2 +1,2 @@
 line1
 line2
@@ -10,2 +10,2 @@
 line10
 line11"""
    diff = _planned_diff(patch)
    plan = build_diff_plan(diff)
    geometry = build_diff_geometry(diff, split=True)

    assert should_render_hunk_header(
        hunk_line_ranges=plan.hunk_line_ranges,
        hunk_index=1,
        window_start=2,
        window_end=3,
    )
    assert (
        virtual_top_buffer_height(
            total_lines=len(plan.all_lines),
            window_start=2,
            window_end=3,
            hunk_index_by_line=plan.hunk_index_by_line,
            hunk_line_ranges=plan.hunk_line_ranges,
            hunk_header_top_offsets=geometry.hunk_header_top_offsets,
            line_top_offsets=geometry.line_top_offsets,
        )
        == 3
    )
    assert (
        virtual_bottom_buffer_height(
            total_lines=len(plan.all_lines),
            window_end=2,
            virtual_content_height=geometry.virtual_content_height,
            line_bottom_offsets=geometry.line_bottom_offsets,
        )
        == 1
    )


def test_row_bounds_and_reveal_targets_are_pure_geometry() -> None:
    patch = """@@ -1,3 +1,3 @@
 context
-old value
+new value
 tail"""
    diff = _planned_diff(patch)
    plan = build_diff_plan(diff)
    geometry = build_diff_geometry(diff, split=False)
    old_row = plan.rendered_rows.rows_unified[1]
    new_row = plan.rendered_rows.rows_unified[2]

    assert row_vertical_bounds(
        old_row,
        all_lines=plan.all_lines,
        split=False,
        line_top_offsets=geometry.line_top_offsets,
        line_bottom_offsets=geometry.line_bottom_offsets,
    ) == (2, 3)
    assert row_vertical_bounds(
        new_row,
        all_lines=plan.all_lines,
        split=False,
        line_top_offsets=geometry.line_top_offsets,
        line_bottom_offsets=geometry.line_bottom_offsets,
    ) == (3, 4)

    viewport = ViewportGeometry(
        scroll_y=0,
        viewport_height=2,
        max_scroll_y=20,
        dock_header_height=1,
    )
    assert cursor_viewport_offset((3, 4), viewport) == 3
    assert scroll_target_for_row_viewport_offset((3, 4), viewport, 0) == 3
    assert scroll_target_for_span(top=3, bottom=4, viewport=viewport) == 2


def test_row_visibility_uses_scrollable_content_coordinates() -> None:
    """Docked headers should not expand the visible diff-line range."""

    viewport = ViewportGeometry(
        scroll_y=142,
        viewport_height=15,
        max_scroll_y=260,
        dock_header_height=3,
    )

    assert row_is_visible((156, 157), viewport)
    assert not row_is_visible((158, 159), viewport)


def test_scroll_target_for_span_respects_vertical_scrolloff() -> None:
    """Cursor reveal should keep Vim-like context around the target row."""

    viewport = ViewportGeometry(
        scroll_y=0,
        viewport_height=9,
        max_scroll_y=80,
        dock_header_height=3,
    )

    assert (
        scroll_target_for_span(top=10, bottom=11, viewport=viewport, scrolloff=2)
        == 4
    )

    scrolled_viewport = ViewportGeometry(
        scroll_y=20,
        viewport_height=9,
        max_scroll_y=80,
        dock_header_height=3,
    )

    assert (
        scroll_target_for_span(
            top=21,
            bottom=22,
            viewport=scrolled_viewport,
            scrolloff=2,
        )
        == 19
    )


def test_merge_line_ranges_sorts_and_coalesces_overlapping_ranges() -> None:
    assert merge_line_ranges([(8, 9), (1, 2), (3, 5), (5, 7)]) == [(1, 9)]
    assert merge_line_ranges([(4, 4), (8, 9), (6, 6)]) == [(4, 4), (6, 6), (8, 9)]
    assert merge_line_ranges([]) == []


def test_hunk_lines_for_window_slices_by_global_line_index() -> None:
    lines = [
        DiffLine(old_line_no=1, new_line_no=1, line_index=10),
        DiffLine(old_line_no=2, new_line_no=2, line_index=11),
        DiffLine(old_line_no=3, new_line_no=3, line_index=12),
    ]
    hunk = DiffHunk(old_start=1, old_count=3, new_start=1, new_count=3, lines=lines)

    assert hunk_lines_for_window(hunk, None, None) == lines
    assert hunk_lines_for_window(hunk, 11, 20) == lines[1:]
    assert hunk_lines_for_window(hunk, 0, 10) == lines[:1]
    assert hunk_lines_for_window(hunk, 20, 30) == []
    assert hunk_lines_for_window(hunk, 12, 11) == []
    assert (
        hunk_lines_for_window(
            DiffHunk(old_start=1, old_count=0, new_start=1, new_count=0),
            0,
            1,
        )
        == []
    )
