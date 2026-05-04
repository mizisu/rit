"""Tests for DiffView geometry helpers."""

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_geometry import (
    ViewportGeometry,
    build_diff_geometry,
    cursor_viewport_offset,
    line_index_at_vertical_offset,
    row_vertical_bounds,
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
    assert cursor_viewport_offset((3, 4), viewport) == 2
    assert scroll_target_for_row_viewport_offset((3, 4), viewport, 0) == 2
    assert scroll_target_for_span(top=3, bottom=4, viewport=viewport) == 2
