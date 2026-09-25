"""Virtual window management for large diff rendering."""

from __future__ import annotations

import asyncio
from bisect import bisect_left, bisect_right
from collections.abc import Awaitable, Iterator, Sequence
from contextlib import ExitStack
from contextvars import ContextVar
from time import monotonic
from typing import TYPE_CHECKING, overload

from textual.constants import MAX_FPS
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from rit.ui.widgets import diff_folding as _folding
from rit.ui.widgets import diff_geometry as _geometry
from rit.ui.widgets.diff_types import SplitDiffBlock, UnifiedDiffBlock, VirtualState

if TYPE_CHECKING:
    from textual.screen import Screen

    from rit.core.types import DiffLine
    from rit.state.models import PendingReviewComment
    from rit.ui.widgets.diff_view import DiffView

__all__ = ()


_RENDER_REQUEST_CONTEXT: ContextVar[int | None] = ContextVar(
    "diff_view_render_request", default=None
)


class _VirtualLineWindow(Sequence["DiffLine"]):
    def __init__(self, lines: Sequence[DiffLine], start: int, stop: int) -> None:
        self._lines = lines
        self._start = start
        self._stop = stop

    def __len__(self) -> int:
        return max(0, self._stop - self._start)

    def __iter__(self) -> Iterator[DiffLine]:
        for index in range(self._start, self._stop):
            yield self._lines[index]

    @overload
    def __getitem__(self, index: int) -> DiffLine: ...

    @overload
    def __getitem__(self, index: slice) -> list[DiffLine]: ...

    def __getitem__(self, index: int | slice) -> DiffLine | list[DiffLine]:
        if isinstance(index, slice):
            return [self[line_index] for line_index in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self._lines[self._start + index]


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
        line_count=len(view._all_lines),
        extra_heights_by_line=_extra_heights_by_line(view),
        extra_heights_by_hunk=_extra_heights_by_hunk(view),
        inline_editor_line_index=getattr(
            view, "_inline_comment_editor_line_index", None
        ),
        inline_editor_height=view._inline_comment_editor_height(),
        file_editor_hunk_index=getattr(view, "_file_comment_editor_hunk_index", None),
        file_editor_height=view._file_comment_editor_height(),
    )
    view._hunk_header_top_offsets = geometry.hunk_header_top_offsets
    view._line_top_offsets = geometry.line_top_offsets
    view._line_heights = geometry.line_heights
    view._line_bottom_offsets = geometry.line_bottom_offsets
    view._virtual_content_height = geometry.virtual_content_height
    view._total_line_render_height = geometry.total_line_render_height


def _extra_heights_by_line(view) -> dict[int, int]:
    from rit.ui.widgets.diff_comments import (
        COLLAPSED_PENDING_DRAFT_HEIGHT,
        estimate_pending_draft_height,
        estimate_thread_height,
        pending_draft_is_collapsed,
    )

    extra_heights: dict[int, int] = {}

    def draft_height(draft: PendingReviewComment) -> int:
        if pending_draft_is_collapsed(view, draft):
            return COLLAPSED_PENDING_DRAFT_HEIGHT
        return estimate_pending_draft_height(draft)

    pending_draft_map = getattr(view, "_pending_comment_drafts_by_line", {})
    for line_index, drafts in pending_draft_map.items():
        height = (
            draft_height(drafts[0])
            if len(drafts) == 1
            else sum(draft_height(draft) for draft in drafts)
        )
        extra_heights[line_index] = extra_heights.get(line_index, 0) + height

    comment_map = getattr(view, "_comment_threads_by_line", {})
    for line_index, threads in comment_map.items():
        height = (
            estimate_thread_height(threads[0])
            if len(threads) == 1
            else sum(estimate_thread_height(thread) for thread in threads)
        )
        extra_heights[line_index] = extra_heights.get(line_index, 0) + height

    return extra_heights


def _extra_heights_by_hunk(view) -> dict[int, int]:
    from rit.ui.widgets.diff_comments import (
        COLLAPSED_PENDING_DRAFT_HEIGHT,
        estimate_pending_draft_height,
        estimate_thread_height,
        pending_draft_is_collapsed,
    )

    if view._diff is None:
        return {}

    draft_map = getattr(view, "_pending_file_comment_drafts_by_path", {})
    thread_map = getattr(view, "_file_comment_threads_by_path", {})
    extra_heights: dict[int, int] = {}
    for hunk_index, hunk in enumerate(view._diff.hunks):
        if not hunk.starts_file:
            continue
        path = hunk.file_path or view._file_path_for_hunk(hunk_index)
        if path is None:
            continue

        height = 0
        for draft in draft_map.get(path, ()):
            height += (
                COLLAPSED_PENDING_DRAFT_HEIGHT
                if pending_draft_is_collapsed(view, draft)
                else estimate_pending_draft_height(draft)
            )
        for thread in thread_map.get(path, ()):
            height += estimate_thread_height(thread)
        if height:
            extra_heights[hunk_index] = height
    return extra_heights


def _viewport_line_range(
    view: DiffView, scroll_y: float | None = None
) -> tuple[int, int]:
    top = int(view.scroll_y if scroll_y is None else scroll_y)
    bottom = top + max(1, view.scrollable_content_region.height) - 1
    return (
        view._line_index_at_vertical_offset(top),
        view._line_index_at_vertical_offset(bottom),
    )


def _set_virtual_window_from_viewport(view) -> bool:
    if not view._virt.active or not view._all_lines:
        return False

    old_start = view._virt.window_start
    old_end = view._virt.window_end
    first, last = _viewport_line_range(view)
    radius = _effective_virtual_window_radius(view)
    view._virt.window_start = max(0, first - radius)
    view._virt.window_end = min(len(view._all_lines) - 1, last + radius)
    return view._virt.window_start != old_start or view._virt.window_end != old_end


def _maybe_update_virtual_window_from_viewport(view) -> None:
    if not view._virt.active or not view.is_mounted or not view._all_lines:
        return

    if view._virt.suppress_next_viewport_shift:
        view._virt.suppress_next_viewport_shift = False
        return

    first, last = _viewport_line_range(view)
    margin = _effective_virtual_window_shift_margin(view)
    start = view._virt.window_start
    end = view._virt.window_end

    if not (
        (start > 0 and first < start + margin)
        or (end < len(view._all_lines) - 1 and last > end - margin)
    ):
        return

    if view._virt.render_pending:
        if not view._virt.cursor_shift_pending:
            view._virt.coalesced_center = view._viewport_center_line()
        return

    view._virt.coalesced_center = None
    if not _set_virtual_window_from_viewport(view):
        return
    view._virt.render_pending = True
    _queue_viewport_render(view)


def _queue_viewport_render(view: DiffView) -> None:
    # Reserve painting before the worker starts, and release even if it is
    # cancelled before entering its coroutine (where finally cannot run).
    batch = ExitStack()
    batch.enter_context(view.app.batch_update())
    request_token = view._render_request_token
    state = view._virt
    state.viewport_scroll_latched = (
        state.window_start > state.rendered_end
        or state.window_end < state.rendered_start
    )

    async def render() -> None:
        try:
            await _run_virtual_window_render_for_request(view, request_token)
            if (
                state.viewport_scroll_latched
                and view._virt is state
                and view._is_current_render_request(request_token)
                and not state.cursor_shift_pending
                and state.viewport_worker is worker
            ):
                state.render_pending = True
                state.viewport_frame_token = request_token
                view.refresh(layout=True)
        finally:
            batch.close()

    try:
        worker = view.run_worker(
            render,
            start=False,
            exclusive=True,
            name="diff-virtual-window-scroll-shift",
        )
        view._virt.viewport_worker = worker
        # Textual's terminal runner uses eager tasks; publish ownership before start.
        view.workers.add_worker(worker, start=True, exclusive=False)
        completion = asyncio.create_task(worker.wait())
    except BaseException:
        batch.close()
        state.viewport_scroll_latched = False
        state.render_pending = False
        raise

    def release(completed: asyncio.Task[None]) -> None:
        try:
            if not completed.cancelled():
                completed.exception()  # The Textual worker reports failures to App.
        finally:
            if view._virt.viewport_worker is worker:
                view._virt.viewport_worker = None
                if worker.is_cancelled and not view._virt.cursor_shift_pending:
                    view._virt.viewport_frame_token = None
                    view._virt.viewport_scroll_latched = False
                    view._virt.render_pending = False
            batch.close()

    completion.add_done_callback(release)


def _replace_viewport_before_frame(view: DiffView) -> bool:
    # A fast replacement can meet the next paint; after two frame periods paint
    # wins, regardless of further input. This budget is not a latency guarantee.
    state = view._virt
    if (
        state.viewport_frame_token is None
        or not view._is_current_render_request(state.viewport_frame_token)
        or monotonic() - state.viewport_painted_at >= 2 / MAX_FPS
    ):
        return False
    state.viewport_frame_token = None
    state.viewport_scroll_latched = False
    state.render_pending = False
    return True


def _viewport_frame_painted(view: DiffView, screen: Screen) -> None:
    if view.app._batch_count or screen is not view.app.screen:
        return
    state = view._virt
    state.viewport_painted_at = monotonic()
    token = state.viewport_frame_token
    if token is None:
        return
    state.viewport_frame_token = None
    _resume_viewport_scroll(view, state, token)


def _resume_viewport_scroll(
    view: DiffView, state: VirtualState, request_token: int
) -> None:
    if (
        view._virt is not state
        or not view._is_current_render_request(request_token)
        or state.cursor_shift_pending
    ):
        return
    state.render_pending = False
    state.viewport_scroll_latched = False
    view.scroll_to(
        y=view.scroll_target_y, animate=False, immediate=True, release_anchor=False
    )
    _maybe_update_virtual_window_from_viewport(view)


def _configure_virtual_window(view) -> None:
    total_lines = len(view._all_lines)
    view._virt.active = view._render_policy_line_count > view.VIRTUALIZE_LINE_THRESHOLD
    view._virt.render_pending = False
    view._virt.viewport_frame_token = None
    view._virt.viewport_scroll_latched = False

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
    if not view._virt.active:
        return

    margin = _effective_virtual_window_shift_margin(view)
    start = view._virt.window_start
    end = view._virt.window_end

    if line_index < start + margin or line_index > end - margin:
        if view._virt.render_pending:
            view._virt.coalesced_center = line_index
            view._virt.cursor_shift_pending = True
            return

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


def _visible_hunk_index_range(
    view,
    window_start: int,
    window_end: int,
) -> range:
    """Return hunk indexes whose line ranges intersect the virtual window."""
    hunk_starts = view._hunk_start_line_indices
    hunk_ends = view._hunk_end_line_indices
    if window_start > window_end or not hunk_starts or not hunk_ends:
        return range(0)

    first = bisect_left(hunk_ends, window_start)
    last = bisect_right(hunk_starts, window_end) - 1
    if first > last:
        return range(0)
    return range(first, last + 1)


async def _finish_widget_removal(removal: Awaitable[None]) -> None:
    """Finish pruning even when a replacement render cancels its caller."""
    task = asyncio.ensure_future(removal)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _remove_virtual_widgets(*widgets: Widget) -> None:
    levels: list[list[Widget]] = []
    nodes = list(widgets)
    # The compositor may still hit-test the old layout until this batch finishes.
    # Explicit visibility survives detachment; inherited visibility does not.
    while nodes:
        levels.append(nodes)
        for node in nodes:
            node.visible = False
        nodes = [
            child
            for node in nodes
            for child in (*node.children, *node._get_virtual_dom())
        ]
    # Drain children before their parents so queued wheel events can still bubble.
    for level in reversed(levels):
        pending: list[asyncio.Event] = []
        for node in level:
            drained = asyncio.Event()
            if node.is_running and node.call_later(drained.set):
                pending.append(drained)
        for drained in pending:
            await drained.wait()
    if (
        widgets
        and isinstance(parent := widgets[0].parent, Widget)
        and all(widget.parent is parent for widget in widgets)
    ):
        await _finish_widget_removal(parent.remove_children(widgets))
    else:
        for widget in widgets:
            await _finish_widget_removal(widget.remove())


async def _remove_virtualized_lines(view, start: int, end: int) -> None:
    if start > end:
        return

    widgets: list[Widget] = []
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
        comments = comment_widgets_map.pop(line_idx, [])
        widgets.extend(comment_layout_widgets_map.pop(line_idx, []) or comments)
        drafts = pending_draft_widgets_map.pop(line_idx, [])
        widgets.extend(pending_draft_layout_widgets_map.pop(line_idx, []) or drafts)

        if line_idx == inline_editor_line and inline_editor_widget is not None:
            widgets.append(inline_editor_layout_widget or inline_editor_widget)
            view._inline_comment_editor_layout_widget = None
            view._inline_comment_editor_widget = None

        block = view._unified_blocks_by_line.get(line_idx)
        if block is None:
            block = view._split_blocks_by_line.get(line_idx)
        if block is not None:
            widgets.append(block)
            for block_line_idx in block.line_indices:
                view._code_widgets_by_line.pop(block_line_idx, None)
                view._unregister_line_widgets(block_line_idx)
            continue

        line_widget = view._get_line_container(line_idx)
        if line_widget is not None:
            widgets.append(line_widget)

        view._code_widgets_by_line.pop(line_idx, None)
        view._unregister_line_widgets(line_idx)

    await _remove_virtual_widgets(*widgets)


async def _remove_mounted_file_comment_editor(view, hunk_index: int) -> None:
    if view._file_comment_editor_mounted_hunk_index != hunk_index:
        return
    editor = view._file_comment_editor_widget
    if editor is not None:
        await _remove_virtual_widgets(editor)
    view._file_comment_editor_widget = None
    view._file_comment_editor_mounted_hunk_index = None


async def _remove_mounted_file_comment_annotations(view, hunk_index: int) -> None:
    widgets = view._file_comment_annotation_widgets_by_hunk.pop(hunk_index, ())
    view._pending_file_comment_widgets_by_hunk.pop(hunk_index, None)
    view._file_comment_widgets_by_hunk.pop(hunk_index, None)
    await _remove_virtual_widgets(*widgets)


async def _clear_virtual_file_headers(view) -> None:
    while view._file_header_widgets:
        hunk_index, header_widget = view._file_header_widgets.popitem()
        await _remove_virtual_widgets(header_widget)
        await _remove_mounted_file_comment_annotations(view, hunk_index)
        await _remove_mounted_file_comment_editor(view, hunk_index)


async def _clear_virtual_hunk_headers(view) -> None:
    while view._hunk_header_widgets:
        _, header_widget = view._hunk_header_widgets.popitem()
        await _remove_virtual_widgets(header_widget)


async def _remove_stale_virtual_file_headers(
    view,
    window_start: int,
    window_end: int,
) -> None:
    stale_headers = []
    for hunk_index, header_widget in view._file_header_widgets.items():
        if view._should_render_hunk_header(hunk_index, window_start, window_end):
            continue
        stale_headers.append((hunk_index, header_widget))

    await _remove_virtual_widgets(*(widget for _, widget in stale_headers))
    for hunk_index, _ in stale_headers:
        view._file_header_widgets.pop(hunk_index, None)
        await _remove_mounted_file_comment_annotations(view, hunk_index)
        await _remove_mounted_file_comment_editor(view, hunk_index)


async def _remove_stale_virtual_hunk_headers(
    view,
    window_start: int,
    window_end: int,
) -> None:
    stale_headers = []
    for hunk_index, header_widget in view._hunk_header_widgets.items():
        if view._should_render_hunk_header(hunk_index, window_start, window_end):
            continue
        stale_headers.append((hunk_index, header_widget))

    await _remove_virtual_widgets(*(widget for _, widget in stale_headers))
    for hunk_index, _ in stale_headers:
        view._hunk_header_widgets.pop(hunk_index, None)


def _virtual_hunk_anchor(
    view,
    hunk_index: int,
    window_start: int,
    window_end: int,
) -> Widget | None:
    _, target_start, _ = view._hunk_line_ranges[hunk_index]
    visible_hunks = _visible_hunk_index_range(
        view,
        max(window_start, target_start),
        window_end,
    )
    for candidate_index in visible_hunks:
        if candidate_index < hunk_index:
            continue
        if candidate_index != hunk_index:
            file_header = view._get_file_header_widget(candidate_index)
            if file_header is not None:
                return file_header
        hunk_header = view._get_hunk_header_widget(candidate_index)
        if hunk_header is not None:
            return hunk_header

        _, candidate_start, candidate_end = view._hunk_line_ranges[candidate_index]
        line_start = max(window_start, candidate_start)
        line_end = min(window_end, candidate_end)
        for line_index in range(line_start, line_end + 1):
            anchor = view._line_widgets_by_index.get(line_index)
            if anchor is not None:
                return anchor

    bottom_buffer = view._virt.bottom_buffer
    if bottom_buffer is not None:
        return bottom_buffer
    return None


def _mount_before_or_at_end(
    container: VerticalScroll,
    widget: Widget,
    *,
    anchor: Widget | None,
) -> None:
    if anchor is None:
        container.mount(widget)
    else:
        container.mount(widget, before=anchor)


async def _sync_visible_virtual_file_headers(
    view,
    container: VerticalScroll,
    window_start: int,
    window_end: int,
) -> None:
    from rit.ui.widgets import diff_comments as _comments

    if view._diff is None:
        return

    for hunk_index in _visible_hunk_index_range(view, window_start, window_end):
        if not (0 <= hunk_index < len(view._diff.hunks)):
            continue
        hunk = view._diff.hunks[hunk_index]
        if not hunk.starts_file:
            continue
        if not view._should_render_hunk_header(hunk_index, window_start, window_end):
            continue
        if view._get_file_header_widget(hunk_index) is not None:
            continue

        anchor = _virtual_hunk_anchor(
            view,
            hunk_index,
            window_start,
            window_end,
        )
        header_widget = view._create_file_header_widget(
            hunk_index=hunk_index,
            hunk=hunk,
        )
        _mount_before_or_at_end(container, header_widget, anchor=anchor)
        view._register_file_header_widget(hunk_index, header_widget)
        _comments.mount_file_comments_for_hunk(
            view,
            container,
            hunk_index,
            before=anchor,
        )
        view._mount_file_comment_editor(
            container,
            hunk_index,
            before=anchor,
        )


async def _sync_visible_virtual_hunk_headers(
    view,
    container: VerticalScroll,
    window_start: int,
    window_end: int,
) -> None:
    if view._diff is None:
        return
    if not view._diff.show_hunk_headers:
        return

    for hunk_index in _visible_hunk_index_range(view, window_start, window_end):
        if not (0 <= hunk_index < len(view._diff.hunks)):
            continue
        if not view._should_render_hunk_header(hunk_index, window_start, window_end):
            continue
        if view._get_hunk_header_widget(hunk_index) is not None:
            continue

        hunk = view._diff.hunks[hunk_index]
        if len(hunk.lines) == 1 and _folding.is_folded_placeholder_line(hunk.lines[0]):
            continue
        header_widget = view._create_hunk_header_widget(
            hunk_index=hunk_index,
            hunk=hunk,
        )
        anchor = _virtual_hunk_anchor(
            view,
            hunk_index,
            window_start,
            window_end,
        )
        _mount_before_or_at_end(container, header_widget, anchor=anchor)
        view._register_hunk_header_widget(hunk_index, header_widget)


async def _sync_virtual_buffers(
    view,
    container: VerticalScroll,
    window_start: int,
    window_end: int,
) -> None:
    top_height = _virtual_top_buffer_height(view, window_start, window_end)
    bottom_height = _virtual_bottom_buffer_height(view, window_end)
    container.styles.margin = (top_height, 0, bottom_height, 0)

    top_buffer = view._virt.top_buffer
    if top_height > 0:
        if top_buffer is None:
            widget = Static(
                "",
                classes="placeholder -virtual-buffer",
                id="virtual-buffer-top",
            )
            first_child = container.children[0] if container.children else None
            if first_child is not None:
                container.mount(widget, before=first_child)
            else:
                container.mount(widget)
            view._virt.top_buffer = widget
    elif top_buffer is not None:
        await _remove_virtual_widgets(top_buffer)
        view._virt.top_buffer = None

    bottom_buffer = view._virt.bottom_buffer
    if bottom_height > 0:
        if bottom_buffer is None:
            widget = Static(
                "",
                classes="placeholder -virtual-buffer",
                id="virtual-buffer-bottom",
            )
            container.mount(widget)
            view._virt.bottom_buffer = widget
    elif bottom_buffer is not None:
        await _remove_virtual_widgets(bottom_buffer)
        view._virt.bottom_buffer = None


def _iter_virtualized_line_groups(
    view,
    start: int,
    end: int,
) -> Iterator[_VirtualLineWindow]:
    if view._diff is None or start > end or not view._all_lines:
        return

    start = max(0, start)
    end = min(len(view._all_lines) - 1, end)
    if start > end:
        return

    current_hunk_index: int | None = None
    current_group_start: int | None = None
    for visible_line_index in range(start, end + 1):
        line = view._all_lines[visible_line_index]
        line_index = line.line_index
        if not (0 <= line_index < len(view._hunk_index_by_line)):
            continue

        hunk_index = view._hunk_index_by_line[line_index]
        if current_hunk_index is None or hunk_index == current_hunk_index:
            current_hunk_index = hunk_index
            if current_group_start is None:
                current_group_start = visible_line_index
            continue

        if current_group_start is not None:
            yield _VirtualLineWindow(
                view._all_lines,
                current_group_start,
                visible_line_index,
            )
        current_hunk_index = hunk_index
        current_group_start = visible_line_index

    if current_group_start is not None:
        yield _VirtualLineWindow(view._all_lines, current_group_start, end + 1)


def _mount_virtualized_lines(
    view: DiffView,
    container: VerticalScroll,
    start: int,
    end: int,
    *,
    before: Widget | None,
) -> None:
    if view._diff is None or start > end:
        return

    for lines in _iter_virtualized_line_groups(view, start, end):
        hunk_index = view._hunk_index_by_line[lines[0].line_index]
        hunk = view._diff.hunks[hunk_index]
        view._mount_hunk_lines(container, hunk, lines, before=before)


def _mount_virtualized_lines_at_bottom(
    view,
    container: VerticalScroll,
    start: int,
    end: int,
) -> None:
    _mount_virtualized_lines(
        view, container, start, end, before=view._virt.bottom_buffer
    )


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

    _mount_virtualized_lines(view, container, start, end, before=anchor)


def _display_virtual_block(
    block: UnifiedDiffBlock | SplitDiffBlock, display: bool
) -> None:
    """Exclude parked descendants from the compositor's previous hit-test map."""
    for node in block.walk_children(Widget, with_self=True):
        node.styles.visibility = None if display else "hidden"
        for child in node._get_virtual_dom():
            child.styles.visibility = None if display else "hidden"
    block.display = display


async def _reuse_virtual_code_blocks(
    view: DiffView,
    content: VerticalScroll,
    removed_ranges: tuple[tuple[int, int], tuple[int, int]],
    added_ranges: tuple[tuple[int, int], tuple[int, int]],
) -> bool:
    """Rebind outgoing code blocks without closing their input queues or children."""
    from rit.ui.widgets import diff_blocks as _blocks

    diff = view._diff
    if diff is None:
        return False
    outgoing: dict[UnifiedDiffBlock | SplitDiffBlock, None] = {}
    for start, end in removed_ranges:
        for index in range(start, end + 1):
            widget = view._line_widgets_by_index.get(index)
            if not isinstance(
                widget, (UnifiedDiffBlock, SplitDiffBlock)
            ) or not _blocks._can_render_in_unified_block(view, view._all_lines[index]):
                return False
            if widget not in outgoing and any(
                child.text_selection is not None or child is view.app.mouse_captured
                for child in widget.walk_children(Widget, with_self=True)
            ):
                return False
            outgoing[widget] = None
    if not outgoing:
        return False

    chunks: list[tuple[int, bool, list[DiffLine]]] = []
    for edge, (start, end) in enumerate(added_ranges):
        for lines in _iter_virtualized_line_groups(view, start, end):
            hunk = diff.hunks[view._hunk_index_by_line[lines[0].line_index]]
            split = view._split_for_hunk(hunk)
            eligible = (
                _blocks._can_render_in_split_block
                if split
                else _blocks._can_render_in_unified_block
            )
            if any(
                _folding.is_folded_placeholder_line(line) or not eligible(view, line)
                for line in lines
            ):
                return False
            for offset in range(0, len(lines), view.UNIFIED_BLOCK_CHUNK_SIZE):
                chunks.append(
                    (
                        edge,
                        split,
                        lines[offset : offset + view.UNIFIED_BLOCK_CHUNK_SIZE],
                    )
                )

    for block in view._virt.spare_blocks:
        if block.parent is not content or any(
            child.text_selection is not None or child is view.app.mouse_captured
            for child in block.walk_children(Widget, with_self=True)
        ):
            return False
    outgoing.update((block, None) for block in view._virt.spare_blocks)
    view._virt.spare_blocks.clear()
    unified = [block for block in outgoing if isinstance(block, UnifiedDiffBlock)]
    split_blocks = [block for block in outgoing if isinstance(block, SplitDiffBlock)]
    top_anchor = next(
        (
            child
            for child in content.children
            if child.id != "virtual-buffer-top" and child not in outgoing
        ),
        None,
    )
    for block in outgoing:
        for index in block.line_indices:
            view._unregister_line_widgets(index)
            view._code_widgets_by_line.pop(index, None)

    for edge, split, lines in chunks:
        anchor = top_anchor if edge == 0 else view._virt.bottom_buffer
        if split:
            if not split_blocks:
                _blocks._render_split_line_block(view, content, lines, before=anchor)
                continue
            block = split_blocks.pop()
            block.set_line_indices(line.line_index for line in lines)
            _blocks._refresh_split_block(view, block)
            _blocks._register_split_block(view, block, lines)
        else:
            if not unified:
                _blocks._render_unified_line_block(view, content, lines, before=anchor)
                continue
            block = unified.pop()
            block.set_line_indices(line.line_index for line in lines)
            _blocks._refresh_unified_block(view, block)
            _blocks._register_unified_block(view, block, lines)
        if not block.display:
            _display_virtual_block(block, True)
        if anchor is not None:
            content.move_child(block, before=anchor)
        else:
            content.move_child(block, after=-1)

    unused = [*unified, *split_blocks]
    limit = max(0, view.scrollable_content_region.height)
    for block in unused[:limit]:
        block.set_line_indices(())
        _display_virtual_block(block, False)
        view._virt.spare_blocks.append(block)
    await _remove_virtual_widgets(*unused[limit:])
    return True


async def _try_shift_virtual_window_incremental(view) -> bool:
    if not view._virt.active or view._diff is None or not view.is_mounted:
        return False

    old_start = view._virt.rendered_start
    old_end = view._virt.rendered_end
    new_start = view._virt.window_start
    new_end = view._virt.window_end

    if old_end < old_start or new_end < new_start:
        return False

    request_token = _RENDER_REQUEST_CONTEXT.get()
    retargeted = False
    while True:
        # Keep intersecting blocks intact so scrolling preserves their render caches.
        for blocks in (view._unified_blocks_by_line, view._split_blocks_by_line):
            if (block := blocks.get(new_start)) is not None:
                new_start = block.line_indices[0]
            if (block := blocks.get(new_end)) is not None:
                new_end = block.line_indices[-1]
        view._virt.window_start = new_start
        view._virt.window_end = new_end
        if (new_start, new_end) == (old_start, old_end) and not retargeted:
            return True

        await _remove_stale_virtual_file_headers(view, new_start, new_end)
        await _remove_stale_virtual_hunk_headers(view, new_start, new_end)
        if request_token is not None and not view._is_current_render_request(
            request_token
        ):
            return False
        if view._virt.cursor_shift_pending or view._virt.coalesced_center is None:
            break
        first, last = _viewport_line_range(view)
        if new_start <= first <= last <= new_end:
            break
        # Input can overtake us while header queues drain. Build the latest window,
        # rather than materializing a stale one and immediately throwing it away.
        view._virt.coalesced_center = None
        _set_virtual_window_from_viewport(view)
        new_start, new_end = view._virt.window_start, view._virt.window_end
        retargeted = True

    content = view.query_one("#diff-content", VerticalScroll)

    removed_ranges = (
        (old_start, min(old_end, new_start - 1)),
        (max(old_start, new_end + 1), old_end),
    )
    added_ranges = (
        (new_start, min(new_end, old_start - 1)),
        (max(new_start, old_end + 1), new_end),
    )
    reused = await _reuse_virtual_code_blocks(
        view, content, removed_ranges, added_ranges
    )
    if not reused:
        if new_start > old_end + 1 or new_end < old_start - 1:
            return False
        for start, end in removed_ranges:
            await _remove_virtualized_lines(view, start, end)

    if not reused:
        _mount_virtualized_lines_at_top(view, content, *added_ranges[0])
        _mount_virtualized_lines_at_bottom(view, content, *added_ranges[1])
    await _sync_visible_virtual_file_headers(view, content, new_start, new_end)
    await _sync_visible_virtual_hunk_headers(view, content, new_start, new_end)
    await _sync_virtual_buffers(view, content, new_start, new_end)

    view._virt.rendered_start = new_start
    view._virt.rendered_end = new_end
    view._visual_selection_specs = {}
    return True


def _reveal_cursor_after_virtual_render(view, request_token: int) -> None:
    if not view._is_current_render_request(request_token):
        return
    from rit.ui.widgets import diff_cursor as _cursor

    pending_scroll = view._virt.pending_scroll
    view._virt.pending_scroll = None
    if pending_scroll is not None:
        pending_scroll()
    _cursor._scroll_to_cursor_horizontal(view)
    _cursor._flush_cursor_ui_now_if_safe(view)
    view._virt.suppress_next_viewport_shift = True


def _complete_cursor_driven_virtual_render(view, request_token: int) -> None:
    if not view._is_current_render_request(request_token):
        return
    view._virt.viewport_frame_token = None
    view._virt.viewport_scroll_latched = False
    view._virt.render_pending = False
    _reveal_cursor_after_virtual_render(view, request_token)


async def _run_virtual_window_render_for_request(view, request_token: int) -> None:
    token = _RENDER_REQUEST_CONTEXT.set(request_token)
    try:
        async with view.batch():
            if not view._is_current_render_request(request_token):
                return
            if (
                not view._virt.cursor_shift_pending
                and view._virt.coalesced_center is not None
            ):
                _set_virtual_window_from_viewport(view)
                view._virt.coalesced_center = None
            view._capture_comment_editors()
            await _render_virtual_window_and_finalize(view)
    finally:
        _RENDER_REQUEST_CONTEXT.reset(token)


async def _render_virtual_window_and_finalize(view) -> None:
    request_token = _RENDER_REQUEST_CONTEXT.get()
    if request_token is None:
        request_token = view._render_request_token
    if not view._is_current_render_request(request_token):
        return

    if view._virt.viewport_scroll_latched and not view._virt.cursor_shift_pending:
        saved = view._suspend_scroll_virtual_window_watch
        view._suspend_scroll_virtual_window_watch = True
        try:
            view.scroll_y = view.scroll_target_y
        finally:
            view._suspend_scroll_virtual_window_watch = saved
        _set_virtual_window_from_viewport(view)

    while True:
        try:
            updated = await _try_shift_virtual_window_incremental(view)
            if not view._is_current_render_request(request_token):
                return

            if not updated:
                await view._render_diff(finalize=False)
            await view._await_content_mounts()
            view._restore_comment_editors()
        except Exception:
            if view._is_current_render_request(request_token):
                view._virt.render_pending = False
            raise

        if not view._is_current_render_request(request_token):
            return
        if (
            view._virt.cursor_shift_pending
            or view._virt.coalesced_center is None
            or not view._virt.active
            or not view.is_mounted
        ):
            break
        first, last = _viewport_line_range(view)
        if view._virt.rendered_start <= first <= last <= view._virt.rendered_end:
            break

        # A queued worker would release the batch and expose an obsolete window
        # to paint. Catch up inside this batch when input has outrun its coverage.
        view._virt.coalesced_center = None
        if not _set_virtual_window_from_viewport(view):
            break
        view._capture_comment_editors()

    # call_after_refresh also runs on Screen idle inside a batch. Finalize only
    # the completed window, not intermediate windows already overtaken by input.
    view.call_after_refresh(
        lambda: view._finalize_render_state_if_current(request_token)
    )
    cursor_driven = view._virt.cursor_shift_pending
    view._virt.cursor_shift_pending = False
    queued_center = view._virt.coalesced_center
    view._virt.coalesced_center = None
    if cursor_driven:
        if queued_center is not None and view._virt.active and view.is_mounted:
            margin = _effective_virtual_window_shift_margin(view)
            if (
                queued_center < view._virt.window_start + margin
                or queued_center > view._virt.window_end - margin
            ):
                _set_virtual_window_around(view, queued_center)
                view._virt.cursor_shift_pending = True
                view._virt.render_pending = True
                view.run_worker(
                    _run_virtual_window_render_for_request(
                        view, view._render_request_token
                    ),
                    exclusive=True,
                    name="diff-virtual-window-shift",
                )
                return

        view.call_after_refresh(
            lambda: _complete_cursor_driven_virtual_render(view, request_token)
        )
        return

    # Overlapping wheel windows must also yield when the last paint is overdue.
    if (
        view._virt.viewport_worker is not None
        and view._virt.viewport_worker.is_running
        and monotonic() - view._virt.viewport_painted_at >= 2 / MAX_FPS
    ):
        view._virt.viewport_scroll_latched = True
    view._virt.render_pending = False

    if queued_center is not None and not view._virt.viewport_scroll_latched:
        _maybe_update_virtual_window_from_viewport(view)


def _render_virtual_window(view, container: VerticalScroll) -> None:
    if view._diff is None:
        return

    total_lines = len(view._all_lines)
    if total_lines == 0:
        return

    start = max(0, view._virt.window_start)
    end = min(total_lines - 1, view._virt.window_end)

    top_buffer_height = _virtual_top_buffer_height(view, start, end)
    bottom_buffer_height = _virtual_bottom_buffer_height(view, end)
    # Tall blank widgets make Textual invalidate a dirty row for every offscreen
    # line. Margins preserve scroll geometry without document-sized paint caches.
    container.styles.margin = (top_buffer_height, 0, bottom_buffer_height, 0)
    if top_buffer_height > 0:
        top_buffer = Static(
            "",
            classes="placeholder -virtual-buffer",
            id="virtual-buffer-top",
        )
        container.mount(top_buffer)
        view._virt.top_buffer = top_buffer

    for hunk_index in _visible_hunk_index_range(view, start, end):
        if not (0 <= hunk_index < len(view._diff.hunks)):
            continue
        hunk = view._diff.hunks[hunk_index]
        view._render_hunk(
            container,
            hunk,
            hunk_index=hunk_index,
            window_start=start,
            window_end=end,
            show_header=view._should_render_hunk_header(hunk_index, start, end),
        )

    if bottom_buffer_height > 0:
        bottom_buffer = Static(
            "",
            classes="placeholder -virtual-buffer",
            id="virtual-buffer-bottom",
        )
        container.mount(bottom_buffer)
        view._virt.bottom_buffer = bottom_buffer
