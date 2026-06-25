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
    return _search_match_columns_casefolded(text, query.casefold())


def _search_match_columns_casefolded(text: str, needle: str) -> tuple[int, ...]:
    if not text or not needle:
        return ()

    text_lower = text.casefold()
    needle_len = len(needle)
    column = text_lower.find(needle)
    if column < 0:
        return ()

    first_column = column
    start = column + needle_len
    column = text_lower.find(needle, start)
    if column < 0:
        return (first_column,)

    columns = [first_column]
    while column >= 0:
        columns.append(column)
        start = column + needle_len
        column = text_lower.find(needle, start)
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
    return search_matches_for_text_casefolded(
        text=text,
        query=query.casefold(),
        row_index=row_index,
        line_index=line_index,
        side=side,
    )


def search_matches_for_text_casefolded(
    *,
    text: str,
    query: str,
    row_index: int,
    line_index: int,
    side: SearchSide,
) -> tuple[DiffSearchMatch, ...]:
    if not text or not query:
        return ()

    text_lower = text.casefold()
    needle_len = len(query)
    column = text_lower.find(query)
    if column < 0:
        return ()

    first_match = DiffSearchMatch(
        row_index=row_index,
        line_index=line_index,
        side=side,
        column=column,
    )
    start = column + needle_len
    column = text_lower.find(query, start)
    if column < 0:
        return (first_match,)

    matches = [first_match]
    while column >= 0:
        matches.append(
            DiffSearchMatch(
                row_index=row_index,
                line_index=line_index,
                side=side,
                column=column,
            )
        )
        start = column + needle_len
        column = text_lower.find(query, start)
    return tuple(matches)


def append_search_matches_for_text_casefolded(
    matches: list[DiffSearchMatch],
    *,
    text: str,
    query: str,
    row_index: int,
    line_index: int,
    side: SearchSide,
) -> None:
    if not text or not query:
        return

    text_lower = text.casefold()
    needle_len = len(query)
    start = 0
    while True:
        column = text_lower.find(query, start)
        if column < 0:
            return
        matches.append(
            DiffSearchMatch(
                row_index=row_index,
                line_index=line_index,
                side=side,
                column=column,
            )
        )
        start = column + needle_len


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
    first_span: SearchHighlightSpan | None = None
    spans: list[SearchHighlightSpan] | None = None
    for index, match in enumerate(matches):
        if match.line_index != line_index or match.side != side:
            continue
        span = SearchHighlightSpan(
            start=match.column,
            end=match.column + query_length,
            style=search_match_style(
                match_index=index,
                active_match_index=active_match_index,
            ),
        )
        if first_span is None:
            first_span = span
        elif spans is None:
            spans = [first_span, span]
        else:
            spans.append(span)

    if first_span is None:
        return ()
    if spans is None:
        return (first_span,)
    return tuple(spans)
