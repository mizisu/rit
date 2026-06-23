"""DiffView adapters for the inline search bar."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rit.ui.widgets.diff_search_commands import clear_state, handle_submitted
from rit.ui.widgets.diff_search_display import refresh_search_display
from rit.ui.widgets.diff_search_policy import (
    search_close_update,
    search_start_update,
    search_submitted_input_update,
)

__all__ = (
    "close_search",
    "handle_submitted_input",
    "start_search",
)

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


def handle_submitted_input(view: DiffView, value: str) -> None:
    """Close the inline search input and apply the submitted query."""
    update = search_submitted_input_update(
        has_bar=view._search_bar_widget is not None,
        value=value,
    )
    if update.close_bar:
        assert view._search_bar_widget is not None
        view._search_bar_widget.display = False
    if update.focus_view:
        view.focus()
    handle_submitted(view, update.submit_query)


def close_search(view: DiffView, *, clear_query: bool) -> None:
    """Close the inline search input and optionally clear active search state."""
    bar = view._search_bar_widget
    update = search_close_update(
        has_bar=bar is not None,
        bar_displayed=bool(bar.display) if bar is not None else False,
        clear_query=clear_query,
    )
    if update.action == "ignore":
        return
    assert bar is not None
    bar.display = False
    if update.clear_state:
        clear_state(view)
    if update.refresh_display:
        refresh_search_display(view)
    if update.update_status:
        view._update_status_line()
    if update.focus_view:
        view.focus()


def start_search(view: DiffView) -> None:
    """Open the inline search input and restore the current search query."""
    bar = view._search_bar_widget
    search_input = view._search_input_widget
    update = search_start_update(
        has_bar=bar is not None,
        has_input=search_input is not None,
        query=view._search_query,
    )
    if update.action == "ignore":
        return
    assert bar is not None
    assert search_input is not None
    bar.display = True
    search_input.value = update.input_value
    if update.focus_input:
        search_input.focus()
