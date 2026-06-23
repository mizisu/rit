"""Pure text matching helpers for DiffView in-file search."""

from __future__ import annotations

from typing import Literal

from rit.ui.widgets.diff_search_types import SearchHighlightSpan, SearchSide
from rit.ui.widgets.diff_types import DiffSearchMatch

__all__ = (
    "search_highlight_spans",
    "search_match_columns",
    "search_match_style",
    "search_matches_for_text",
    "search_sides_for_line",
)


def search_sides_for_line(
    *,
    row_mode: Literal["unified", "split"],
    row_side: SearchSide,
    line_is_modified: bool,
    line_is_deleted: bool,
    line_is_added: bool,
) -> tuple[SearchSide, ...]:
    """Return the sides that should be searched for one rendered row."""
    if row_mode == "unified":
        return (row_side,)
    if line_is_modified:
        return ("old", "new")
    if line_is_deleted:
        return ("old",)
    if line_is_added:
        return ("new",)
    return ("auto",)


def search_match_columns(text: str, query: str) -> tuple[int, ...]:
    """Return non-overlapping case-insensitive match columns."""
    needle = query.casefold()
    if not text or not needle:
        return ()

    text_lower = text.casefold()
    needle_len = len(needle)
    columns: list[int] = []
    start = 0
    while True:
        column = text_lower.find(needle, start)
        if column < 0:
            break
        columns.append(column)
        start = column + needle_len
    return tuple(columns)


def search_matches_for_text(
    *,
    text: str,
    query: str,
    row_index: int,
    line_index: int,
    side: SearchSide,
) -> tuple[DiffSearchMatch, ...]:
    """Return search matches for one rendered row side."""
    return tuple(
        DiffSearchMatch(
            row_index=row_index,
            line_index=line_index,
            side=side,
            column=column,
        )
        for column in search_match_columns(text, query)
    )


def search_match_style(*, match_index: int, active_match_index: int) -> str:
    """Return the highlight style for a search match."""
    if match_index == active_match_index:
        return "on $warning 45%"
    return "on $warning 25%"


def search_highlight_spans(
    matches: list[DiffSearchMatch],
    *,
    line_index: int,
    side: SearchSide,
    query_length: int,
    active_match_index: int,
) -> tuple[SearchHighlightSpan, ...]:
    """Return search highlight spans for one rendered line side."""
    spans: list[SearchHighlightSpan] = []
    for index, match in enumerate(matches):
        if match.line_index != line_index or match.side != side:
            continue
        spans.append(
            SearchHighlightSpan(
                start=match.column,
                end=match.column + query_length,
                style=search_match_style(
                    match_index=index,
                    active_match_index=active_match_index,
                ),
            )
        )
    return tuple(spans)
