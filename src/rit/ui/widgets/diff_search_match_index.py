"""DiffView adapters for search match indexing and projection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.content import Content

from rit.ui.widgets.diff_search_matching import (
    search_highlight_spans,
    search_matches_for_text,
    search_sides_for_line,
)
from rit.ui.widgets.diff_search_policy import (
    next_search_match_index,
    search_match_index_at_cursor,
    search_match_refresh,
)
from rit.ui.widgets.diff_search_types import SearchSide
from rit.ui.widgets.diff_types import DiffSearchMatch

__all__ = (
    "apply_search_highlights",
    "build_matches",
    "next_match_index_from_cursor",
    "refresh_matches",
    "search_sides_for_row",
    "sync_match_index_to_cursor",
)

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


def search_sides_for_row(
    view: DiffView,
    row: DiffSearchMatch | object,
) -> tuple[SearchSide, ...]:
    from rit.ui.widgets.diff_types import RenderedRow

    assert isinstance(row, RenderedRow)
    line = view._all_lines[row.line_index]
    return search_sides_for_line(
        row_mode=row.mode,
        row_side=row.side,
        line_is_modified=line.is_modified,
        line_is_deleted=line.is_deleted,
        line_is_added=line.is_added,
    )


def build_matches(view: DiffView, query: str) -> list[DiffSearchMatch]:
    """Build search matches for the rows currently visible in the diff mode."""
    if not query:
        return []

    matches: list[DiffSearchMatch] = []
    for row in view._rows_for_current_mode():
        line = view._all_lines[row.line_index]
        for side in search_sides_for_row(view, row):
            text = view._get_line_text(line, side)
            matches.extend(
                search_matches_for_text(
                    text=text,
                    query=query,
                    row_index=row.row_index,
                    line_index=row.line_index,
                    side=side,
                )
            )
    return matches


def apply_search_highlights(
    view: DiffView,
    content: Content,
    line_index: int,
    side: SearchSide,
) -> Content:
    """Apply search match highlighting to one rendered line."""
    if not view._search_query or not view._search_matches:
        return content

    needle_len = len(view._search_query)
    active_idx = view._search_match_index
    result = content

    for span in search_highlight_spans(
        view._search_matches,
        line_index=line_index,
        side=side,
        query_length=needle_len,
        active_match_index=active_idx,
    ):
        result = result.stylize(span.style, span.start, span.end)

    return result


def refresh_matches(view: DiffView) -> None:
    """Rebuild search matches and align the active match with the cursor."""
    matches = build_matches(view, view._search_query) if view._search_query else []
    refresh = search_match_refresh(
        query=view._search_query,
        matches=matches,
        current_line=view.cursor_line,
        current_side=view._current_cursor_side(),
        current_column=view.cursor_column,
    )
    view._search_matches = refresh.matches
    view._search_match_index = refresh.match_index


def sync_match_index_to_cursor(view: DiffView) -> None:
    """Update the active search match when the cursor lands exactly on a match."""
    if not view._search_matches:
        view._search_match_index = -1
        return

    view._search_match_index = search_match_index_at_cursor(
        view._search_matches,
        current_line=view.cursor_line,
        current_side=view._current_cursor_side(),
        current_column=view.cursor_column,
    )


def next_match_index_from_cursor(view: DiffView) -> int:
    """Return the next search match index from the current cursor position."""
    return next_search_match_index(
        view._search_matches,
        current_row_index=view._current_row_index(),
        current_side=view._current_cursor_side(),
        current_column=view.cursor_column,
    )
