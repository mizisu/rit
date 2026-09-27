"""Pure row matching and line-side indexing for diff search."""

from __future__ import annotations

from collections.abc import Sequence

from rit.core.types import DiffLine
from rit.ui.widgets.diff_plan import RenderedRows
from rit.ui.widgets.diff_search_matching import (
    append_search_matches_for_text_casefolded,
    search_sides_for_line,
)
from rit.ui.widgets.diff_search_types import SearchSide
from rit.ui.widgets.diff_types import DiffSearchMatch, RenderedRow

type SearchMatchBucket = tuple[tuple[int, DiffSearchMatch], ...]
type SearchMatchesByLineSide = dict[tuple[int, SearchSide], SearchMatchBucket]
type _SearchMatchBucketBuilder = SearchMatchBucket | list[tuple[int, DiffSearchMatch]]


def build_matches_from_rows(
    lines: Sequence[DiffLine],
    rows: Sequence[RenderedRow],
    query: str,
) -> list[DiffSearchMatch]:
    """Build search matches from a stable row snapshot."""
    if not query:
        return []

    query = query.casefold()
    matches: list[DiffSearchMatch] = []
    # Search must not churn the viewport's bounded row-view cache.
    data_rows = rows.iter_data() if isinstance(rows, RenderedRows) else iter(rows)
    for mode, row_index, line_index, _, _, row_side, _, _, _ in data_rows:
        line = lines[line_index]
        sides = search_sides_for_line(
            row_mode=mode,
            row_side=row_side,
            line_is_modified=line.is_modified,
            line_is_deleted=line.is_deleted,
            line_is_added=line.is_added,
        )
        for side in sides:
            if side == "old":
                text = line.old_content
            elif side == "new" or line.has_new_side:
                text = line.new_content
            else:
                text = line.old_content if line.has_old_side else ""
            append_search_matches_for_text_casefolded(
                matches,
                text=text,
                query=query,
                row_index=row_index,
                line_index=line_index,
                side=side,
            )
    return matches


def build_match_buckets(matches: list[DiffSearchMatch]) -> SearchMatchesByLineSide:
    """Index matches by line and side for rendered-line highlighting."""
    return _build_search_matches_by_line_side(matches)


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
