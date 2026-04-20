"""Cursor movement, scrolling, word motion, and hunk navigation for DiffView."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

from textual.containers import VerticalScroll
from textual.geometry import Size

from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_virtual as _virtual
from rit.ui.widgets.diff_types import RenderedRow

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


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
    if view._all_lines:
        _move_cursor_rows(view, count, scroll_in_visual=view.visual_mode)
    else:
        for _ in range(count):
            VerticalScroll.action_scroll_down(view)


def _scroll_up(view: DiffView) -> None:
    count = _consume_count(view)
    if view._all_lines:
        _move_cursor_rows(view, -count, scroll_in_visual=view.visual_mode)
    else:
        for _ in range(count):
            VerticalScroll.action_scroll_up(view)


def _scroll_home(view: DiffView) -> None:
    rows = view._rows_for_current_mode()
    if rows:
        _jump_to_row_with_anchor(view, rows[0], viewport_offset=0)
        return
    view.scroll_home(animate=False)


def _scroll_end(view: DiffView) -> None:
    rows = view._rows_for_current_mode()
    if rows:
        _jump_to_row_with_anchor(view, rows[-1], bottom_align=True)
        view.scroll_end(animate=False)
        _flush_cursor_ui_now_if_safe(view)
        return
    view.scroll_end(animate=False)


async def _half_page_down(view: DiffView) -> None:
    if view._all_lines:
        await _animated_half_page_scroll(view, 1)
        return
    view.scroll_page_down(animate=False)


async def _half_page_up(view: DiffView) -> None:
    if view._all_lines:
        await _animated_half_page_scroll(view, -1)
        return
    view.scroll_page_up(animate=False)


async def _animated_half_page_scroll(view: DiffView, direction: int) -> None:
    step = _half_page_step(view)
    viewport_offset = _current_cursor_viewport_offset(view)
    delay = 0.15 / step

    for _ in range(step):
        view._cursor_ui.suppress_scroll = True
        try:
            moved = _move_cursor_rows(
                view,
                direction,
                scroll_in_visual=view.visual_mode,
            )
        finally:
            view._cursor_ui.suppress_scroll = False
        if not moved:
            break
        if viewport_offset is not None:
            row = view._current_row()
            if row is not None:
                _scroll_row_to_viewport_offset(view, row, viewport_offset)
        _flush_cursor_ui_now_if_safe(view)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Cursor movement actions
# ---------------------------------------------------------------------------


def _cursor_left(view: DiffView) -> None:
    if not view._all_lines:
        return
    if view.visual_mode and view.visual_type == "line":
        return
    if view.cursor_column > 0:
        _move_cursor(view, column=view.cursor_column - 1)


def _cursor_right(view: DiffView) -> None:
    if not view._all_lines:
        return
    if view.visual_mode and view.visual_type == "line":
        return
    if view.cursor_line >= len(view._all_lines):
        return
    text = view._get_cursor_text()
    if not text:
        _move_cursor(view, column=0)
        return
    max_col = len(text) - 1
    if view.cursor_column < max_col:
        _move_cursor(view, column=view.cursor_column + 1)


def _start_of_line(view: DiffView) -> None:
    if not view._all_lines:
        return
    if view.visual_mode and view.visual_type == "line":
        return
    _move_cursor(view, column=0)


def _first_non_blank(view: DiffView) -> None:
    if not view._all_lines:
        return
    if view.visual_mode and view.visual_type == "line":
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
    if view.visual_mode and view.visual_type == "line":
        return
    if not (0 <= view.cursor_line < len(view._all_lines)):
        return
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
        "old" if view._resolve_active_pane_for_line(line) == "new" else "new"
    )
    _move_cursor(view, pane=target_pane, scroll_in_visual=view.visual_mode)


# ---------------------------------------------------------------------------
# Word motion actions
# ---------------------------------------------------------------------------


def _next_word(view: DiffView) -> None:
    if not view._all_lines or view.cursor_line >= len(view._all_lines):
        return
    text = view._get_cursor_text()
    next_pos = _find_next_word_start(text, view.cursor_column)
    if next_pos is not None:
        _move_cursor(view, column=next_pos)
        return
    rows = view._rows_for_current_mode()
    current = view._current_row_index()
    if current >= len(rows) - 1:
        return
    target_row = rows[current + 1]
    next_text = _get_cursor_text_for_target(
        view,
        target_row.line_index,
        view.active_pane
        if target_row.side == "auto"
        else ("old" if target_row.side == "old" else "new"),
    )
    _move_cursor(
        view,
        line=target_row.line_index,
        pane=None
        if target_row.side == "auto"
        else ("old" if target_row.side == "old" else "new"),
        column=_find_first_word(next_text),
        scroll_in_visual=view.visual_mode,
    )


def _prev_word(view: DiffView) -> None:
    if not view._all_lines or view.cursor_line >= len(view._all_lines):
        return
    text = view._get_cursor_text()
    prev_pos = _find_prev_word_start(text, view.cursor_column)
    if prev_pos is not None:
        _move_cursor(view, column=prev_pos)
        return
    rows = view._rows_for_current_mode()
    current = view._current_row_index()
    if current <= 0:
        return
    target_row = rows[current - 1]
    prev_text = _get_cursor_text_for_target(
        view,
        target_row.line_index,
        view.active_pane
        if target_row.side == "auto"
        else ("old" if target_row.side == "old" else "new"),
    )
    _move_cursor(
        view,
        line=target_row.line_index,
        pane=None
        if target_row.side == "auto"
        else ("old" if target_row.side == "old" else "new"),
        column=max(0, len(prev_text) - 1),
        scroll_in_visual=view.visual_mode,
    )


def _end_word(view: DiffView) -> None:
    if not view._all_lines or view.cursor_line >= len(view._all_lines):
        return
    text = view._get_cursor_text()
    end_pos = _find_next_word_end(text, view.cursor_column)
    if end_pos is not None:
        _move_cursor(view, column=end_pos)
        return
    rows = view._rows_for_current_mode()
    current = view._current_row_index()
    if current >= len(rows) - 1:
        return
    target_row = rows[current + 1]
    next_text = _get_cursor_text_for_target(
        view,
        target_row.line_index,
        view.active_pane
        if target_row.side == "auto"
        else ("old" if target_row.side == "old" else "new"),
    )
    first_word_pos = _find_first_word(next_text)
    end_pos = _find_next_word_end(next_text, first_word_pos - 1)
    _move_cursor(
        view,
        line=target_row.line_index,
        pane=None
        if target_row.side == "auto"
        else ("old" if target_row.side == "old" else "new"),
        column=end_pos if end_pos is not None else first_word_pos,
        scroll_in_visual=view.visual_mode,
    )


# ---------------------------------------------------------------------------
# Word boundary helpers
# ---------------------------------------------------------------------------


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _find_first_word(text: str) -> int:
    pos = 0
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _find_next_word_start(text: str, pos: int) -> int | None:
    if pos >= len(text) - 1:
        return None
    current_pos = pos
    if _is_word_char(text[current_pos]):
        while current_pos < len(text) and _is_word_char(text[current_pos]):
            current_pos += 1
    elif not text[current_pos].isspace():
        while (
            current_pos < len(text)
            and not text[current_pos].isspace()
            and not _is_word_char(text[current_pos])
        ):
            current_pos += 1
    while current_pos < len(text) and text[current_pos].isspace():
        current_pos += 1
    return current_pos if current_pos < len(text) else None


def _find_prev_word_start(text: str, pos: int) -> int | None:
    if pos <= 0:
        return None
    current_pos = pos - 1
    while current_pos > 0 and text[current_pos].isspace():
        current_pos -= 1
    if _is_word_char(text[current_pos]):
        while current_pos > 0 and _is_word_char(text[current_pos - 1]):
            current_pos -= 1
    else:
        while (
            current_pos > 0
            and not text[current_pos - 1].isspace()
            and not _is_word_char(text[current_pos - 1])
        ):
            current_pos -= 1
    return current_pos


def _find_next_word_end(text: str, pos: int) -> int | None:
    if pos >= len(text) - 1:
        return None
    current_pos = pos + 1
    while current_pos < len(text) and text[current_pos].isspace():
        current_pos += 1
    if current_pos >= len(text):
        return None
    if _is_word_char(text[current_pos]):
        while current_pos < len(text) - 1 and _is_word_char(text[current_pos + 1]):
            current_pos += 1
    else:
        while (
            current_pos < len(text) - 1
            and not text[current_pos + 1].isspace()
            and not _is_word_char(text[current_pos + 1])
        ):
            current_pos += 1
    return current_pos


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
    if not (0 <= row.line_index < len(view._all_lines)):
        return None
    top = view._line_top_offsets[row.line_index]
    bottom = view._line_bottom_offsets[row.line_index]
    line = view._all_lines[row.line_index]
    if not view.split and line.is_modified:
        if row.side == "old":
            return top, top + 1
        if row.side == "new":
            return top + 1, top + 2
    return top, bottom


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


def _current_cursor_viewport_offset(view: DiffView) -> int | None:
    row = view._current_row()
    if row is None:
        return None
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return None
    top, _ = bounds
    return max(0, top - int(view.scroll_y) - _dock_header_height(view))


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
    top, bottom = bounds
    viewport_height = max(1, view.scrollable_content_region.height)
    header_h = _dock_header_height(view)
    target_scroll = max(0, top - max(0, viewport_offset) - header_h)
    if bottom - target_scroll - header_h > viewport_height:
        target_scroll = max(0, bottom - viewport_height - header_h)
    view.scroll_to(
        y=min(target_scroll, max(0, int(view.max_scroll_y))),
        animate=animate,
    )


def _scroll_row_to_viewport_bottom(
    view: DiffView,
    row: RenderedRow,
    *,
    animate: bool = False,
) -> None:
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return
    _, bottom = bounds
    viewport_height = max(1, view.scrollable_content_region.height)
    view.scroll_to(
        y=min(max(0, bottom - viewport_height), max(0, int(view.max_scroll_y))),
        animate=animate,
    )


def _row_is_visible(view: DiffView, row: RenderedRow) -> bool:
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return False
    top, bottom = bounds
    header_h = _dock_header_height(view)
    current_top = int(view.scroll_y) + header_h
    current_bottom = current_top + max(1, view.scrollable_content_region.height)
    return top >= current_top and bottom <= current_bottom


def _scroll_to_vertical_span(
    view: DiffView,
    top: int,
    bottom: int,
    *,
    animate: bool = False,
    top_align: bool = False,
) -> None:
    viewport_height = max(1, view.scrollable_content_region.height)
    header_h = _dock_header_height(view)
    current_top = int(view.scroll_y) + header_h
    current_bottom = current_top + viewport_height

    if top_align:
        view.scroll_to(y=max(0, top - header_h), animate=animate)
        return
    if top < current_top:
        view.scroll_to(y=max(0, top - header_h), animate=animate)
        return
    if bottom > current_bottom:
        view.scroll_to(y=max(0, bottom - viewport_height), animate=animate)


def _scroll_to_cursor(view: DiffView) -> None:
    if not view._all_lines or not view.is_mounted:
        return
    row = view._current_row()
    if row is None:
        return
    bounds = _row_vertical_bounds(view, row)
    if bounds is None:
        return
    top, bottom = bounds
    saved = view._suspend_scroll_virtual_window_watch
    view._suspend_scroll_virtual_window_watch = True
    try:
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

    prefix_width = view._unified_prefix_width_for_layout()
    cursor_x = prefix_width + view.cursor_column
    viewport_width = view.size.width
    current_scroll = view.scroll_x

    if cursor_x >= current_scroll + viewport_width - edge_padding:
        view.scroll_x = cursor_x - viewport_width + reveal_padding
    elif cursor_x < current_scroll + prefix_width:
        view.scroll_x = max(0, cursor_x - prefix_width - edge_padding)


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
    cursor_lines: set[int] | None = None,
    selection_dirty_lines: set[int] | None = None,
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

    if cursor_lines:
        view._cursor_ui.dirty_lines.update(
            line_idx
            for line_idx in cursor_lines
            if 0 <= line_idx < len(view._all_lines)
        )
    if selection_dirty_lines:
        view._cursor_ui.selection_dirty.update(
            line_idx
            for line_idx in selection_dirty_lines
            if 0 <= line_idx < len(view._all_lines)
        )
    if selection_full_refresh:
        view._cursor_ui.selection_full_refresh = True
    if sync_search_match:
        view._cursor_ui.sync_search = True
    if update_status_line:
        view._cursor_ui.update_status = True

    if view._cursor_ui.flush_pending:
        return

    view._cursor_ui.flush_pending = True
    view.call_next(view._flush_queued_cursor_ui_updates)


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

    if view.visual_mode:
        if selection_full_refresh:
            cursor_lines.clear()
        elif selection_dirty_lines:
            cursor_lines.difference_update(selection_dirty_lines)

    if cursor_lines:
        if not _blocks._refresh_grouped_blocks_for_lines(view, cursor_lines):
            for line_idx in sorted(cursor_lines):
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
    line_changed = old_line != new_line
    column_changed = old_column != new_column
    pane_changed = old_pane != new_pane

    dirty_cursor_lines = {new_line}
    if line_changed:
        dirty_cursor_lines.add(old_line)
        hunk_index = view._get_hunk_index_for_line(new_line)
        if hunk_index is not None and hunk_index != view.current_hunk_index:
            view.current_hunk_index = hunk_index
        _virtual._maybe_update_virtual_window(view, new_line)
        _comments.update_cursor_highlight(view, old_line, new_line)

    dirty_lines: set[int] = set()
    if view.visual_mode:
        dirty_lines = {new_line}
        if line_changed:
            dirty_lines.add(old_line)
        if scroll_in_visual and (line_changed or pane_changed):
            if not view._cursor_ui.suppress_scroll:
                _scroll_to_cursor(view)
    else:
        if line_changed or pane_changed:
            if not view._cursor_ui.suppress_scroll:
                _scroll_to_cursor(view)

    if column_changed or pane_changed:
        if not view._cursor_ui.suppress_scroll:
            _scroll_to_cursor_horizontal(view)

    if line_changed or pane_changed or column_changed:
        _queue_cursor_ui_flush(
            view,
            cursor_lines=dirty_cursor_lines,
            selection_dirty_lines=dirty_lines if view.visual_mode else None,
            sync_search_match=True,
            update_status_line=(line_changed or pane_changed)
            or (column_changed and bool(view._search_query)),
        )


def _move_cursor(
    view: DiffView,
    *,
    line: int | None = None,
    column: int | None = None,
    pane: Literal["old", "new"] | None = None,
    scroll_in_visual: bool = False,
) -> bool:
    if not view._all_lines:
        return False

    old_line = view.cursor_line
    old_column = view.cursor_column
    old_pane = view.active_pane

    target_line = (
        old_line if line is None else max(0, min(line, len(view._all_lines) - 1))
    )
    target_line_obj = view._all_lines[target_line]
    requested_pane = old_pane if pane is None else pane
    target_pane = view._resolve_active_pane_for_line(target_line_obj, requested_pane)

    target_text = _get_cursor_text_for_target(view, target_line, target_pane)
    requested_column = old_column if column is None else column
    if target_text:
        target_column = max(0, min(requested_column, len(target_text) - 1))
    else:
        target_column = 0

    if (
        target_line == old_line
        and target_column == old_column
        and target_pane == old_pane
    ):
        return False

    view._cursor_ui.suspend_pane_watch = True
    view._cursor_ui.suspend_line_watch = True
    view._cursor_ui.suspend_column_watch = True
    try:
        view.active_pane = target_pane
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
    scroll_in_visual: bool = False,
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
        scroll_in_visual=scroll_in_visual,
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
    return _move_cursor_to_row(view, rows[target], scroll_in_visual=scroll_in_visual)


def _clamp_cursor_column_to_current_row(view: DiffView) -> None:
    text = view._get_cursor_text()
    if text:
        view.cursor_column = min(view.cursor_column, len(text) - 1)
    else:
        view.cursor_column = 0


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
