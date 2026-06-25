"""Cursor repaint policy."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal


PaneName = Literal["old", "new"]
_EMPTY_LINE_SET: frozenset[int] = frozenset()

__all__ = (
    "ActivePaneUpdate",
    "CursorColumnUpdate",
    "CursorFlushRequest",
    "CursorLineUpdate",
    "CursorMoveScrollUpdate",
    "CursorMoveUpdate",
    "CursorQueueUpdate",
    "CursorRepaintUpdate",
    "PaneName",
    "active_pane_update",
    "clamp_cursor_column",
    "cursor_column_update",
    "cursor_flush_request",
    "cursor_line_update",
    "cursor_lines_for_flush",
    "cursor_move_scroll_update",
    "cursor_move_update",
    "cursor_queue_update",
)


@dataclass(frozen=True)
class CursorMoveUpdate:
    """Repaint and status policy for a cursor move."""

    cursor_lines: frozenset[int]
    selection_dirty_lines: frozenset[int] | None
    sync_search_match: bool
    update_status_line: bool
    changed: bool
    line_changed: bool
    column_changed: bool
    pane_changed: bool


@dataclass(frozen=True)
class CursorFlushRequest:
    """Normalized batched cursor UI update request."""

    cursor_lines: frozenset[int]
    selection_dirty_lines: frozenset[int]
    selection_full_refresh: bool
    sync_search_match: bool
    update_status_line: bool


@dataclass(frozen=True)
class CursorQueueUpdate:
    """Cursor UI flush policy before line-bound normalization."""

    cursor_lines: frozenset[int]
    selection_dirty_lines: frozenset[int] | None
    sync_search_match: bool
    update_status_line: bool


@dataclass(frozen=True)
class CursorMoveScrollUpdate:
    """Scroll policy for a cursor move."""

    scroll_vertical: bool
    scroll_horizontal: bool


@dataclass(frozen=True)
class ActivePaneUpdate:
    """Repaint policy after the active pane changes."""

    cursor_lines: frozenset[int]
    selection_dirty_lines: frozenset[int] | None
    sync_search_match: bool
    update_status_line: bool


@dataclass(frozen=True)
class CursorLineUpdate:
    """Repaint policy after the cursor line changes."""

    cursor_lines: frozenset[int]
    selection_dirty_lines: frozenset[int] | None
    sync_search_match: bool
    update_status_line: bool


@dataclass(frozen=True)
class CursorColumnUpdate:
    """Repaint policy after the cursor column changes."""

    corrected_column: int | None
    cursor_lines: frozenset[int]
    selection_dirty_lines: frozenset[int] | None
    sync_search_match: bool
    scroll_horizontal: bool
    update_status_line: bool = False


type CursorRepaintUpdate = (
    ActivePaneUpdate | CursorLineUpdate | CursorColumnUpdate | CursorMoveUpdate
)


def _single_line_set(line: int) -> frozenset[int]:
    return frozenset((line,))


def _empty_line_set() -> frozenset[int]:
    return _EMPTY_LINE_SET


def _line_pair_set(first: int, second: int) -> frozenset[int]:
    if first == second:
        return _single_line_set(first)
    return frozenset((first, second))


def active_pane_update(
    *,
    cursor_line: int,
    visual_mode: bool,
) -> ActivePaneUpdate:
    """Return repaint policy after the active pane changes."""
    cursor_lines = _single_line_set(cursor_line)
    return ActivePaneUpdate(
        cursor_lines=cursor_lines,
        selection_dirty_lines=cursor_lines if visual_mode else None,
        sync_search_match=True,
        update_status_line=True,
    )


def cursor_column_update(
    *,
    cursor_line: int,
    new_column: int,
    text_length: int,
    visual_mode: bool,
) -> CursorColumnUpdate:
    """Return repaint policy after the cursor column changes."""
    clamped_column = clamp_cursor_column(
        requested_column=new_column,
        text_length=text_length,
    )
    if new_column != clamped_column:
        return CursorColumnUpdate(
            corrected_column=clamped_column,
            cursor_lines=_empty_line_set(),
            selection_dirty_lines=None,
            sync_search_match=False,
            scroll_horizontal=False,
        )

    cursor_lines = _single_line_set(cursor_line)
    return CursorColumnUpdate(
        corrected_column=None,
        cursor_lines=cursor_lines,
        selection_dirty_lines=cursor_lines if visual_mode else None,
        sync_search_match=True,
        scroll_horizontal=True,
    )


def clamp_cursor_column(*, requested_column: int, text_length: int) -> int:
    """Return a cursor column inside the available text bounds."""
    if text_length <= 0:
        return 0
    return max(0, min(requested_column, text_length - 1))


def cursor_line_update(
    *,
    old_line: int,
    new_line: int,
    visual_mode: bool,
) -> CursorLineUpdate:
    """Return repaint policy after the cursor line changes."""
    lines = _line_pair_set(old_line, new_line)
    return CursorLineUpdate(
        cursor_lines=lines,
        selection_dirty_lines=lines if visual_mode else None,
        sync_search_match=True,
        update_status_line=True,
    )


def cursor_queue_update(update: CursorRepaintUpdate) -> CursorQueueUpdate:
    """Return the queued cursor UI flush policy for a cursor update."""
    return CursorQueueUpdate(
        cursor_lines=update.cursor_lines,
        selection_dirty_lines=update.selection_dirty_lines,
        sync_search_match=update.sync_search_match,
        update_status_line=update.update_status_line,
    )


def cursor_move_scroll_update(
    update: CursorMoveUpdate,
    *,
    visual_mode: bool,
    scroll_in_visual: bool,
    suppress_scroll: bool,
) -> CursorMoveScrollUpdate:
    """Return scroll policy for a cursor move."""
    if suppress_scroll:
        return CursorMoveScrollUpdate(
            scroll_vertical=False,
            scroll_horizontal=False,
        )

    vertical_triggered = update.line_changed or update.pane_changed
    scroll_vertical = vertical_triggered and (not visual_mode or scroll_in_visual)
    return CursorMoveScrollUpdate(
        scroll_vertical=scroll_vertical,
        scroll_horizontal=update.column_changed or update.pane_changed,
    )


def cursor_flush_request(
    *,
    line_count: int,
    cursor_lines: Collection[int] | None = None,
    selection_dirty_lines: Collection[int] | None = None,
    selection_full_refresh: bool = False,
    sync_search_match: bool = False,
    update_status_line: bool = False,
) -> CursorFlushRequest:
    """Return a normalized cursor UI flush request."""
    return CursorFlushRequest(
        cursor_lines=_bounded_line_indices(cursor_lines, line_count),
        selection_dirty_lines=_bounded_line_indices(
            selection_dirty_lines, line_count
        ),
        selection_full_refresh=selection_full_refresh,
        sync_search_match=sync_search_match,
        update_status_line=update_status_line,
    )


def _bounded_line_indices(
    lines: Collection[int] | None,
    line_count: int,
) -> frozenset[int]:
    if not lines or line_count <= 0:
        return _empty_line_set()
    first_line: int | None = None
    second_line: int | None = None
    extra_lines: list[int] | None = None
    for line_idx in lines:
        if not 0 <= line_idx < line_count:
            continue
        if first_line is None:
            first_line = line_idx
        elif second_line is None:
            second_line = line_idx
        else:
            if extra_lines is None:
                extra_lines = [first_line, second_line]
            extra_lines.append(line_idx)
    if first_line is None:
        return _empty_line_set()
    if second_line is None:
        return _single_line_set(first_line)
    if extra_lines is None:
        return _line_pair_set(first_line, second_line)
    return frozenset(extra_lines)


def cursor_move_update(
    *,
    old_line: int,
    new_line: int,
    old_column: int,
    new_column: int,
    old_pane: PaneName,
    new_pane: PaneName,
    visual_mode: bool,
    search_query: str | None,
) -> CursorMoveUpdate:
    """Return repaint and status policy for a cursor move."""
    line_changed = old_line != new_line
    column_changed = old_column != new_column
    pane_changed = old_pane != new_pane
    changed = line_changed or column_changed or pane_changed

    cursor_lines = (
        _line_pair_set(old_line, new_line)
        if line_changed
        else _single_line_set(new_line)
    )

    selection_dirty_lines: frozenset[int] | None = None
    if visual_mode:
        selection_dirty_lines = cursor_lines

    return CursorMoveUpdate(
        cursor_lines=cursor_lines,
        selection_dirty_lines=selection_dirty_lines,
        sync_search_match=changed,
        update_status_line=(line_changed or pane_changed)
        or (column_changed and bool(search_query)),
        changed=changed,
        line_changed=line_changed,
        column_changed=column_changed,
        pane_changed=pane_changed,
    )


def cursor_lines_for_flush(
    *,
    cursor_lines: set[int],
    selection_dirty_lines: set[int],
    selection_full_refresh: bool,
    visual_mode: bool,
) -> set[int]:
    """Return cursor lines that still need repainting for a flush."""
    if not visual_mode:
        return cursor_lines
    if selection_full_refresh:
        return set()
    if not selection_dirty_lines:
        return cursor_lines
    lines = set(cursor_lines)
    lines.difference_update(selection_dirty_lines)
    return lines
