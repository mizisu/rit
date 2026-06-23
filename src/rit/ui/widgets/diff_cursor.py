"""Cursor movement, scrolling, word motion, and hunk navigation for DiffView."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Literal

from textual.containers import VerticalScroll
from textual.geometry import Size

from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_geometry as _geometry
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_cursor_update as _cursor_update
from rit.ui.widgets import diff_virtual as _virtual
from rit.ui.widgets import diff_visual_mode as _visual_mode
from rit.ui.widgets import diff_word_motion as _word_motion
from rit.ui.widgets.diff_types import RenderedRow, SplitDiffBlock, UnifiedDiffBlock

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


__all__ = ()


# ---------------------------------------------------------------------------
# Count prefix
# ---------------------------------------------------------------------------


def _consume_count(view: DiffView) -> int:
    if view._cursor_ui.pending_count:
        count = int(view._cursor_ui.pending_count)
        view._cursor_ui.pending_count = ""
        return max(1, count)
    return 1


# ---------------------------------------------------------------------------
# Scroll actions
# ---------------------------------------------------------------------------


def _scroll_down(view: DiffView) -> None:
    count = _consume_count(view)
    if not view._all_lines:
        for _ in range(count):
            VerticalScroll.action_scroll_down(view)
        return
    for _ in range(count):
        if not _step_down_one(view):
            break


def _scroll_up(view: DiffView) -> None:
    count = _consume_count(view)
    if not view._all_lines:
        for _ in range(count):
            VerticalScroll.action_scroll_up(view)
        return
    for _ in range(count):
        if not _step_up_one(view):
            break


def _is_on_last_row_of_current_line(view: DiffView) -> bool:
    rows = view._rows_for_current_mode()
    if not rows:
        return True
    cur_idx = view._current_row_index()
    if cur_idx < 0 or cur_idx >= len(rows):
        return True
    cur_line = rows[cur_idx].line_index
    next_idx = cur_idx + 1
    if next_idx >= len(rows):
        return True
    return rows[next_idx].line_index != cur_line


def _is_on_first_row_of_current_line(view: DiffView) -> bool:
    rows = view._rows_for_current_mode()
    if not rows:
        return True
    cur_idx = view._current_row_index()
    if cur_idx <= 0:
        return True
    cur_line = rows[cur_idx].line_index
    return rows[cur_idx - 1].line_index != cur_line


def _activate_comment_cursor(view: DiffView, line_index: int) -> None:
    if view._cursor_ui.flush_pending:
        _flush_queued_cursor_ui_updates(view)
    _comments.update_cursor_highlight(view, line_index, line_index)
    view._update_line_cursor(line_index)
    widget = _comments.active_comment_widget(view, line_index)
    if widget is not None and widget.is_mounted:
        view.scroll_to_widget(widget, animate=False)


def _step_down_one(view: DiffView) -> bool:
    cur_line = view.cursor_line
    n_comments = _comments.total_comments_at_line(view, cur_line)
    cur_offset = view._comment_cursor_index

    if cur_offset > 0:
        if cur_offset < n_comments:
            view._comment_cursor_index = cur_offset + 1
            _activate_comment_cursor(view, cur_line)
            return True
        # past last comment — leave the line entirely
        view._comment_cursor_index = 0
        _comments.update_cursor_highlight(view, cur_line, cur_line)
        moved = _move_cursor_rows(view, 1, scroll_in_visual=view.visual_mode)
        if moved:
            _flush_cursor_ui_now_if_safe(view)
        return moved

    if (
        not view.visual_mode
        and n_comments > 0
        and _is_on_last_row_of_current_line(view)
    ):
        view._comment_cursor_index = 1
        _activate_comment_cursor(view, cur_line)
        return True

    moved = _move_cursor_rows(view, 1, scroll_in_visual=view.visual_mode)
    if moved:
        _flush_cursor_ui_now_if_safe(view)
    return moved


def _step_up_one(view: DiffView) -> bool:
    cur_line = view.cursor_line
    cur_offset = view._comment_cursor_index

    if cur_offset > 1:
        view._comment_cursor_index = cur_offset - 1
        _activate_comment_cursor(view, cur_line)
        return True

    if cur_offset == 1:
        view._comment_cursor_index = 0
        _comments.update_cursor_highlight(view, cur_line, cur_line)
        view._update_line_cursor(cur_line)
        _scroll_to_cursor(view)
        return True

    moved = _move_cursor_rows(view, -1, scroll_in_visual=view.visual_mode)
    if not moved:
        return False

    if view.visual_mode:
        _flush_cursor_ui_now_if_safe(view)
        return True

    new_line = view.cursor_line
    if new_line != cur_line and _is_on_last_row_of_current_line(view):
        n_comments = _comments.total_comments_at_line(view, new_line)
        if n_comments > 0:
            view._comment_cursor_index = n_comments
            _activate_comment_cursor(view, new_line)
            return True
    _flush_cursor_ui_now_if_safe(view)
    return True


def _scroll_home(view: DiffView) -> None:
    rows = view._rows_for_current_mode()
    if rows:
        _jump_to_row_with_anchor(view, rows[0], viewport_offset=0)
        return
    view.scroll_home(animate=False)


def _scroll_end(view: DiffView) -> None:
    rows = view._rows_for_current_mode()
    count_explicit = bool(view._cursor_ui.pending_count)
    count = _consume_count(view)
    if rows:
        if count_explicit:
            target_line = max(0, min(count - 1, len(view._all_lines) - 1))
            target_row = _first_row_for_line(view, target_line) or rows[-1]
            _jump_to_row_with_anchor(view, target_row, viewport_offset=0)
        else:
            _jump_to_row_with_anchor(view, rows[-1], bottom_align=True)
            view.scroll_end(animate=False)
        _flush_cursor_ui_now_if_safe(view)
        return
    view.scroll_end(animate=False)


async def _half_page_down(view: DiffView) -> None:
    if view._all_lines:
        _jump_half_page(view, 1)
        return
    view.scroll_page_down(animate=False)


async def _half_page_up(view: DiffView) -> None:
    if view._all_lines:
        _jump_half_page(view, -1)
        return
    view.scroll_page_up(animate=False)


def _jump_half_page(view: DiffView, direction: int) -> None:
    rows = view._rows_for_current_mode()
    if not rows:
        return

    step = _half_page_step(view)
    viewport_offset = _current_cursor_viewport_offset(view)
    current = view._current_row_index()
    target = max(0, min(current + (direction * step), len(rows) - 1))
    if target == current:
        return

    if view._cursor_ui.desired_column is None:
        view._cursor_ui.desired_column = view.cursor_column

    row = rows[target]
    view._cursor_ui.suppress_scroll = True
    try:
        moved = _move_cursor_to_row(
            view,
            row,
            column=view._cursor_ui.desired_column,
            scroll_in_visual=view.visual_mode,
            preserve_desired_column=True,
        )
    finally:
        view._cursor_ui.suppress_scroll = False

    if not moved:
        return

    if viewport_offset is not None:
        _scroll_row_to_viewport_offset(view, row, viewport_offset)
    else:
        _scroll_to_cursor(view)
    _scroll_to_cursor_horizontal(view)
    _flush_cursor_ui_now_if_safe(view)


# ---------------------------------------------------------------------------
# Cursor movement actions
# ---------------------------------------------------------------------------


def _cursor_left(view: DiffView) -> None:
    if not view._all_lines:
        return
    if not _visual_mode.allows_column_motion(
        visual_mode=view.visual_mode,
        visual_type=view.visual_type,
    ):
        return
    count = _consume_count(view)
    if view.cursor_column > 0:
        _move_cursor(view, column=max(0, view.cursor_column - count))


def _cursor_right(view: DiffView) -> None:
    if not view._all_lines:
        return
    if not _visual_mode.allows_column_motion(
        visual_mode=view.visual_mode,
        visual_type=view.visual_type,
    ):
        return
    if view.cursor_line >= len(view._all_lines):
        return
    count = _consume_count(view)
    text = view._get_cursor_text()
    if not text:
        _move_cursor(view, column=0)
        return
    max_col = len(text) - 1
    if view.cursor_column < max_col:
        _move_cursor(view, column=min(max_col, view.cursor_column + count))


def _start_of_line(view: DiffView) -> None:
    if not view._all_lines:
        return
    if not _visual_mode.allows_column_motion(
        visual_mode=view.visual_mode,
        visual_type=view.visual_type,
    ):
        return
    _move_cursor(view, column=0)


def _first_non_blank(view: DiffView) -> None:
    if not view._all_lines:
        return
    if not _visual_mode.allows_column_motion(
        visual_mode=view.visual_mode,
        visual_type=view.visual_type,
    ):
        return
    if not (0 <= view.cursor_line < len(view._all_lines)):
        return
    text = view._get_cursor_text()
    first_nb = 0
    while first_nb < len(text) and text[first_nb].isspace():
        first_nb += 1
    _move_cursor(view, column=0 if first_nb >= len(text) else first_nb)


def _end_of_line(view: DiffView) -> None:
    if not view._all_lines:
        return
    if not _visual_mode.allows_column_motion(
        visual_mode=view.visual_mode,
        visual_type=view.visual_type,
    ):
        return
    if not (0 <= view.cursor_line < len(view._all_lines)):
        return
    count = _consume_count(view)
    if count > 1:
        _move_cursor_rows(view, count - 1)
    text = view._get_cursor_text()
    _move_cursor(view, column=max(0, len(text) - 1))


def _cycle_active_pane(view: DiffView) -> None:
    if not view._all_lines:
        return
    line = view._current_line()
    if line is None:
        return
    if not view.split and not line.is_modified:
        return
    target_pane: Literal["old", "new"] = (
        "old" if view.active_pane == "new" else "new"
    )
    _move_cursor(
        view,
        pane=target_pane,
        scroll_in_visual=view.visual_mode,
        update_active_pane=True,
    )


# ---------------------------------------------------------------------------
# Word motion actions
# ---------------------------------------------------------------------------


def _next_word(view: DiffView) -> None:
    count = _consume_count(view)
    for _ in range(count):
        if not _next_word_once(view):
            break


def _prev_word(view: DiffView) -> None:
    count = _consume_count(view)
    for _ in range(count):
        if not _prev_word_once(view):
            break


def _end_word(view: DiffView) -> None:
    count = _consume_count(view)
    for _ in range(count):
        if not _end_word_once(view):
            break


def _next_word_once(view: DiffView) -> bool:
    if not view._all_lines or view.cursor_line >= len(view._all_lines):
        return False
    text = view._get_cursor_text()
    next_pos = _word_motion.next_word_start(text, view.cursor_column)
    if next_pos is not None:
        return _move_cursor(view, column=next_pos)

    rows = view._rows_for_current_mode()
    current = view._current_row_index()
    for target_row in rows[current + 1 :]:
        pane = _pane_for_row(view, target_row)
        next_text = _get_cursor_text_for_target(view, target_row.line_index, pane)
        if next_text == "":
            return _move_cursor(
                view,
                line=target_row.line_index,
                pane=pane,
                column=0,
                scroll_in_visual=view.visual_mode,
            )
        first_word_pos = _word_motion.first_word_start(next_text)
        if first_word_pos < len(next_text):
            return _move_cursor(
                view,
                line=target_row.line_index,
                pane=pane,
                column=first_word_pos,
                scroll_in_visual=view.visual_mode,
            )
    return False


def _prev_word_once(view: DiffView) -> bool:
    if not view._all_lines or view.cursor_line >= len(view._all_lines):
        return False
    text = view._get_cursor_text()
    prev_pos = _word_motion.previous_word_start(text, view.cursor_column)
    if prev_pos is not None:
        return _move_cursor(view, column=prev_pos)

    rows = view._rows_for_current_mode()
    current = view._current_row_index()
    for target_row in reversed(rows[:current]):
        pane = _pane_for_row(view, target_row)
        prev_text = _get_cursor_text_for_target(view, target_row.line_index, pane)
        if prev_text == "":
            return _move_cursor(
                view,
                line=target_row.line_index,
                pane=pane,
                column=0,
                scroll_in_visual=view.visual_mode,
            )
        prev_word_start = _word_motion.last_word_start(prev_text)
        if prev_word_start is not None:
            return _move_cursor(
                view,
                line=target_row.line_index,
                pane=pane,
                column=prev_word_start,
                scroll_in_visual=view.visual_mode,
            )
    return False


def _end_word_once(view: DiffView) -> bool:
    if not view._all_lines or view.cursor_line >= len(view._all_lines):
        return False
    text = view._get_cursor_text()
    end_pos = _word_motion.next_word_end(text, view.cursor_column)
    if end_pos is not None:
        return _move_cursor(view, column=end_pos)

    rows = view._rows_for_current_mode()
    current = view._current_row_index()
    for target_row in rows[current + 1 :]:
        pane = _pane_for_row(view, target_row)
        next_text = _get_cursor_text_for_target(view, target_row.line_index, pane)
        first_word_pos = _word_motion.first_word_start(next_text)
        if first_word_pos >= len(next_text):
            continue
        end_pos = _word_motion.next_word_end(next_text, first_word_pos - 1)
        return _move_cursor(
            view,
            line=target_row.line_index,
            pane=pane,
            column=end_pos if end_pos is not None else first_word_pos,
            scroll_in_visual=view.visual_mode,
        )
    return False


def _pane_for_row(view: DiffView, row: RenderedRow) -> Literal["old", "new"]:
    if row.side == "auto":
        return view.cursor_pane
    return row.side


# ---------------------------------------------------------------------------
# Shared helpers (primary home — DiffView keeps thin wrappers)
# ---------------------------------------------------------------------------


def _dock_header_height(view: DiffView) -> int:
    header = view._header_widget
    if header is None:
        return 0
    return header.outer_size.height


def _half_page_step(view: DiffView) -> int:
    return max(1, view.scrollable_content_region.height // 2)


def _row_vertical_bounds(view: DiffView, row: RenderedRow) -> tuple[int, int] | None:
    return _geometry.row_vertical_bounds(
        row,
        all_lines=view._all_lines,
        split=view.split,
        line_top_offsets=view._line_top_offsets,
        line_bottom_offsets=view._line_bottom_offsets,
    )


def _get_cursor_text_for_target(
    view: DiffView,
    line_index: int,
    pane: Literal["old", "new"],
) -> str:
    if not (0 <= line_index < len(view._all_lines)):
        return ""
    line = view._all_lines[line_index]
    side = view._cursor_side_for_line(line, pane)
    return view._get_line_text(line, side)


# ---------------------------------------------------------------------------
# Scroll helpers
# ---------------------------------------------------------------------------


def _viewport_geometry(view: DiffView) -> _geometry.ViewportGeometry:
    return _geometry.ViewportGeometry(
        scroll_y=int(view.scroll_y),
        viewport_height=max(1, view.scrollable_content_region.height),
        max_scroll_y=max(0, int(view.max_scroll_y)),
        dock_header_height=_dock_header_height(view),
    )


def _current_cursor_viewport_offset(view: DiffView) -> int | None:
    row = view._current_row()
    if row is None:
        return None
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return None
    return _geometry.cursor_viewport_offset(bounds, _viewport_geometry(view))


def _scroll_row_to_viewport_offset(
    view: DiffView,
    row: RenderedRow,
    viewport_offset: int,
    *,
    animate: bool = False,
) -> None:
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return
    target_scroll = _geometry.scroll_target_for_row_viewport_offset(
        bounds,
        _viewport_geometry(view),
        viewport_offset,
    )
    view.scroll_to(y=target_scroll, animate=animate)


def _scroll_row_to_viewport_bottom(
    view: DiffView,
    row: RenderedRow,
    *,
    animate: bool = False,
) -> None:
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return
    target_scroll = _geometry.scroll_target_for_row_bottom(
        bounds,
        _viewport_geometry(view),
    )
    view.scroll_to(y=target_scroll, animate=animate)


def _row_is_visible(view: DiffView, row: RenderedRow) -> bool:
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return False
    return _geometry.row_is_visible(bounds, _viewport_geometry(view))


def _scroll_to_vertical_span(
    view: DiffView,
    top: int,
    bottom: int,
    *,
    animate: bool = False,
    top_align: bool = False,
    scrolloff: int | None = None,
) -> None:
    target_scroll = _geometry.scroll_target_for_span(
        top=top,
        bottom=bottom,
        viewport=_viewport_geometry(view),
        top_align=top_align,
        scrolloff=view.LAYOUT.vertical_scrolloff if scrolloff is None else scrolloff,
    )
    if target_scroll is not None:
        view.scroll_to(y=target_scroll, animate=animate)


def _target_widget_for_row(view: DiffView, row: RenderedRow):
    """Return the most specific mounted widget for a rendered row, if any.

    Estimated line offsets diverge from real heights when inline comments,
    pending drafts, or the inline editor are present. Callers should prefer
    `scroll_to_widget` on this widget over geometry-based scrolling.
    """
    target = view._row_anchor_widgets.get(row.anchor_id)
    if target is None or not target.is_mounted:
        target = view._line_widgets_by_index.get(row.line_index)
    if target is None or not target.is_mounted:
        return None
    if isinstance(target, (SplitDiffBlock, UnifiedDiffBlock)):
        return None
    return target


def _mounted_block_row_vertical_bounds(
    view: DiffView,
    row: RenderedRow,
) -> tuple[int, int] | None:
    block = (
        view._split_blocks_by_line.get(row.line_index)
        if view.split
        else view._unified_blocks_by_line.get(row.line_index)
    )
    if block is None or not block.is_mounted:
        return None

    line_indices = list(block.line_indices)
    try:
        row_offset = line_indices.index(row.line_index)
    except ValueError:
        return None

    block_top = int(view.scroll_y) + (
        block.region.y - view.scrollable_content_region.y
    )
    if view.split:
        top = block_top + row_offset
        return top, top + 1

    offset = 0
    for line_index in line_indices:
        if not (0 <= line_index < len(view._all_lines)):
            continue
        line = view._all_lines[line_index]
        if line_index == row.line_index:
            if line.is_modified and row.side == "new":
                offset += 1
            top = block_top + offset
            return top, top + 1
        offset += _geometry.render_height_for_line(line, split=False)

    return None


def _has_height_estimate_drift(view: DiffView) -> bool:
    """Return True when extras (comments, drafts, inline editor) make height estimates unreliable."""
    if view._inline_comment_editor_line_index is not None:
        return True
    if view._comment_threads_by_line:
        return True
    if view._pending_comment_drafts_by_line:
        return True
    return False


def _scroll_to_cursor(view: DiffView) -> None:
    if not view._all_lines or not view.is_mounted:
        return
    row = view._current_row()
    if row is None:
        return

    saved = view._suspend_scroll_virtual_window_watch
    view._suspend_scroll_virtual_window_watch = True
    try:
        if _has_height_estimate_drift(view):
            target_widget = _target_widget_for_row(view, row)
            if target_widget is not None:
                view.scroll_to_widget(target_widget, animate=False)
                return
            bounds = _mounted_block_row_vertical_bounds(view, row)
            if bounds is not None:
                top, bottom = bounds
                _scroll_to_vertical_span(view, top, bottom, animate=False)
                return

        bounds = _row_vertical_bounds(view, row)
        if bounds is None:
            return
        top, bottom = bounds
        _scroll_to_vertical_span(view, top, bottom, animate=False)
    finally:
        view._suspend_scroll_virtual_window_watch = saved


def _scroll_to_cursor_horizontal(view: DiffView) -> None:
    edge_padding = view.LAYOUT.horizontal_scroll_edge_padding
    reveal_padding = view.LAYOUT.horizontal_scroll_reveal_padding

    if view.split:
        scroll_widget = view._get_active_split_scroll_widget()
        if scroll_widget is None:
            return

        cursor_x = view.cursor_column
        viewport_width = max(1, scroll_widget.size.width)
        current_scroll = scroll_widget.scroll_x

        if cursor_x >= current_scroll + viewport_width - edge_padding:
            view._sync_split_horizontal_scroll(
                cursor_x - viewport_width + reveal_padding,
                source=None,
            )
        elif cursor_x < current_scroll + edge_padding:
            view._sync_split_horizontal_scroll(cursor_x - edge_padding, source=None)
        return

    scroll_widget = view._content_widget or view
    prefix_width = view._unified_prefix_width_for_layout()
    cursor_x = prefix_width + view.cursor_column
    viewport_width = max(1, scroll_widget.size.width)
    current_scroll = scroll_widget.scroll_x

    if cursor_x >= current_scroll + viewport_width - edge_padding:
        scroll_widget.scroll_x = cursor_x - viewport_width + reveal_padding
    elif cursor_x < current_scroll + prefix_width:
        scroll_widget.scroll_x = max(0, cursor_x - prefix_width - edge_padding)


# ---------------------------------------------------------------------------
# Jump / anchor
# ---------------------------------------------------------------------------


def _jump_to_row_with_anchor(
    view: DiffView,
    row: RenderedRow,
    *,
    pane: Literal["old", "new"] | None = None,
    column: int | None = None,
    viewport_offset: int | None = None,
    bottom_align: bool = False,
    animate: bool = False,
    reveal_horizontal: bool = False,
    update_active_pane: bool = False,
) -> None:
    target_pane = pane
    if target_pane is None and row.side != "auto":
        target_pane = "old" if row.side == "old" else "new"

    view._cursor_ui.suppress_scroll = True
    try:
        _move_cursor(
            view,
            line=row.line_index,
            pane=target_pane,
            column=column,
            scroll_in_visual=view.visual_mode,
            update_active_pane=update_active_pane,
        )
    finally:
        view._cursor_ui.suppress_scroll = False

    if bottom_align:
        _scroll_row_to_viewport_bottom(view, row, animate=animate)
    elif viewport_offset is not None:
        _scroll_row_to_viewport_offset(view, row, viewport_offset, animate=animate)
    else:
        _scroll_to_cursor(view)

    if reveal_horizontal:
        _scroll_to_cursor_horizontal(view)

    _flush_cursor_ui_now_if_safe(view)


def _flush_cursor_ui_now_if_safe(view: DiffView) -> None:
    if (
        not view.is_mounted
        or not view._cursor_ui.flush_pending
        or view._virt.render_pending
    ):
        return
    _flush_queued_cursor_ui_updates(view)


# ---------------------------------------------------------------------------
# Cursor UI flush / batching
# ---------------------------------------------------------------------------


def _queue_cursor_ui_flush(
    view: DiffView,
    *,
    cursor_lines: Collection[int] | None = None,
    selection_dirty_lines: Collection[int] | None = None,
    selection_full_refresh: bool = False,
    sync_search_match: bool = False,
    update_status_line: bool = False,
) -> None:
    if not view.is_mounted:
        if sync_search_match:
            _search.sync_match_index_to_cursor(view)
        if update_status_line:
            view._update_status_line()
        return

    request = _cursor_update.cursor_flush_request(
        line_count=len(view._all_lines),
        cursor_lines=cursor_lines,
        selection_dirty_lines=selection_dirty_lines,
        selection_full_refresh=selection_full_refresh,
        sync_search_match=sync_search_match,
        update_status_line=update_status_line,
    )

    if request.cursor_lines:
        view._cursor_ui.dirty_lines.update(request.cursor_lines)
    if request.selection_dirty_lines:
        view._cursor_ui.selection_dirty.update(request.selection_dirty_lines)
    if request.selection_full_refresh:
        view._cursor_ui.selection_full_refresh = True
    if request.sync_search_match:
        view._cursor_ui.sync_search = True
    if request.update_status_line:
        view._cursor_ui.update_status = True

    if view._cursor_ui.flush_pending:
        return

    view._cursor_ui.flush_pending = True
    view.call_next(view._flush_queued_cursor_ui_updates)


def _queue_cursor_update(
    view: DiffView,
    update: _cursor_update.CursorRepaintUpdate,
) -> None:
    queue_update = _cursor_update.cursor_queue_update(update)
    _queue_cursor_ui_flush(
        view,
        cursor_lines=queue_update.cursor_lines,
        selection_dirty_lines=queue_update.selection_dirty_lines,
        sync_search_match=queue_update.sync_search_match,
        update_status_line=queue_update.update_status_line,
    )


def _flush_queued_cursor_ui_updates(view: DiffView) -> None:
    view._cursor_ui.flush_pending = False

    cursor_lines = set(view._cursor_ui.dirty_lines)
    selection_dirty_lines = set(view._cursor_ui.selection_dirty)
    selection_full_refresh = view._cursor_ui.selection_full_refresh
    sync_search_match = view._cursor_ui.sync_search
    update_status_line = view._cursor_ui.update_status

    view._cursor_ui.dirty_lines.clear()
    view._cursor_ui.selection_dirty.clear()
    view._cursor_ui.selection_full_refresh = False
    view._cursor_ui.sync_search = False
    view._cursor_ui.update_status = False

    if not view.is_mounted:
        return

    cursor_lines = _cursor_update.cursor_lines_for_flush(
        cursor_lines=cursor_lines,
        selection_dirty_lines=selection_dirty_lines,
        selection_full_refresh=selection_full_refresh,
        visual_mode=view.visual_mode,
    )

    if cursor_lines:
        _blocks._refresh_grouped_blocks_for_lines(view, cursor_lines)
        for line_idx in sorted(cursor_lines):
            if (
                line_idx in view._unified_blocks_by_line
                or line_idx in view._split_blocks_by_line
            ):
                continue
            view._update_line_cursor(line_idx)

    if selection_full_refresh:
        view._update_selection_highlighting()
    elif selection_dirty_lines:
        view._update_selection_highlighting(selection_dirty_lines)

    if sync_search_match:
        _search.sync_match_index_to_cursor(view)
    if update_status_line:
        view._update_status_line()


# ---------------------------------------------------------------------------
# Core cursor movement
# ---------------------------------------------------------------------------


def _apply_cursor_move_side_effects(
    view: DiffView,
    *,
    old_line: int,
    new_line: int,
    old_column: int,
    new_column: int,
    old_pane: Literal["old", "new"],
    new_pane: Literal["old", "new"],
    scroll_in_visual: bool,
) -> None:
    move_update = _cursor_update.cursor_move_update(
        old_line=old_line,
        new_line=new_line,
        old_column=old_column,
        new_column=new_column,
        old_pane=old_pane,
        new_pane=new_pane,
        visual_mode=view.visual_mode,
        search_query=view._search_query,
    )

    if move_update.line_changed:
        hunk_index = view._get_hunk_index_for_line(new_line)
        if hunk_index is not None and hunk_index != view.current_hunk_index:
            view.current_hunk_index = hunk_index
        _virtual._maybe_update_virtual_window(view, new_line)
        _comments.update_cursor_highlight(view, old_line, new_line)

    scroll_update = _cursor_update.cursor_move_scroll_update(
        move_update,
        visual_mode=view.visual_mode,
        scroll_in_visual=scroll_in_visual,
        suppress_scroll=view._cursor_ui.suppress_scroll,
    )

    if scroll_update.scroll_vertical:
        _scroll_to_cursor(view)
    if scroll_update.scroll_horizontal:
        _scroll_to_cursor_horizontal(view)

    if move_update.changed:
        _queue_cursor_update(view, move_update)


def _move_cursor(
    view: DiffView,
    *,
    line: int | None = None,
    column: int | None = None,
    pane: Literal["old", "new"] | None = None,
    scroll_in_visual: bool = False,
    preserve_desired_column: bool = False,
    update_active_pane: bool = False,
) -> bool:
    if not view._all_lines:
        return False

    if not preserve_desired_column:
        view._cursor_ui.desired_column = None

    old_line = view.cursor_line
    old_column = view.cursor_column
    old_pane = view.cursor_pane

    target_line = (
        old_line if line is None else max(0, min(line, len(view._all_lines) - 1))
    )
    target_line_obj = view._all_lines[target_line]
    requested_pane = old_pane if pane is None else pane
    target_pane = requested_pane
    target_side = view._resolve_active_pane_for_line(target_line_obj, target_pane)

    target_text = _get_cursor_text_for_target(view, target_line, target_side)
    requested_column = old_column if column is None else column
    target_column = _cursor_update.clamp_cursor_column(
        requested_column=requested_column,
        text_length=len(target_text),
    )

    if (
        target_line == old_line
        and target_column == old_column
        and target_pane == old_pane
        and (not update_active_pane or view.active_pane == target_pane)
    ):
        return False

    view._cursor_ui.suspend_pane_watch = True
    view._cursor_ui.suspend_line_watch = True
    view._cursor_ui.suspend_column_watch = True
    try:
        if update_active_pane:
            view.active_pane = target_pane
        view.cursor_pane = target_pane
        view.cursor_line = target_line
        view.cursor_column = target_column
    finally:
        view._cursor_ui.suspend_pane_watch = False
        view._cursor_ui.suspend_line_watch = False
        view._cursor_ui.suspend_column_watch = False

    _apply_cursor_move_side_effects(
        view,
        old_line=old_line,
        new_line=target_line,
        old_column=old_column,
        new_column=target_column,
        old_pane=old_pane,
        new_pane=target_pane,
        scroll_in_visual=scroll_in_visual,
    )
    return True


def _move_cursor_to_row(
    view: DiffView,
    row: RenderedRow,
    *,
    column: int | None = None,
    scroll_in_visual: bool = False,
    preserve_desired_column: bool = False,
    update_active_pane: bool = False,
) -> bool:
    target_pane: Literal["old", "new"] | None
    if row.side == "auto":
        target_pane = None
    else:
        target_pane = "old" if row.side == "old" else "new"
    return _move_cursor(
        view,
        line=row.line_index,
        pane=target_pane,
        column=column,
        scroll_in_visual=scroll_in_visual,
        preserve_desired_column=preserve_desired_column,
        update_active_pane=update_active_pane,
    )


def _set_cursor_from_row(view: DiffView, row: RenderedRow) -> None:
    _move_cursor_to_row(view, row)


def _move_cursor_rows(
    view: DiffView, delta: int, *, scroll_in_visual: bool = False
) -> bool:
    rows = view._rows_for_current_mode()
    if not rows:
        return False
    current = view._current_row_index()
    target = max(0, min(current + delta, len(rows) - 1))
    if target == current:
        return False
    if view._cursor_ui.desired_column is None:
        view._cursor_ui.desired_column = view.cursor_column
    return _move_cursor_to_row(
        view,
        rows[target],
        column=view._cursor_ui.desired_column,
        scroll_in_visual=scroll_in_visual,
        preserve_desired_column=True,
    )


def _clamp_cursor_column_to_current_row(view: DiffView) -> None:
    text = view._get_cursor_text()
    view.cursor_column = _cursor_update.clamp_cursor_column(
        requested_column=view.cursor_column,
        text_length=len(text),
    )


def _first_row_for_line(view: DiffView, line_index: int) -> RenderedRow | None:
    for row in view._rows_for_current_mode():
        if row.line_index == line_index:
            return row
    return None


def _first_row_for_hunk(view: DiffView, hunk_index: int) -> RenderedRow | None:
    for row in view._rows_for_current_mode():
        if row.hunk_index == hunk_index:
            return row
    return None


# ---------------------------------------------------------------------------
# Hunk navigation
# ---------------------------------------------------------------------------


def _next_hunk(view: DiffView) -> None:
    if not view._diff or not view._diff.hunks:
        return
    total = len(view._diff.hunks)
    if view.current_hunk_index < total - 1:
        view.current_hunk_index += 1
        target_row = _first_row_for_hunk(view, view.current_hunk_index)
        if target_row is not None:
            _jump_to_row_with_anchor(view, target_row, viewport_offset=0)
        else:
            _scroll_to_hunk(view, view.current_hunk_index)
        view.post_message(
            view.HunkNavigated(
                hunk_index=view.current_hunk_index,
                total_hunks=total,
            )
        )


def _prev_hunk(view: DiffView) -> None:
    if not view._diff or not view._diff.hunks:
        return
    total = len(view._diff.hunks)
    if view.current_hunk_index > 0:
        view.current_hunk_index -= 1
        target_row = _first_row_for_hunk(view, view.current_hunk_index)
        if target_row is not None:
            _jump_to_row_with_anchor(view, target_row, viewport_offset=0)
        else:
            _scroll_to_hunk(view, view.current_hunk_index)
        view.post_message(
            view.HunkNavigated(
                hunk_index=view.current_hunk_index,
                total_hunks=total,
            )
        )


def _scroll_to_hunk(view: DiffView, index: int) -> None:
    if view._diff is None or not (0 <= index < len(view._diff.hunks)):
        return

    if view._virt.active:
        target_range = next(
            (item for item in view._hunk_line_ranges if item[0] == index),
            None,
        )
        if target_range is not None:
            _, start, end = target_range
            target_line = start if end >= start else start
            if not (view._virt.window_start <= target_line <= view._virt.window_end):
                _virtual._set_virtual_window_around(view, target_line)
                view._virt.render_pending = True
                view.run_worker(
                    _virtual._run_virtual_window_render_for_request(
                        view, view._render_request_token
                    ),
                    exclusive=True,
                    name="diff-virtual-hunk-jump",
                )
                view.call_after_refresh(lambda: _scroll_to_hunk(view, index))
                return

    if 0 <= index < len(view._hunk_header_top_offsets):
        _scroll_to_vertical_span(
            view,
            view._hunk_header_top_offsets[index],
            view._hunk_header_top_offsets[index] + 1,
            animate=True,
            top_align=True,
        )


# ---------------------------------------------------------------------------
# Center cursor (zz)
# ---------------------------------------------------------------------------


def _center_cursor(view: DiffView) -> None:
    row = view._current_row()
    if row is None:
        return
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return
    top, bottom = bounds
    mid = (top + bottom) // 2
    viewport_height = max(1, view.scrollable_content_region.height)
    header_h = _dock_header_height(view)
    target_y = max(0, mid - viewport_height // 2 - header_h)

    base_max_y = int(view.max_scroll_y) - view._center_padding_height
    needed_padding = max(0, target_y - base_max_y)

    container = view._content_widget
    if container is None:
        return

    delta = needed_padding - view._center_padding_height
    if delta != 0:
        view._center_padding_height = needed_padding
        pad = view._center_padding_widget
        if needed_padding > 0:
            from textual.widgets import Static

            if pad is None:
                pad = Static("", id="center-padding")
                pad.styles.height = needed_padding
                container.mount(pad)
                view._center_padding_widget = pad
            else:
                pad.styles.height = needed_padding
        elif pad is not None:
            pad.styles.height = 0

        vs = view.virtual_size
        view.virtual_size = Size(vs.width, max(0, vs.height + delta))

    view.scroll_to(y=target_y, animate=False)
