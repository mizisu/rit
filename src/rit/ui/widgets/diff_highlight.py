"""Syntax highlighting for DiffView (async background processing)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from textual import work

from rit.core.highlighting import (
    highlight_lines_for_diff,
    highlight_lines_for_diff_range,
    prewarm_highlighter,
)
from rit.core.types import DiffLine, FileDiff

from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_virtual as _virtual

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


async def _prewarm_highlighter(view) -> None:
    await asyncio.to_thread(prewarm_highlighter)


def _effective_window_highlight_buffer(view) -> int:
    if _virtual._has_custom_virtual_setting(view, "WINDOW_HIGHLIGHT_BUFFER"):
        return max(0, int(view.WINDOW_HIGHLIGHT_BUFFER))
    return max(12, _virtual._effective_virtual_window_radius(view) // 2)


def _highlight_cache_key(
    view, diff: FileDiff, use_word_diff: bool | None = None
) -> tuple[int, bool]:
    return (
        id(diff),
        view.word_diff_enabled if use_word_diff is None else use_word_diff,
    )


def _has_highlighted_diff(
    view, diff: FileDiff, *, use_word_diff: bool | None = None
) -> bool:
    return _highlight_cache_key(view, diff, use_word_diff) in view._highlight_cache


def _clear_highlighted_content(view, diff: FileDiff) -> None:
    for hunk in diff.hunks:
        for line in hunk.lines:
            line.highlighted_old_content = None
            line.highlighted_new_content = None
    view._invalidate_base_code_content_cache()


def _highlight_diff_sync(
    view, diff: FileDiff, *, use_word_diff: bool | None = None
) -> None:
    include_word_diff = (
        view.word_diff_enabled if use_word_diff is None else use_word_diff
    )
    if _has_highlighted_diff(view, diff, use_word_diff=include_word_diff):
        return

    highlight_lines_for_diff(diff, include_word_diff=include_word_diff)
    view._invalidate_base_code_content_cache()
    view._highlight_cache.add(_highlight_cache_key(view, diff, include_word_diff))


def _queue_highlight_diff(view, filename: str, diff: FileDiff) -> None:
    view._highlight_request_token += 1
    request_token = view._highlight_request_token
    use_word_diff = view.word_diff_enabled
    view._queued_full_highlight = (filename, diff, use_word_diff, request_token)

    if view._full_highlight_worker_active:
        return

    view._full_highlight_worker_active = True
    view.run_worker(
        _drain_queued_full_highlights(view),
        exclusive=False,
        name="diff-highlight",
    )


async def _drain_queued_full_highlights(view) -> None:
    try:
        while True:
            request = view._queued_full_highlight
            view._queued_full_highlight = None
            if request is None:
                return

            filename, diff, use_word_diff, request_token = request
            await _highlight_diff_async(
                view,
                filename,
                diff,
                request_token=request_token,
                use_word_diff=use_word_diff,
            )
    finally:
        view._full_highlight_worker_active = False


async def _highlight_diff_async(
    view,
    filename: str,
    diff: FileDiff,
    *,
    request_token: int,
    use_word_diff: bool,
) -> None:
    await asyncio.to_thread(
        highlight_lines_for_diff,
        diff,
        include_word_diff=use_word_diff,
    )
    view._highlight_cache.add(_highlight_cache_key(view, diff, use_word_diff))

    if request_token != view._highlight_request_token:
        return
    if view.current_file != filename or view._diff is not diff:
        return
    if view.word_diff_enabled != use_word_diff:
        return

    rendered_start, rendered_end = view._get_rendered_line_bounds()
    if rendered_end < rendered_start:
        return

    _refresh_rendered_highlight_range(view, rendered_start, rendered_end)


def _should_use_windowed_highlight_strategy(view) -> bool:
    return view._virtualized or len(view._all_lines) >= view.BLOCK_RENDER_LINE_THRESHOLD


def _use_windowed_highlight_strategy(view, diff: FileDiff | None = None) -> bool:
    if diff is not None and _has_highlighted_diff(view, diff):
        return False
    return _should_use_windowed_highlight_strategy(view)


@staticmethod
def _line_has_highlight(view, line: DiffLine) -> bool:
    if line.old_content and line.highlighted_old_content is None:
        return False
    if line.new_content and line.highlighted_new_content is None:
        return False
    return True


def _current_highlight_window(view) -> tuple[int, int]:
    if not view._all_lines:
        return 0, -1

    start, end = view._get_rendered_line_bounds()
    if end < start:
        return 0, -1

    buffer = _effective_window_highlight_buffer(view)

    if view._virtualized:
        return (
            max(0, start - buffer),
            min(len(view._all_lines) - 1, end + buffer),
        )

    if _should_use_windowed_highlight_strategy(view):
        viewport_height = max(1, view.scrollable_content_region.height)
        viewport_top = int(view.scroll_y)
        viewport_bottom = max(viewport_top, viewport_top + viewport_height - 1)
        visible_start = view._line_index_at_vertical_offset(viewport_top)
        visible_end = view._line_index_at_vertical_offset(viewport_bottom)
        buffer = min(buffer, max(12, viewport_height // 2))
        return (
            max(0, visible_start - buffer),
            min(len(view._all_lines) - 1, visible_end + buffer),
        )

    return start, end


def _is_highlight_range_ready(view, start: int, end: int) -> bool:
    if start > end:
        return True

    for line_index in range(start, end + 1):
        if line_index >= len(view._all_lines):
            break
        if not _line_has_highlight(view, view._all_lines[line_index]):
            return False
    return True


def _ensure_visible_highlight(view) -> None:
    if view._diff is None or view.current_file is None:
        return
    if not _use_windowed_highlight_strategy(view, view._diff):
        return

    start, end = _current_highlight_window(view)
    if start > end or _is_highlight_range_ready(view, start, end):
        return

    _queue_highlight_diff_range(view, view.current_file, view._diff, start, end)


def _queue_highlight_diff_range(
    view,
    filename: str,
    diff: FileDiff,
    start_line: int,
    end_line: int,
) -> None:
    use_word_diff = view.word_diff_enabled
    request_signature = (start_line, end_line, use_word_diff)
    if view._window_highlight_inflight == request_signature:
        return

    queued_request = view._queued_window_highlight
    if queued_request is not None:
        (
            queued_filename,
            queued_diff,
            queued_start,
            queued_end,
            queued_use_word_diff,
            _queued_token,
        ) = queued_request
        if (
            queued_filename == filename
            and queued_diff is diff
            and queued_start == start_line
            and queued_end == end_line
            and queued_use_word_diff == use_word_diff
        ):
            return

    view._highlight_request_token += 1
    request_token = view._highlight_request_token
    view._queued_window_highlight = (
        filename,
        diff,
        start_line,
        end_line,
        use_word_diff,
        request_token,
    )

    if view._window_highlight_worker_active:
        return

    view._window_highlight_worker_active = True
    view.run_worker(
        _drain_queued_window_highlights(view),
        exclusive=False,
        name="diff-highlight-window",
    )


async def _drain_queued_window_highlights(view) -> None:
    try:
        while True:
            request = view._queued_window_highlight
            view._queued_window_highlight = None
            if request is None:
                return

            (
                filename,
                diff,
                start_line,
                end_line,
                use_word_diff,
                request_token,
            ) = request
            view._window_highlight_inflight = (
                start_line,
                end_line,
                use_word_diff,
            )
            await _highlight_diff_range_async(
                view,
                filename,
                diff,
                start_line=start_line,
                end_line=end_line,
                request_token=request_token,
                use_word_diff=use_word_diff,
            )
    finally:
        view._window_highlight_worker_active = False


async def _highlight_diff_range_async(
    view,
    filename: str,
    diff: FileDiff,
    *,
    start_line: int,
    end_line: int,
    request_token: int,
    use_word_diff: bool,
) -> None:
    try:
        await asyncio.to_thread(
            highlight_lines_for_diff_range,
            diff,
            start_line,
            end_line,
            include_word_diff=use_word_diff,
        )
    finally:
        if view._window_highlight_inflight == (
            start_line,
            end_line,
            use_word_diff,
        ):
            view._window_highlight_inflight = None

    if request_token != view._highlight_request_token:
        return
    if view.current_file != filename or view._diff is not diff:
        return
    if view.word_diff_enabled != use_word_diff:
        return

    rendered_start, rendered_end = view._get_rendered_line_bounds()
    refresh_start = max(start_line, rendered_start)
    refresh_end = min(end_line, rendered_end)
    if refresh_start > refresh_end:
        return

    _refresh_rendered_highlight_range(view, refresh_start, refresh_end)


def _refresh_rendered_highlight_range(view, start_line: int, end_line: int) -> None:
    if not view.is_mounted or start_line > end_line:
        return

    dirty_lines = {
        line_idx
        for line_idx in range(start_line, end_line + 1)
        if view._is_line_rendered(line_idx)
    }
    if not dirty_lines:
        return

    view._invalidate_base_code_content_cache(dirty_lines)
    _blocks._refresh_grouped_blocks_for_lines(view, dirty_lines)

    for line_idx in sorted(dirty_lines):
        if (
            line_idx in view._unified_blocks_by_line
            or line_idx in view._split_blocks_by_line
        ):
            continue
        _blocks._refresh_non_block_line_content(view, line_idx)
