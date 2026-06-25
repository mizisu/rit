"""Pure policy helpers for DiffView in-file search."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Set
from typing import Literal

from rit.ui.widgets.diff_search_types import (
    SearchActivationPlacementUpdate,
    SearchActivationUpdate,
    SearchChangeUpdate,
    SearchCloseUpdate,
    SearchJumpUpdate,
    SearchMatchRefresh,
    SearchRefreshUpdate,
    SearchRevealUpdate,
    SearchSide,
    SearchStartUpdate,
    SearchSubmissionRequest,
    SearchSubmitUpdate,
    SearchSubmittedInputUpdate,
)
from rit.ui.widgets.diff_types import DiffSearchMatch

_EMPTY_LINE_SET: frozenset[int] = frozenset()

__all__ = (
    "next_search_match_index",
    "search_activation_placement_update",
    "search_activation_update",
    "search_change_update",
    "search_close_update",
    "search_jump_target_index",
    "search_jump_update",
    "search_match_index_at_cursor",
    "search_match_refresh",
    "search_refresh_update",
    "search_reveal_update",
    "search_start_update",
    "search_submission_request",
    "search_submit_update",
    "search_submitted_input_update",
)


def _empty_line_set() -> frozenset[int]:
    return _EMPTY_LINE_SET


def _single_line_set(line: int) -> frozenset[int]:
    return frozenset((line,))


def _line_pair_set(first: int, second: int) -> frozenset[int]:
    if first == second:
        return _single_line_set(first)
    return frozenset((first, second))


def search_reveal_update(
    *,
    target_exists: bool,
    has_target_widget: bool,
    target_visible: bool,
) -> SearchRevealUpdate:
    """Return scroll policy for revealing a search match."""
    if not target_exists:
        return SearchRevealUpdate(action="ignore", viewport_offset=0)
    if has_target_widget:
        return SearchRevealUpdate(action="scroll_widget", viewport_offset=0)
    if target_visible:
        return SearchRevealUpdate(action="ignore", viewport_offset=0)
    return SearchRevealUpdate(action="scroll_row", viewport_offset=0)


def search_refresh_update(
    matches: list[DiffSearchMatch],
    *,
    previous_match_lines: Set[int],
) -> SearchRefreshUpdate:
    """Return line refresh policy after search matches change."""
    if not matches:
        current_lines = _empty_line_set()
    elif len(matches) == 1:
        current_lines = _single_line_set(matches[0].line_index)
    elif len(matches) == 2:
        current_lines = _line_pair_set(matches[0].line_index, matches[1].line_index)
    else:
        current_lines = frozenset(match.line_index for match in matches)
    if not current_lines:
        if not previous_match_lines:
            return SearchRefreshUpdate(
                dirty_lines=current_lines,
                previous_match_lines=current_lines,
            )
        return SearchRefreshUpdate(
            dirty_lines=frozenset(previous_match_lines),
            previous_match_lines=current_lines,
        )
    if not previous_match_lines:
        return SearchRefreshUpdate(
            dirty_lines=current_lines,
            previous_match_lines=current_lines,
        )
    if current_lines == previous_match_lines:
        return SearchRefreshUpdate(
            dirty_lines=current_lines,
            previous_match_lines=current_lines,
        )
    return SearchRefreshUpdate(
        dirty_lines=current_lines | frozenset(previous_match_lines),
        previous_match_lines=current_lines,
    )


def search_activation_update(
    matches: list[DiffSearchMatch],
    *,
    old_index: int,
    target_index: int,
) -> SearchActivationUpdate | None:
    """Return the non-UI policy for activating a search match."""
    if not (0 <= target_index < len(matches)):
        return None

    match = matches[target_index]
    dirty_lines = _single_line_set(match.line_index)
    if 0 <= old_index < len(matches):
        dirty_lines = _line_pair_set(match.line_index, matches[old_index].line_index)

    pane = None if match.side == "auto" else match.side
    return SearchActivationUpdate(
        match=match,
        dirty_lines=dirty_lines,
        pane=pane,
        update_active_pane=match.side != "auto",
    )


def search_activation_placement_update(
    *,
    has_target_row: bool,
    target_row_visible: bool,
    has_current_row: bool,
    row_distance: int,
    half_page_step: int,
) -> SearchActivationPlacementUpdate:
    """Return placement policy after activating a search match."""
    should_anchor = has_target_row and (
        not target_row_visible
        or not has_current_row
        or row_distance > half_page_step
    )
    if should_anchor:
        return SearchActivationPlacementUpdate(
            action="jump_anchor",
            viewport_offset=0,
            reveal_horizontal=True,
        )
    return SearchActivationPlacementUpdate(
        action="move_cursor",
        viewport_offset=0,
        reveal_horizontal=False,
    )


def search_match_index_at_cursor(
    matches: list[DiffSearchMatch],
    *,
    current_line: int,
    current_side: SearchSide,
    current_column: int,
) -> int:
    """Return the search match index exactly under the cursor."""
    position = (current_line, current_column)
    start = bisect_left(
        matches,
        position,
        key=lambda match: (match.line_index, match.column),
    )
    end = bisect_right(
        matches,
        position,
        key=lambda match: (match.line_index, match.column),
    )
    for index in range(start, end):
        if matches[index].side == current_side:
            return index
    return -1


def search_match_refresh(
    *,
    query: str,
    matches: list[DiffSearchMatch],
    current_line: int,
    current_side: SearchSide,
    current_column: int,
) -> SearchMatchRefresh:
    """Return rebuilt search matches and the active index for the cursor."""
    if not query:
        return SearchMatchRefresh(matches=[], match_index=-1)
    return SearchMatchRefresh(
        matches=matches,
        match_index=search_match_index_at_cursor(
            matches,
            current_line=current_line,
            current_side=current_side,
            current_column=current_column,
        ),
    )


def search_change_update(
    value: str,
    *,
    matches: list[DiffSearchMatch],
    cursor_target_index: int,
) -> SearchChangeUpdate:
    """Return search state after an inline search input change."""
    request = search_submission_request(value)
    if request.action != "search":
        return SearchChangeUpdate(
            query="",
            matches=[],
            match_index=-1,
            reveal_index=None,
        )

    reveal_index = cursor_target_index if cursor_target_index >= 0 else None
    return SearchChangeUpdate(
        query=request.query,
        matches=matches,
        match_index=cursor_target_index,
        reveal_index=reveal_index,
    )


def next_search_match_index(
    matches: list[DiffSearchMatch],
    *,
    current_row_index: int,
    current_side: SearchSide,
    current_column: int,
) -> int:
    """Return the next search match index from the cursor position."""
    if not matches:
        return -1

    position = (current_row_index, current_column)
    equal_start = bisect_left(
        matches,
        position,
        key=lambda match: (match.row_index, match.column),
    )
    equal_end = bisect_right(
        matches,
        position,
        key=lambda match: (match.row_index, match.column),
    )
    for index in range(equal_start, equal_end):
        if matches[index].side != current_side:
            return index

    if equal_end < len(matches):
        return equal_end

    return 0


def search_jump_target_index(
    *,
    current_match_index: int,
    match_count: int,
    cursor_target_index: int,
    direction: Literal[-1, 1],
) -> int:
    """Return the match index targeted by a repeated search jump."""
    if match_count <= 0:
        return -1
    if current_match_index < 0:
        return match_count - 1 if direction < 0 else cursor_target_index
    return (current_match_index + direction) % match_count


def search_jump_update(
    *,
    query: str,
    match_count: int,
    current_match_index: int,
    cursor_target_index: int,
    direction: Literal[-1, 1],
) -> SearchJumpUpdate:
    """Return search state action for repeated search navigation."""
    if not query:
        return SearchJumpUpdate(
            action="inactive",
            target_index=-1,
            flash_message="No active search",
            flash_style="warning",
            update_status=False,
        )
    if match_count <= 0:
        return SearchJumpUpdate(
            action="no_matches",
            target_index=-1,
            flash_message=f"No matches: {query}",
            flash_style="warning",
            update_status=True,
        )
    return SearchJumpUpdate(
        action="activate",
        target_index=search_jump_target_index(
            current_match_index=current_match_index,
            match_count=match_count,
            cursor_target_index=cursor_target_index,
            direction=direction,
        ),
        flash_message=None,
        flash_style=None,
        update_status=False,
    )


def search_submission_request(query: str | None) -> SearchSubmissionRequest:
    """Return the normalized action for a submitted search query."""
    if query is None:
        return SearchSubmissionRequest(action="ignore", query="")

    normalized = query.strip()
    if not normalized:
        return SearchSubmissionRequest(action="clear", query="")
    return SearchSubmissionRequest(action="search", query=normalized)


def search_submit_update(
    query: str | None,
    *,
    matches: list[DiffSearchMatch],
    cursor_target_index: int,
) -> SearchSubmitUpdate:
    """Return search state action for a submitted query."""
    request = search_submission_request(query)
    if request.action == "ignore":
        return SearchSubmitUpdate(
            action="ignore",
            query="",
            match_index=-1,
            flash_message=None,
            flash_style=None,
        )
    if request.action == "clear":
        return SearchSubmitUpdate(
            action="clear",
            query="",
            match_index=-1,
            flash_message="Search cleared",
            flash_style=None,
        )
    if not matches:
        return SearchSubmitUpdate(
            action="no_matches",
            query=request.query,
            match_index=-1,
            flash_message=f"No matches: {request.query}",
            flash_style="warning",
        )
    return SearchSubmitUpdate(
        action="activate",
        query=request.query,
        match_index=cursor_target_index,
        flash_message=None,
        flash_style=None,
    )


def search_start_update(
    *,
    has_bar: bool,
    has_input: bool,
    query: str,
) -> SearchStartUpdate:
    """Return search bar action for opening the inline search input."""
    if not has_bar or not has_input:
        return SearchStartUpdate(
            action="ignore",
            input_value="",
            focus_input=False,
        )
    return SearchStartUpdate(
        action="open",
        input_value=query,
        focus_input=True,
    )


def search_close_update(
    *,
    has_bar: bool,
    bar_displayed: bool,
    clear_query: bool,
) -> SearchCloseUpdate:
    """Return search bar action for closing the inline search input."""
    if not has_bar or not bar_displayed:
        return SearchCloseUpdate(
            action="ignore",
            clear_state=False,
            refresh_display=False,
            update_status=False,
            focus_view=False,
        )
    return SearchCloseUpdate(
        action="close",
        clear_state=clear_query,
        refresh_display=True,
        update_status=True,
        focus_view=True,
    )


def search_submitted_input_update(
    *,
    has_bar: bool,
    value: str,
) -> SearchSubmittedInputUpdate:
    """Return search bar action after submitting the inline search input."""
    return SearchSubmittedInputUpdate(
        close_bar=has_bar,
        focus_view=True,
        submit_query=value,
    )
