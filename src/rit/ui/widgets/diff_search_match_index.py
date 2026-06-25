"""DiffView adapters for search match indexing and projection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.content import Content

from rit.ui.widgets.diff_search_matching import (
    append_search_matches_for_text_casefolded,
    search_match_style,
    search_sides_for_line,
)
from rit.ui.widgets.diff_search_policy import (
    next_search_match_index,
    search_match_index_at_cursor,
    search_match_refresh,
)
from rit.ui.widgets.diff_search_types import SearchSide
from rit.ui.widgets.diff_types import DiffSearchMatch, RenderedRow

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


type SearchMatchIndexSource = tuple[int, int]
type SearchMatchBucket = tuple[tuple[int, DiffSearchMatch], ...]
type SearchMatchesByLineSide = dict[tuple[int, SearchSide], SearchMatchBucket]
type _SearchMatchBucketBuilder = (
    SearchMatchBucket | list[tuple[int, DiffSearchMatch]]
)


def search_sides_for_row(
    view: DiffView,
    row: DiffSearchMatch | object,
) -> tuple[SearchSide, ...]:
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

    query = query.casefold()
    matches: list[DiffSearchMatch] = []
    for row in view._rows_for_current_mode():
        line = view._all_lines[row.line_index]
        for side in search_sides_for_row(view, row):
            text = view._get_line_text(line, side)
            append_search_matches_for_text_casefolded(
                matches,
                text=text,
                query=query,
                row_index=row.row_index,
                line_index=row.line_index,
                side=side,
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

    for match_index, match in _search_matches_for_line_side(
        view,
        line_index=line_index,
        side=side,
    ):
        result = result.stylize(
            search_match_style(
                match_index=match_index,
                active_match_index=active_idx,
            ),
            match.column,
            match.column + needle_len,
        )

    return result


def _search_matches_for_line_side(
    view: DiffView,
    *,
    line_index: int,
    side: SearchSide,
) -> SearchMatchBucket:
    source = _search_match_index_source(view._search_matches)
    if view._search_matches_by_line_side_source != source:
        view._search_matches_by_line_side = _build_search_matches_by_line_side(
            view._search_matches
        )
        view._search_matches_by_line_side_source = source
    return view._search_matches_by_line_side.get((line_index, side), ())


def _search_match_index_source(
    matches: list[DiffSearchMatch],
) -> SearchMatchIndexSource:
    return (id(matches), len(matches))


def _build_search_matches_by_line_side(
    matches: list[DiffSearchMatch],
) -> SearchMatchesByLineSide:
    match_count = len(matches)
    if match_count == 0:
        return {}
    if match_count == 1:
        match = matches[0]
        return {(match.line_index, match.side): ((0, match),)}

    buckets: dict[tuple[int, SearchSide], _SearchMatchBucketBuilder] = {}
    for match_index, match in enumerate(matches):
        key = (match.line_index, match.side)
        entry = (match_index, match)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = (entry,)
        elif isinstance(bucket, list):
            bucket.append(entry)
        else:
            buckets[key] = [bucket[0], entry]

    return {
        key: tuple(bucket) if isinstance(bucket, list) else bucket
        for key, bucket in buckets.items()
    }


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
