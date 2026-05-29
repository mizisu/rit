"""Virtual window management for large diff rendering."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from textual.widgets import Static
from textual.containers import VerticalScroll

from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_geometry as _geometry


if TYPE_CHECKING:
    pass

_RENDER_REQUEST_CONTEXT: ContextVar[int | None] = ContextVar(
    "diff_view_render_request", default=None
)


def _has_custom_virtual_setting(view, name: str) -> bool:
    return name in view.__dict__


def _effective_virtual_window_radius(view) -> int:
    if _has_custom_virtual_setting(view, "VIRTUAL_WINDOW_RADIUS"):
        return max(1, int(view.VIRTUAL_WINDOW_RADIUS))

    viewport_height = view.scrollable_content_region.height
    if viewport_height <= 0:
        return max(1, int(view.VIRTUAL_WINDOW_RADIUS))

    window_rows_multiplier = (
        view.COMPLEX_DIFF_WINDOW_ROWS_MULTIPLIER
        if view._comparison_heavy_ratio() >= view.COMPLEX_DIFF_RATIO_THRESHOLD
        else view.DEFAULT_WINDOW_ROWS_MULTIPLIER
    )
    average_line_height = max(1.0, view._average_render_line_height())
    dynamic_radius = max(
        view.MIN_DYNAMIC_WINDOW_RADIUS,
        int((viewport_height * window_rows_multiplier) / average_line_height),
    )
    return min(int(type(view).VIRTUAL_WINDOW_RADIUS), dynamic_radius)


def _effective_virtual_window_shift_margin(view) -> int:
    if _has_custom_virtual_setting(view, "VIRTUAL_WINDOW_SHIFT_MARGIN"):
        return max(1, int(view.VIRTUAL_WINDOW_SHIFT_MARGIN))
    return max(
        1,
        _effective_virtual_window_radius(view) // view.DYNAMIC_WINDOW_SHIFT_DIVISOR,
    )


def _rebuild_virtual_layout(view) -> None:
    geometry = _geometry.build_diff_geometry(
        view._diff,
        split=view.split,
        extra_heights_by_line=_extra_heights_by_line(view),
        inline_editor_line_index=getattr(
            view, "_inline_comment_editor_line_index", None
        ),
        inline_editor_height=view._inline_comment_editor_height(),
    )
    view._hunk_header_top_offsets = geometry.hunk_header_top_offsets
    view._line_top_offsets = geometry.line_top_offsets
    view._line_heights = geometry.line_heights
    view._line_bottom_offsets = geometry.line_bottom_offsets
    view._virtual_content_height = geometry.virtual_content_height
    view._total_line_render_height = geometry.total_line_render_height


def _extra_heights_by_line(view) -> dict[int, int]:
    from rit.ui.widgets.diff_comments import (
        estimate_pending_draft_height,
        estimate_thread_height,
    )

    extra_heights: dict[int, int] = {}

    pending_draft_map = getattr(view, "_pending_comment_drafts_by_line", {})
    for line_index, drafts in pending_draft_map.items():
        extra_heights[line_index] = extra_heights.get(line_index, 0) + sum(
            estimate_pending_draft_height(draft) for draft in drafts
        )

    comment_map = getattr(view, "_comment_threads_by_line", {})
    for line_index, threads in comment_map.items():
        extra_heights[line_index] = extra_heights.get(line_index, 0) + sum(
            estimate_thread_height(thread) for thread in threads
        )

    return extra_heights


def _set_virtual_window_from_viewport(view) -> bool:
    if not view._virt.active or not view._all_lines:
        return False

    old_start = view._virt.window_start
    old_end = view._virt.window_end
    _set_virtual_window_around(view, view._viewport_center_line())
    return view._virt.window_start != old_start or view._virt.window_end != old_end


def _maybe_update_virtual_window_from_viewport(view) -> None:
    if not view._virt.active or not view.is_mounted or not view._all_lines:
        return

    center_line = view._viewport_center_line()
    margin = _effective_virtual_window_shift_margin(view)
    start = view._virt.window_start
    end = view._virt.window_end

    if not (center_line < start + margin or center_line > end - margin):
        return

    if view._virt.render_pending:
        view._virt.coalesced_center = center_line
        return

    view._virt.coalesced_center = None
    _set_virtual_window_around(view, center_line)
    view._virt.render_pending = True
    view.run_worker(
        _run_virtual_window_render_for_request(view, view._render_request_token),
        exclusive=True,
        name="diff-virtual-window-scroll-shift",
    )


def _configure_virtual_window(view) -> None:
    total_lines = len(view._all_lines)
    view._virt.active = total_lines > view.VIRTUALIZE_LINE_THRESHOLD
    view._virt.render_pending = False

    if total_lines == 0:
        view._virt.window_start = 0
        view._virt.window_end = -1
        return

    if not view._virt.active:
        view._virt.window_start = 0
        view._virt.window_end = total_lines - 1
        return

    if view.is_mounted and view.scroll_y > 0:
        _set_virtual_window_from_viewport(view)
        return

    _set_virtual_window_around(view, view.cursor_line)


def _set_virtual_window_around(view, center_line: int) -> None:
    total_lines = len(view._all_lines)
    if total_lines == 0:
        view._virt.window_start = 0
        view._virt.window_end = -1
        return

    radius = _effective_virtual_window_radius(view)
    start = max(0, center_line - radius)
    end = min(total_lines - 1, center_line + radius)

    target_window_size = radius * 2 + 1
    current_window_size = end - start + 1

    if current_window_size < target_window_size:
        deficit = target_window_size - current_window_size
        grow_down = min(deficit, total_lines - 1 - end)
        end += grow_down
        deficit -= grow_down
        if deficit > 0:
            start = max(0, start - deficit)

    view._virt.window_start = start
    view._virt.window_end = end


def _maybe_update_virtual_window(view, line_index: int) -> None:
    if not view._virt.active or view._virt.render_pending:
        return

    margin = _effective_virtual_window_shift_margin(view)
    start = view._virt.window_start
    end = view._virt.window_end

    if line_index < start + margin or line_index > end - margin:
        _set_virtual_window_around(view, line_index)
        view._virt.cursor_shift_pending = True
        view._virt.render_pending = True
        view.run_worker(
            _run_virtual_window_render_for_request(view, view._render_request_token),
            exclusive=True,
            name="diff-virtual-window-shift",
        )


def _virtual_top_buffer_height(view, window_start: int, window_end: int) -> int:
    return _geometry.virtual_top_buffer_height(
        total_lines=len(view._all_lines),
        window_start=window_start,
        window_end=window_end,
        hunk_index_by_line=view._hunk_index_by_line,
        hunk_line_ranges=view._hunk_line_ranges,
        hunk_header_top_offsets=view._hunk_header_top_offsets,
        line_top_offsets=view._line_top_offsets,
    )


def _virtual_bottom_buffer_height(view, window_end: int) -> int:
    return _geometry.virtual_bottom_buffer_height(
        total_lines=len(view._all_lines),
        window_end=window_end,
        virtual_content_height=view._virtual_content_height,
        line_bottom_offsets=view._line_bottom_offsets,
    )


@staticmethod
def _set_virtual_buffer_height(view, widget: Static, height: int) -> None:
    widget.styles.height = max(1, height)


async def _remove_virtualized_lines(
    view,
    start: int,
    end: int,
    *,
    preserve_start: int | None = None,
    preserve_end: int | None = None,
) -> list[tuple[int, int]]:
    if start > end:
        return []

    removed_blocks: set[int] = set()
    repair_ranges: list[tuple[int, int]] = []

    comment_widgets_map = getattr(view, "_comment_widgets_by_line", {})
    comment_layout_widgets_map = getattr(view, "_comment_layout_widgets_by_line", {})
    pending_draft_widgets_map = getattr(view, "_pending_comment_widgets_by_line", {})
    pending_draft_layout_widgets_map = getattr(
        view, "_pending_comment_layout_widgets_by_line", {}
    )
    inline_editor_line = getattr(view, "_inline_comment_editor_line_index", None)
    inline_editor_widget = getattr(view, "_inline_comment_editor_widget", None)
    inline_editor_layout_widget = getattr(
        view, "_inline_comment_editor_layout_widget", None
    )

    for line_idx in range(start, end + 1):
        comment_layout_widgets = comment_layout_widgets_map.pop(line_idx, [])
        if comment_layout_widgets:
            for cw in comment_layout_widgets:
                await cw.remove()
            comment_widgets_map.pop(line_idx, None)
        else:
            for cw in comment_widgets_map.pop(line_idx, []):
                await cw.remove()

        pending_draft_layout_widgets = pending_draft_layout_widgets_map.pop(
            line_idx, []
        )
        if pending_draft_layout_widgets:
            for dw in pending_draft_layout_widgets:
                await dw.remove()
            pending_draft_widgets_map.pop(line_idx, None)
        else:
            for dw in pending_draft_widgets_map.pop(line_idx, []):
                await dw.remove()

        if line_idx == inline_editor_line and inline_editor_widget is not None:
            if inline_editor_layout_widget is not None:
                await inline_editor_layout_widget.remove()
                view._inline_comment_editor_layout_widget = None
            else:
                await inline_editor_widget.remove()
            view._inline_comment_editor_widget = None

        block = view._unified_blocks_by_line.get(line_idx)
        if block is None:
            block = view._split_blocks_by_line.get(line_idx)
        if block is not None:
            block_id = id(block)
            if block_id not in removed_blocks:
                removed_blocks.add(block_id)
                if preserve_start is not None and preserve_end is not None:
                    block_start = block.line_indices[0]
                    block_end = block.line_indices[-1]
                    overlap_start = max(block_start, preserve_start)
                    overlap_end = min(block_end, preserve_end)
                    if overlap_start <= overlap_end:
                        repair_ranges.append((overlap_start, overlap_end))
                await block.remove()
            for block_line_idx in block.line_indices:
                view._code_widgets_by_line.pop(block_line_idx, None)
                view._unregister_line_widgets(block_line_idx)
            continue

        line_widget = view._get_line_container(line_idx)
        if line_widget is not None:
            await line_widget.remove()

        view._code_widgets_by_line.pop(line_idx, None)
        view._unregister_line_widgets(line_idx)

    return view._merge_line_ranges(repair_ranges)


async def _clear_virtual_hunk_headers(view) -> None:
    for hunk_index, header_widget in list(view._hunk_header_widgets.items()):
        await header_widget.remove()
        view._hunk_header_widgets.pop(hunk_index, None)


async def _remove_stale_virtual_hunk_headers(
    view,
    window_start: int,
    window_end: int,
) -> None:
    for hunk_index in list(view._hunk_header_widgets):
        if view._should_render_hunk_header(hunk_index, window_start, window_end):
            continue
        header_widget = view._get_hunk_header_widget(hunk_index)
        if header_widget is not None:
            await header_widget.remove()
            view._hunk_header_widgets.pop(hunk_index, None)


async def _sync_visible_virtual_hunk_headers(
    view,
    container: VerticalScroll,
    window_start: int,
    window_end: int,
) -> None:
    if view._diff is None:
        return

    for hunk_index, hunk in enumerate(view._diff.hunks):
        if not view._should_render_hunk_header(hunk_index, window_start, window_end):
            continue
        if view._get_hunk_header_widget(hunk_index) is not None:
            continue

        hunk_header = (
            f"@@ -{hunk.old_start},{hunk.old_count} "
            f"+{hunk.new_start},{hunk.new_count} @@"
        )
        if hunk.header:
            hunk_header += f" {hunk.header}"

        header_widget = view._create_hunk_header_widget(
            hunk_index=hunk_index,
            hunk_header=hunk_header,
        )
        _, hunk_start, _ = view._hunk_line_ranges[hunk_index]
        anchor = view._get_line_container(hunk_start)
        if anchor is not None:
            container.mount(header_widget, before=anchor)
        elif view._virt.bottom_buffer is not None:
            container.mount(header_widget, before=view._virt.bottom_buffer)
        else:
            container.mount(header_widget)
        view._register_hunk_header_widget(hunk_index, header_widget)


async def _sync_virtual_buffers(
    view,
    container: VerticalScroll,
    window_start: int,
    window_end: int,
) -> None:
    top_height = _virtual_top_buffer_height(view, window_start, window_end)
    bottom_height = _virtual_bottom_buffer_height(view, window_end)

    top_buffer = view._virt.top_buffer
    if top_height > 0:
        if isinstance(top_buffer, Static):
            _set_virtual_buffer_height(view, top_buffer, top_height)
        else:
            widget = Static(
                "",
                classes="placeholder -virtual-buffer",
                id="virtual-buffer-top",
            )
            _set_virtual_buffer_height(view, widget, top_height)
            first_child = container.children[0] if container.children else None
            if first_child is not None:
                container.mount(widget, before=first_child)
            else:
                container.mount(widget)
            view._virt.top_buffer = widget
    elif top_buffer is not None:
        await top_buffer.remove()
        view._virt.top_buffer = None

    bottom_buffer = view._virt.bottom_buffer
    if bottom_height > 0:
        if isinstance(bottom_buffer, Static):
            _set_virtual_buffer_height(view, bottom_buffer, bottom_height)
        else:
            widget = Static(
                "",
                classes="placeholder -virtual-buffer",
                id="virtual-buffer-bottom",
            )
            _set_virtual_buffer_height(view, widget, bottom_height)
            container.mount(widget)
            view._virt.bottom_buffer = widget
    elif bottom_buffer is not None:
        await bottom_buffer.remove()
        view._virt.bottom_buffer = None


def _mount_virtualized_lines_at_bottom(
    view,
    container: VerticalScroll,
    start: int,
    end: int,
) -> None:
    if view._diff is None or start > end:
        return

    bottom_buffer = view._virt.bottom_buffer

    for hunk in view._diff.hunks:
        lines = [line for line in hunk.lines if start <= line.line_index <= end]
        if not lines:
            continue

        if view.split:
            view._mount_split_lines(container, lines, before=bottom_buffer)
        else:
            view._mount_unified_lines(container, lines, before=bottom_buffer)


def _mount_virtualized_lines_at_top(
    view,
    container: VerticalScroll,
    start: int,
    end: int,
) -> None:
    if view._diff is None or start > end:
        return

    anchor = None
    for child in container.children:
        if child.id == "virtual-buffer-top":
            continue
        anchor = child
        break

    for hunk in view._diff.hunks:
        lines = [line for line in hunk.lines if start <= line.line_index <= end]
        if not lines:
            continue

        if view.split:
            view._mount_split_lines(container, lines, before=anchor)
        else:
            view._mount_unified_lines(container, lines, before=anchor)


def _mount_virtualized_ranges_at_top(
    view,
    container: VerticalScroll,
    ranges: list[tuple[int, int]],
) -> None:
    for start, end in sorted(ranges, reverse=True):
        _mount_virtualized_lines_at_top(view, container, start, end)


def _mount_virtualized_ranges_at_bottom(
    view,
    container: VerticalScroll,
    ranges: list[tuple[int, int]],
) -> None:
    for start, end in sorted(ranges):
        _mount_virtualized_lines_at_bottom(view, container, start, end)


async def _try_shift_virtual_window_incremental(view) -> bool:
    if not view._virt.active or view._diff is None or not view.is_mounted:
        return False

    grouped_blocks_active = _blocks._should_use_unified_block_renderer(
        view
    ) or _blocks._should_use_split_block_renderer(view)

    old_start = view._virt.rendered_start
    old_end = view._virt.rendered_end
    new_start = view._virt.window_start
    new_end = view._virt.window_end

    if old_end < old_start or new_end < new_start:
        return False

    content = view.query_one("#diff-content", VerticalScroll)

    if grouped_blocks_active:
        # Large jumps or non-monotonic shifts still remount only the current
        # visible grouped window inside the existing container.
        if new_start > old_end + 1 or new_end < old_start - 1:
            await view._remount_grouped_visible_window(
                content,
                old_start,
                old_end,
                new_start,
                new_end,
            )
            view._virt.rendered_start = new_start
            view._virt.rendered_end = new_end
            view._visual_selection_specs = {}
            return True

    # Downward shift (append bottom, drop top)
    if new_start >= old_start and new_end >= old_end:
        # No overlap (large jump) -> fallback to full render.
        if new_start > old_end + 1:
            return False

        dropped_start = old_start
        dropped_end = min(old_end, new_start - 1)
        preserve_start = new_start
        preserve_end = old_end
        repair_ranges = await _remove_virtualized_lines(
            view,
            dropped_start,
            dropped_end,
            preserve_start=preserve_start if grouped_blocks_active else None,
            preserve_end=preserve_end if grouped_blocks_active else None,
        )

        await _remove_stale_virtual_hunk_headers(view, new_start, new_end)

        if grouped_blocks_active and repair_ranges:
            _mount_virtualized_ranges_at_top(view, content, repair_ranges)

        added_start = max(old_end + 1, new_start)
        added_end = new_end
        _mount_virtualized_lines_at_bottom(view, content, added_start, added_end)
        await _sync_visible_virtual_hunk_headers(view, content, new_start, new_end)

        await _sync_virtual_buffers(view, content, new_start, new_end)

        view._virt.rendered_start = new_start
        view._virt.rendered_end = new_end
        view._visual_selection_specs = {}
        return True

    # Upward shift (prepend top, drop bottom)
    if new_start <= old_start and new_end <= old_end:
        # No overlap (large jump) -> fallback to full render.
        if new_end < old_start - 1:
            return False

        dropped_start = max(old_start, new_end + 1)
        dropped_end = old_end
        preserve_start = old_start
        preserve_end = new_end
        repair_ranges = await _remove_virtualized_lines(
            view,
            dropped_start,
            dropped_end,
            preserve_start=preserve_start if grouped_blocks_active else None,
            preserve_end=preserve_end if grouped_blocks_active else None,
        )

        await _remove_stale_virtual_hunk_headers(view, new_start, new_end)

        added_start = new_start
        added_end = min(old_start - 1, new_end)
        _mount_virtualized_lines_at_top(view, content, added_start, added_end)
        if grouped_blocks_active and repair_ranges:
            _mount_virtualized_ranges_at_bottom(view, content, repair_ranges)
        await _sync_visible_virtual_hunk_headers(view, content, new_start, new_end)

        await _sync_virtual_buffers(view, content, new_start, new_end)

        view._virt.rendered_start = new_start
        view._virt.rendered_end = new_end
        view._visual_selection_specs = {}
        return True

    return False


def _reveal_cursor_after_virtual_render(view, request_token: int) -> None:
    if not view._is_current_render_request(request_token):
        return
    from rit.ui.widgets import diff_cursor as _cursor

    _cursor._scroll_to_cursor(view)
    _cursor._scroll_to_cursor_horizontal(view)
    _cursor._flush_cursor_ui_now_if_safe(view)


async def _run_virtual_window_render_for_request(view, request_token: int) -> None:
    token = _RENDER_REQUEST_CONTEXT.set(request_token)
    try:
        await _render_virtual_window_and_finalize(view)
    finally:
        _RENDER_REQUEST_CONTEXT.reset(token)


async def _render_virtual_window_and_finalize(view) -> None:
    request_token = _RENDER_REQUEST_CONTEXT.get()
    if request_token is None:
        request_token = view._render_request_token
    if not view._is_current_render_request(request_token):
        return

    try:
        updated = await _try_shift_virtual_window_incremental(view)
        if not view._is_current_render_request(request_token):
            return

        if updated:
            view.call_after_refresh(
                lambda: view._finalize_render_state_if_current(request_token)
            )
        else:
            await view._render_diff()
    finally:
        if view._is_current_render_request(request_token):
            view._virt.render_pending = False

    if not view._is_current_render_request(request_token):
        return

    cursor_driven = view._virt.cursor_shift_pending
    view._virt.cursor_shift_pending = False
    queued_center = view._virt.coalesced_center
    view._virt.coalesced_center = None
    if cursor_driven:
        view.call_after_refresh(
            lambda: _reveal_cursor_after_virtual_render(view, request_token)
        )
        return
    if queued_center is None or not view._virt.active or not view.is_mounted:
        return

    margin = _effective_virtual_window_shift_margin(view)
    if not (
        queued_center < view._virt.window_start + margin
        or queued_center > view._virt.window_end - margin
    ):
        return

    _set_virtual_window_around(view, queued_center)
    view._virt.render_pending = True
    view.run_worker(
        _run_virtual_window_render_for_request(view, view._render_request_token),
        exclusive=True,
        name="diff-virtual-window-scroll-shift",
    )


def _render_virtual_window(view, container: VerticalScroll) -> None:
    if view._diff is None:
        return

    total_lines = len(view._all_lines)
    if total_lines == 0:
        return

    start = max(0, view._virt.window_start)
    end = min(total_lines - 1, view._virt.window_end)

    top_buffer_height = _virtual_top_buffer_height(view, start, end)
    if top_buffer_height > 0:
        top_buffer = Static(
            "",
            classes="placeholder -virtual-buffer",
            id="virtual-buffer-top",
        )
        _set_virtual_buffer_height(view, top_buffer, top_buffer_height)
        container.mount(top_buffer)
        view._virt.top_buffer = top_buffer

    for hunk_index, hunk in enumerate(view._diff.hunks):
        if hunk_index < len(view._hunk_line_ranges):
            _, hunk_start, hunk_end = view._hunk_line_ranges[hunk_index]
            if hunk_end < start or hunk_start > end:
                continue

        view._render_hunk(
            container,
            hunk,
            hunk_index=hunk_index,
            window_start=start,
            window_end=end,
            show_header=view._should_render_hunk_header(hunk_index, start, end),
        )

    bottom_buffer_height = _virtual_bottom_buffer_height(view, end)
    if bottom_buffer_height > 0:
        bottom_buffer = Static(
            "",
            classes="placeholder -virtual-buffer",
            id="virtual-buffer-bottom",
        )
        _set_virtual_buffer_height(view, bottom_buffer, bottom_buffer_height)
        container.mount(bottom_buffer)
        view._virt.bottom_buffer = bottom_buffer
