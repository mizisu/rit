"""DiffView command adapters for applying in-file search input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rit.ui.messages import Flash
from rit.ui.widgets.diff_search_display import refresh_search_display
from rit.ui.widgets.diff_search_match_index import build_matches
from rit.ui.widgets.diff_search_navigation import activate_match, reveal_match
from rit.ui.widgets.diff_search_policy import (
    next_search_match_index,
    search_change_update,
    search_submission_request,
    search_submit_update,
)

__all__ = (
    "clear_state",
    "handle_changed",
    "handle_submitted",
)

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


def clear_state(view: DiffView) -> None:
    """Clear active in-file search state."""
    view._search_query = ""
    view._search_matches = []
    view._search_match_index = -1


def handle_changed(view: DiffView, value: str) -> None:
    """Update search state after the inline search input changes."""
    request = search_submission_request(value)
    if request.action == "search":
        matches = build_matches(view, request.query)
        cursor_target_index = next_search_match_index(
            matches,
            current_row_index=view._current_row_index(),
            current_side=view._current_cursor_side(),
            current_column=view.cursor_column,
        )
    else:
        matches = []
        cursor_target_index = -1

    update = search_change_update(
        value,
        matches=matches,
        cursor_target_index=cursor_target_index,
    )
    view._search_query = update.query
    view._search_matches = update.matches
    view._search_match_index = update.match_index

    refresh_search_display(view)
    if update.reveal_index is not None:
        reveal_match(view, update.reveal_index)
    view._update_status_line()


def handle_submitted(view: DiffView, query: str | None) -> None:
    """Apply a submitted in-file search query."""
    request = search_submission_request(query)
    matches = build_matches(view, request.query) if request.action == "search" else []
    cursor_target_index = (
        next_search_match_index(
            matches,
            current_row_index=view._current_row_index(),
            current_side=view._current_cursor_side(),
            current_column=view.cursor_column,
        )
        if matches
        else -1
    )
    update = search_submit_update(
        query,
        matches=matches,
        cursor_target_index=cursor_target_index,
    )

    if update.action == "ignore":
        return

    if update.action == "clear":
        clear_state(view)
        refresh_search_display(view)
        assert update.flash_message is not None
        view.post_message(Flash(update.flash_message, duration=1.5))
        view._update_status_line()
        return

    view._search_query = update.query
    view._search_matches = matches
    view._search_match_index = update.match_index
    if update.action == "no_matches":
        refresh_search_display(view)
        assert update.flash_message is not None
        assert update.flash_style is not None
        view.post_message(Flash(update.flash_message, style=update.flash_style))
        view._update_status_line()
        return

    refresh_search_display(view)
    activate_match(view, view._search_match_index)
