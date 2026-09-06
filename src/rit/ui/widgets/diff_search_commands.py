"""DiffView command adapters for applying in-file search input."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING

from rit.core.types import DiffLine
from rit.ui.messages import Flash
from rit.ui.widgets.diff_search_display import refresh_search_display
from rit.ui.widgets.diff_search_match_index import (
    SearchMatchesByLineSide,
    build_match_buckets,
    build_matches,
    build_matches_from_rows,
)
from rit.ui.widgets.diff_search_navigation import activate_match, reveal_match
from rit.ui.widgets.diff_search_policy import (
    next_search_match_index,
    search_change_update,
    search_submission_request,
    search_submit_update,
)
from rit.ui.widgets.diff_types import DiffSearchMatch, RenderedRow

__all__ = (
    "clear_state",
    "handle_changed",
    "handle_submitted",
)

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


_ASYNC_SEARCH_ROW_THRESHOLD = 5_000
_SEARCH_DEBOUNCE_SECONDS = 0.05


def clear_state(view: DiffView) -> None:
    """Clear active in-file search state."""
    view._search_request_token += 1
    view._search_query = ""
    view._search_matches = []
    view._search_match_index = -1
    view._search_matches_by_line_side = {}
    view._search_matches_by_line_side_source = None


def handle_changed(view: DiffView, value: str) -> None:
    """Update search state after the inline search input changes."""
    view._search_request_token += 1
    request_token = view._search_request_token
    request = search_submission_request(value)
    rows = view._rows_for_current_mode()
    if request.action == "search" and len(rows) >= _ASYNC_SEARCH_ROW_THRESHOLD:
        view.run_worker(
            _handle_changed_async(
                view,
                value,
                request.query,
                view._all_lines,
                rows,
                request_token,
            ),
            group="diff-search",
            exclusive=True,
            name="diff-search-change",
        )
        return

    matches = build_matches(view, request.query) if request.action == "search" else []
    _apply_changed(view, value, matches)


async def _handle_changed_async(
    view: DiffView,
    value: str,
    query: str,
    lines: Sequence[DiffLine],
    rows: Sequence[RenderedRow],
    request_token: int,
) -> None:
    await asyncio.sleep(_SEARCH_DEBOUNCE_SECONDS)
    matches, buckets = await asyncio.to_thread(
        _build_search_index,
        lines,
        rows,
        query,
    )
    if request_token != view._search_request_token:
        return
    _apply_changed(view, value, matches, buckets=buckets)


def _build_search_index(
    lines: Sequence[DiffLine],
    rows: Sequence[RenderedRow],
    query: str,
) -> tuple[list[DiffSearchMatch], SearchMatchesByLineSide]:
    matches = build_matches_from_rows(lines, rows, query)
    return matches, build_match_buckets(matches)


def _apply_changed(
    view: DiffView,
    value: str,
    matches: list[DiffSearchMatch],
    *,
    buckets: SearchMatchesByLineSide | None = None,
) -> None:
    cursor_target_index = next_search_match_index(
        matches,
        current_row_index=view._current_row_index(),
        current_side=view._current_cursor_side(),
        current_column=view.cursor_column,
    )
    update = search_change_update(
        value,
        matches=matches,
        cursor_target_index=cursor_target_index,
    )
    view._search_query = update.query
    view._search_matches = update.matches
    view._search_match_index = update.match_index
    view._search_matches_by_line_side = buckets or {}
    view._search_matches_by_line_side_source = (
        (id(update.matches), len(update.matches)) if buckets is not None else None
    )

    refresh_search_display(view)
    if update.reveal_index is not None:
        reveal_match(view, update.reveal_index)


def handle_submitted(view: DiffView, query: str | None) -> None:
    """Apply a submitted in-file search query."""
    view._search_request_token += 1
    request_token = view._search_request_token
    request = search_submission_request(query)
    rows = view._rows_for_current_mode()
    if request.action == "search" and len(rows) >= _ASYNC_SEARCH_ROW_THRESHOLD:
        view.run_worker(
            _handle_submitted_async(
                view,
                query,
                request.query,
                view._all_lines,
                rows,
                request_token,
            ),
            group="diff-search",
            exclusive=True,
            name="diff-search-submit",
        )
        return

    matches = build_matches(view, request.query) if request.action == "search" else []
    _apply_submitted(view, query, matches)


async def _handle_submitted_async(
    view: DiffView,
    query: str | None,
    normalized_query: str,
    lines: Sequence[DiffLine],
    rows: Sequence[RenderedRow],
    request_token: int,
) -> None:
    matches, buckets = await asyncio.to_thread(
        _build_search_index,
        lines,
        rows,
        normalized_query,
    )
    if request_token != view._search_request_token:
        return
    _apply_submitted(view, query, matches, buckets=buckets)


def _apply_submitted(
    view: DiffView,
    query: str | None,
    matches: list[DiffSearchMatch],
    *,
    buckets: SearchMatchesByLineSide | None = None,
) -> None:
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
        return

    view._search_query = update.query
    view._search_matches = matches
    view._search_match_index = update.match_index
    view._search_matches_by_line_side = buckets or {}
    view._search_matches_by_line_side_source = (
        (id(matches), len(matches)) if buckets is not None else None
    )
    if update.action == "no_matches":
        refresh_search_display(view)
        assert update.flash_message is not None
        assert update.flash_style is not None
        view.post_message(Flash(update.flash_message, style=update.flash_style))
        return

    refresh_search_display(view)
    activate_match(view, view._search_match_index)
