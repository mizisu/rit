"""Diff in-file search: inline prompt, match building, and navigation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual.content import Content

from rit.ui.messages import Flash
from rit.ui.widgets.diff_types import DiffSearchMatch

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


def clear_state(view: DiffView) -> None:
    view._search_query = ""
    view._search_matches = []
    view._search_match_index = -1


def search_sides_for_row(
    view: DiffView,
    row: DiffSearchMatch | object,
) -> tuple[Literal["old", "new", "auto"], ...]:
    from rit.ui.widgets.diff_types import RenderedRow

    assert isinstance(row, RenderedRow)
    if row.mode == "unified":
        return (row.side,)

    line = view._all_lines[row.line_index]
    if line.is_modified:
        return ("old", "new")
    if line.is_deleted:
        return ("old",)
    if line.is_added:
        return ("new",)
    return ("auto",)


def build_matches(view: DiffView, query: str) -> list[DiffSearchMatch]:
    needle = query.casefold()
    if not needle:
        return []

    needle_len = len(needle)
    matches: list[DiffSearchMatch] = []
    for row in view._rows_for_current_mode():
        line = view._all_lines[row.line_index]
        for side in search_sides_for_row(view, row):
            text = view._get_line_text(line, side)
            if not text:
                continue
            text_lower = text.casefold()
            start = 0
            while True:
                column = text_lower.find(needle, start)
                if column < 0:
                    break
                matches.append(
                    DiffSearchMatch(
                        row_index=row.row_index,
                        line_index=row.line_index,
                        side=side,
                        column=column,
                    )
                )
                start = column + needle_len
    return matches


def apply_search_highlights(
    view: DiffView,
    content: Content,
    line_index: int,
    side: Literal["old", "new", "auto"],
) -> Content:
    if not view._search_query or not view._search_matches:
        return content

    needle_len = len(view._search_query)
    active_idx = view._search_match_index
    result = content

    for idx, match in enumerate(view._search_matches):
        if match.line_index != line_index or match.side != side:
            continue
        end = match.column + needle_len
        if idx == active_idx:
            result = result.stylize("on $warning 45%", match.column, end)
        else:
            result = result.stylize("on $warning 25%", match.column, end)

    return result


def refresh_matches(view: DiffView) -> None:
    if not view._search_query:
        view._search_matches = []
        view._search_match_index = -1
        return

    view._search_matches = build_matches(view, view._search_query)
    sync_match_index_to_cursor(view)


def sync_match_index_to_cursor(view: DiffView) -> None:
    if not view._search_matches:
        view._search_match_index = -1
        return

    current_line = view.cursor_line
    current_side = view._current_cursor_side()
    current_column = view.cursor_column

    view._search_match_index = -1
    for index, match in enumerate(view._search_matches):
        if (
            match.line_index == current_line
            and match.side == current_side
            and match.column == current_column
        ):
            view._search_match_index = index
            break


def next_match_index_from_cursor(view: DiffView) -> int:
    if not view._search_matches:
        return -1

    current_row_index = view._current_row_index()
    current_side = view._current_cursor_side()
    current_column = view.cursor_column

    for index, match in enumerate(view._search_matches):
        if match.row_index > current_row_index:
            return index
        if match.row_index < current_row_index:
            continue
        if match.column > current_column:
            return index
        if match.column == current_column and match.side != current_side:
            return index

    return 0


def reveal_match(view: DiffView, index: int) -> None:
    """Scroll the viewport so the active match row is visible without moving the cursor."""
    if not (0 <= index < len(view._search_matches)):
        return

    match = view._search_matches[index]
    rows = view._rows_for_current_mode()
    target_row = rows[match.row_index] if 0 <= match.row_index < len(rows) else None
    if target_row is None:
        return

    from rit.ui.widgets import diff_cursor as _cursor

    target_widget = _cursor._target_widget_for_row(view, target_row)
    if target_widget is not None:
        view.scroll_to_widget(target_widget, animate=False, top=True)
        return

    if view._row_is_visible(target_row):
        return
    _cursor._scroll_row_to_viewport_offset(view, target_row, 0)


def activate_match(view: DiffView, index: int) -> None:
    if not (0 <= index < len(view._search_matches)):
        return

    old_index = view._search_match_index
    match = view._search_matches[index]
    view._search_match_index = index

    dirty: set[int] = {match.line_index}
    if 0 <= old_index < len(view._search_matches):
        dirty.add(view._search_matches[old_index].line_index)
    view._invalidate_base_code_content_cache(dirty)

    rows = view._rows_for_current_mode()
    target_row = rows[match.row_index] if 0 <= match.row_index < len(rows) else None
    current_row = view._current_row()
    should_anchor = target_row is not None and (
        not view._row_is_visible(target_row)
        or current_row is None
        or abs(target_row.row_index - current_row.row_index) > view._half_page_step()
    )

    if should_anchor and target_row is not None:
        view._jump_to_row_with_anchor(
            target_row,
            pane=None if match.side == "auto" else match.side,
            column=match.column,
            viewport_offset=0,
            reveal_horizontal=True,
        )
        return

    view._move_cursor(
        line=match.line_index,
        pane=None if match.side == "auto" else match.side,
        column=match.column,
        scroll_in_visual=view.visual_mode,
    )
    view._scroll_to_cursor_horizontal()
    view._update_status_line()


def _refresh_search_display(view: DiffView) -> None:
    match_lines = {m.line_index for m in view._search_matches}
    match_lines = match_lines | view._prev_search_match_lines
    view._prev_search_match_lines = {m.line_index for m in view._search_matches}

    if not match_lines:
        return

    view._invalidate_base_code_content_cache(match_lines)

    from rit.ui.widgets import diff_blocks as _blocks

    if _blocks._refresh_grouped_blocks_for_lines(view, match_lines):
        return

    for line_idx in match_lines:
        view._update_line_cursor(line_idx)


def handle_submitted(view: DiffView, query: str | None) -> None:
    if query is None:
        return

    normalized = query.strip()
    if not normalized:
        clear_state(view)
        _refresh_search_display(view)
        view.post_message(Flash("Search cleared", duration=1.5))
        view._update_status_line()
        return

    view._search_query = normalized
    refresh_matches(view)
    if not view._search_matches:
        view._search_match_index = -1
        _refresh_search_display(view)
        view.post_message(Flash(f"No matches: {normalized}", style="warning"))
        view._update_status_line()
        return

    view._search_match_index = next_match_index_from_cursor(view)
    _refresh_search_display(view)
    activate_match(view, view._search_match_index)


def jump_match(view: DiffView, direction: Literal[-1, 1]) -> None:
    if not view._search_query:
        view.post_message(Flash("No active search", style="warning"))
        return

    refresh_matches(view)
    if not view._search_matches:
        view.post_message(Flash(f"No matches: {view._search_query}", style="warning"))
        view._update_status_line()
        return

    if view._search_match_index < 0:
        target_index = next_match_index_from_cursor(view)
        if direction < 0:
            target_index = len(view._search_matches) - 1
    else:
        target_index = (view._search_match_index + direction) % len(
            view._search_matches
        )

    activate_match(view, target_index)
