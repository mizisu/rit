"""DiffView geometry and viewport reveal calculations."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from rit.core.types import DiffLine, FileDiff
from rit.ui.widgets.diff_types import RenderedRow


@dataclass(frozen=True)
class DiffGeometry:
    hunk_header_top_offsets: list[int]
    line_top_offsets: list[int]
    line_heights: list[int]
    line_bottom_offsets: list[int]
    virtual_content_height: int
    total_line_render_height: int


@dataclass(frozen=True)
class ViewportGeometry:
    scroll_y: int
    viewport_height: int
    max_scroll_y: int
    dock_header_height: int = 0


def render_height_for_line(line: DiffLine, *, split: bool) -> int:
    if not split and line.is_modified:
        return 2
    return 1


def build_diff_geometry(
    diff: FileDiff | None,
    *,
    split: bool,
    extra_heights_by_line: dict[int, int] | None = None,
    inline_editor_line_index: int | None = None,
    inline_editor_height: int = 0,
) -> DiffGeometry:
    if diff is None:
        return DiffGeometry([], [], [], [], 0, 0)

    line_count = (
        max(
            (line.line_index for hunk in diff.hunks for line in hunk.lines),
            default=-1,
        )
        + 1
    )
    hunk_header_top_offsets: list[int] = []
    line_top_offsets = [0] * line_count
    line_heights = [1] * line_count
    line_bottom_offsets = [0] * line_count
    total_line_render_height = 0
    extras = extra_heights_by_line or {}

    offset = 0
    for hunk in diff.hunks:
        hunk_header_top_offsets.append(offset)
        offset += 1
        for line in hunk.lines:
            line_index = line.line_index
            if line_index < 0 or line_index >= line_count:
                continue

            height = render_height_for_line(line, split=split)
            line_top_offsets[line_index] = offset
            line_heights[line_index] = height
            total_line_render_height += height
            offset += height

            if line_index == inline_editor_line_index:
                offset += max(0, inline_editor_height)

            offset += max(0, extras.get(line_index, 0))
            line_bottom_offsets[line_index] = offset

    return DiffGeometry(
        hunk_header_top_offsets=hunk_header_top_offsets,
        line_top_offsets=line_top_offsets,
        line_heights=line_heights,
        line_bottom_offsets=line_bottom_offsets,
        virtual_content_height=offset,
        total_line_render_height=total_line_render_height,
    )


def line_index_at_vertical_offset(
    *,
    line_top_offsets: list[int],
    line_bottom_offsets: list[int],
    virtual_content_height: int,
    offset: int,
) -> int:
    if not line_top_offsets:
        return 0

    clamped = max(0, min(offset, max(0, virtual_content_height - 1)))
    index = bisect_right(line_top_offsets, clamped) - 1
    if index < 0:
        return 0
    if clamped >= line_bottom_offsets[index] and index + 1 < len(line_top_offsets):
        return index + 1
    return index


def viewport_center_line(
    *,
    line_top_offsets: list[int],
    line_bottom_offsets: list[int],
    virtual_content_height: int,
    scroll_y: int,
    dock_header_height: int,
    viewport_height: int,
) -> int:
    if not line_top_offsets:
        return 0
    center_offset = int(scroll_y + dock_header_height + max(1, viewport_height) / 2)
    return line_index_at_vertical_offset(
        line_top_offsets=line_top_offsets,
        line_bottom_offsets=line_bottom_offsets,
        virtual_content_height=virtual_content_height,
        offset=center_offset,
    )


def rendered_line_bounds(
    *,
    total_lines: int,
    virtual_active: bool,
    rendered_start: int,
    rendered_end: int,
) -> tuple[int, int]:
    if total_lines <= 0:
        return 0, -1
    if virtual_active:
        start = max(0, rendered_start)
        end = min(total_lines - 1, rendered_end)
        return start, end
    return 0, total_lines - 1


def is_line_rendered(
    line_index: int,
    *,
    total_lines: int,
    virtual_active: bool,
    rendered_start: int,
    rendered_end: int,
) -> bool:
    if line_index < 0 or line_index >= total_lines:
        return False
    start, end = rendered_line_bounds(
        total_lines=total_lines,
        virtual_active=virtual_active,
        rendered_start=rendered_start,
        rendered_end=rendered_end,
    )
    return start <= line_index <= end


def should_render_hunk_header(
    *,
    hunk_line_ranges: list[tuple[int, int, int]],
    hunk_index: int,
    window_start: int,
    window_end: int,
) -> bool:
    if not (0 <= hunk_index < len(hunk_line_ranges)):
        return False
    _, hunk_start, hunk_end = hunk_line_ranges[hunk_index]
    if hunk_end < window_start or hunk_start > window_end:
        return False
    return window_start <= hunk_start <= window_end


def virtual_top_buffer_height(
    *,
    total_lines: int,
    window_start: int,
    window_end: int,
    hunk_index_by_line: list[int],
    hunk_line_ranges: list[tuple[int, int, int]],
    hunk_header_top_offsets: list[int],
    line_top_offsets: list[int],
) -> int:
    if total_lines <= 0 or not (0 <= window_start < total_lines):
        return 0

    hunk_index = hunk_index_by_line[window_start]
    if should_render_hunk_header(
        hunk_line_ranges=hunk_line_ranges,
        hunk_index=hunk_index,
        window_start=window_start,
        window_end=window_end,
    ):
        if 0 <= hunk_index < len(hunk_header_top_offsets):
            return hunk_header_top_offsets[hunk_index]
    return line_top_offsets[window_start]


def virtual_bottom_buffer_height(
    *,
    total_lines: int,
    window_end: int,
    virtual_content_height: int,
    line_bottom_offsets: list[int],
) -> int:
    if total_lines <= 0 or not (0 <= window_end < total_lines):
        return 0
    return max(0, virtual_content_height - line_bottom_offsets[window_end])


def row_vertical_bounds(
    row: RenderedRow,
    *,
    all_lines: list[DiffLine],
    split: bool,
    line_top_offsets: list[int],
    line_bottom_offsets: list[int],
) -> tuple[int, int] | None:
    if not (0 <= row.line_index < len(all_lines)):
        return None
    if row.line_index >= len(line_top_offsets) or row.line_index >= len(
        line_bottom_offsets
    ):
        return None

    top = line_top_offsets[row.line_index]
    bottom = line_bottom_offsets[row.line_index]
    line = all_lines[row.line_index]
    if not split and line.is_modified:
        if row.side == "old":
            return top, top + 1
        if row.side == "new":
            return top + 1, top + 2
    return top, bottom


def cursor_viewport_offset(
    bounds: tuple[int, int],
    viewport: ViewportGeometry,
) -> int:
    top, _ = bounds
    return max(0, top - viewport.scroll_y - viewport.dock_header_height)


def scroll_target_for_row_viewport_offset(
    bounds: tuple[int, int],
    viewport: ViewportGeometry,
    viewport_offset: int,
) -> int:
    top, bottom = bounds
    target_scroll = max(
        0,
        top - max(0, viewport_offset) - viewport.dock_header_height,
    )
    if bottom - target_scroll - viewport.dock_header_height > max(
        1, viewport.viewport_height
    ):
        target_scroll = max(
            0,
            bottom - max(1, viewport.viewport_height) - viewport.dock_header_height,
        )
    return _clamp_scroll_y(target_scroll, viewport.max_scroll_y)


def scroll_target_for_row_bottom(
    bounds: tuple[int, int],
    viewport: ViewportGeometry,
) -> int:
    _, bottom = bounds
    return _clamp_scroll_y(
        bottom - max(1, viewport.viewport_height), viewport.max_scroll_y
    )


def row_is_visible(bounds: tuple[int, int], viewport: ViewportGeometry) -> bool:
    top, bottom = bounds
    current_top = viewport.scroll_y + viewport.dock_header_height
    current_bottom = current_top + max(1, viewport.viewport_height)
    return top >= current_top and bottom <= current_bottom


def scroll_target_for_span(
    *,
    top: int,
    bottom: int,
    viewport: ViewportGeometry,
    top_align: bool = False,
) -> int | None:
    viewport_height = max(1, viewport.viewport_height)
    current_top = viewport.scroll_y + viewport.dock_header_height
    current_bottom = current_top + viewport_height

    if top_align:
        return _clamp_scroll_y(top - viewport.dock_header_height, viewport.max_scroll_y)
    if top < current_top:
        return _clamp_scroll_y(top - viewport.dock_header_height, viewport.max_scroll_y)
    if bottom > current_bottom:
        return _clamp_scroll_y(bottom - viewport_height, viewport.max_scroll_y)
    return None


def _clamp_scroll_y(target: int, max_scroll_y: int) -> int:
    return min(max(0, target), max(0, max_scroll_y))
