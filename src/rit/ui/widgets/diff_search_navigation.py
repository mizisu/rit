"""DiffView adapters for search match navigation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from rit.ui.messages import Flash
from rit.ui.widgets.diff_search_match_index import (
    next_match_index_from_cursor,
    refresh_matches,
)
from rit.ui.widgets.diff_search_policy import (
    search_activation_placement_update,
    search_activation_update,
    search_jump_update,
    search_reveal_update,
)

__all__ = (
    "activate_match",
    "jump_match",
    "reveal_match",
)

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


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
    update = search_reveal_update(
        target_exists=True,
        has_target_widget=target_widget is not None,
        target_visible=False if target_widget is not None else view._row_is_visible(target_row),
    )
    if update.action == "scroll_widget":
        assert target_widget is not None
        view.scroll_to_widget(target_widget, animate=False, top=True)
        return

    if update.action == "ignore":
        return
    _cursor._scroll_row_to_viewport_offset(view, target_row, update.viewport_offset)


def activate_match(view: DiffView, index: int) -> None:
    """Move the cursor or viewport to the requested search match."""
    activation = search_activation_update(
        view._search_matches,
        old_index=view._search_match_index,
        target_index=index,
    )
    if activation is None:
        return

    match = activation.match
    view._search_match_index = index
    view._invalidate_base_code_content_cache(set(activation.dirty_lines))

    rows = view._rows_for_current_mode()
    target_row = rows[match.row_index] if 0 <= match.row_index < len(rows) else None
    current_row = view._current_row()
    placement = search_activation_placement_update(
        has_target_row=target_row is not None,
        target_row_visible=view._row_is_visible(target_row)
        if target_row is not None
        else False,
        has_current_row=current_row is not None,
        row_distance=abs(target_row.row_index - current_row.row_index)
        if target_row is not None and current_row is not None
        else 0,
        half_page_step=view._half_page_step(),
    )

    if placement.action == "jump_anchor" and target_row is not None:
        view._jump_to_row_with_anchor(
            target_row,
            pane=activation.pane,
            column=match.column,
            viewport_offset=placement.viewport_offset,
            reveal_horizontal=placement.reveal_horizontal,
            update_active_pane=activation.update_active_pane,
        )
        return

    view._move_cursor(
        line=match.line_index,
        pane=activation.pane,
        column=match.column,
        scroll_in_visual=view.visual_mode,
        update_active_pane=activation.update_active_pane,
    )
    view._scroll_to_cursor_horizontal()
    view._update_status_line()


def jump_match(view: DiffView, direction: Literal[-1, 1]) -> None:
    """Activate the previous or next search match."""
    if view._search_query:
        refresh_matches(view)
    update = search_jump_update(
        query=view._search_query,
        match_count=len(view._search_matches),
        current_match_index=view._search_match_index,
        cursor_target_index=next_match_index_from_cursor(view)
        if view._search_matches
        else -1,
        direction=direction,
    )
    if update.action != "activate":
        assert update.flash_message is not None
        assert update.flash_style is not None
        view.post_message(Flash(update.flash_message, style=update.flash_style))
        if update.update_status:
            view._update_status_line()
        return

    activate_match(view, update.target_index)
