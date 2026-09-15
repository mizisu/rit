"""Ownership of one displayed diff's in-file search session."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Literal

from textual.content import Content

from rit.core.types import DiffLine
from rit.ui.widgets.diff_search_match_index import (
    SearchMatchesByLineSide,
    build_match_buckets,
    build_matches_from_rows,
)
from rit.ui.widgets.diff_search_matching import search_match_style
from rit.ui.widgets.diff_search_policy import (
    next_search_match_index,
    search_activation_update,
    search_change_update,
    search_jump_update,
    search_match_index_at_cursor,
    search_refresh_update,
    search_submission_request,
    search_submit_update,
)
from rit.ui.widgets.diff_search_types import (
    FlashStyle,
    SearchActivationUpdate,
    SearchSide,
)
from rit.ui.widgets.diff_types import DiffSearchMatch, RenderedRow

_ASYNC_SEARCH_ROW_THRESHOLD = 5_000
_SEARCH_DEBOUNCE_SECONDS = 0.05


@dataclass(frozen=True)
class SearchCursor:
    """Current cursor coordinates in the displayed diff."""

    row: int
    line: int
    side: SearchSide
    column: int


@dataclass(frozen=True)
class SearchResult:
    """Repaint and navigation effects for DiffView to perform."""

    dirty_lines: frozenset[int] = frozenset()
    reveal: DiffSearchMatch | None = None
    activation: SearchActivationUpdate | None = None
    flash_message: str | None = None
    flash_style: FlashStyle = "default"
    flash_duration: float | None = None


def _build_search_index(
    lines: Sequence[DiffLine], rows: Sequence[RenderedRow], query: str
) -> tuple[list[DiffSearchMatch], SearchMatchesByLineSide]:
    matches = build_matches_from_rows(lines, rows, query)
    return matches, build_match_buckets(matches)


class DiffSearchSession:
    """Keep query, result validity, navigation and highlight state together.

    Cursor coordinates are read at publication time, including after background
    matching. The result callback performs only the Textual effects.
    """

    def __init__(
        self,
        cursor: Callable[[], SearchCursor],
        display: Callable[[SearchResult], None],
    ) -> None:
        self._cursor = cursor
        self._display = display
        self._query = ""
        self._generation = 0
        self._matches: list[DiffSearchMatch] = []
        self._active_index = -1
        self._buckets: SearchMatchesByLineSide | None = None
        self._previous_match_lines: frozenset[int] = frozenset()

    @property
    def query(self) -> str:
        return self._query

    @property
    def matches(self) -> Sequence[DiffSearchMatch]:
        return self._matches

    @property
    def active_index(self) -> int:
        return self._active_index

    def clear(self, *, repaint: bool = True) -> None:
        """Invalidate outstanding results, clearing old highlights when requested."""
        self._generation += 1
        self._install("", [], -1)
        if repaint:
            self.repaint()

    def repaint(self) -> None:
        """Refresh current and previously highlighted lines."""
        self._display(SearchResult(dirty_lines=self._dirty_lines()))

    def _dirty_lines(self) -> frozenset[int]:
        update = search_refresh_update(
            self._matches, previous_match_lines=self._previous_match_lines
        )
        self._previous_match_lines = update.previous_match_lines
        return update.dirty_lines

    def _install(
        self,
        query: str,
        matches: list[DiffSearchMatch],
        active_index: int,
        buckets: SearchMatchesByLineSide | None = None,
    ) -> None:
        self._query = query
        self._matches = matches
        self._active_index = active_index
        self._buckets = buckets

    def search(
        self,
        value: str | None,
        lines: Sequence[DiffLine],
        rows: Sequence[RenderedRow],
        *,
        submitted: bool = False,
    ) -> Coroutine[None, None, None] | None:
        """Apply input now, or return background work for the widget to schedule."""
        self._generation += 1
        request = search_submission_request(value)
        if request.action == "search" and len(rows) >= _ASYNC_SEARCH_ROW_THRESHOLD:
            return self._search_async(
                value, request.query, lines, rows, self._generation, submitted
            )
        matches = (
            build_matches_from_rows(lines, rows, request.query)
            if request.action == "search"
            else []
        )
        self._publish(value, matches, submitted=submitted)
        return None

    async def _search_async(
        self,
        value: str | None,
        query: str,
        lines: Sequence[DiffLine],
        rows: Sequence[RenderedRow],
        generation: int,
        submitted: bool,
    ) -> None:
        if not submitted:
            await asyncio.sleep(_SEARCH_DEBOUNCE_SECONDS)
        matches, buckets = await asyncio.to_thread(
            _build_search_index, lines, rows, query
        )
        if generation != self._generation:
            return
        self._publish(value, matches, submitted=submitted, buckets=buckets)

    def _next_index(self, matches: list[DiffSearchMatch]) -> int:
        if not matches:
            return -1
        cursor = self._cursor()
        return next_search_match_index(
            matches,
            current_row_index=cursor.row,
            current_side=cursor.side,
            current_column=cursor.column,
        )

    def _publish(
        self,
        value: str | None,
        matches: list[DiffSearchMatch],
        *,
        submitted: bool,
        buckets: SearchMatchesByLineSide | None = None,
    ) -> None:
        target = self._next_index(matches)
        if not submitted:
            assert value is not None
            change = search_change_update(
                value, matches=matches, cursor_target_index=target
            )
            self._install(change.query, change.matches, change.match_index, buckets)
            self._display(
                SearchResult(
                    dirty_lines=self._dirty_lines(),
                    reveal=matches[change.reveal_index]
                    if change.reveal_index is not None
                    else None,
                )
            )
            return

        update = search_submit_update(
            value, matches=matches, cursor_target_index=target
        )
        if update.action == "ignore":
            return
        if update.action == "clear":
            self.clear(repaint=False)
        else:
            self._install(update.query, matches, update.match_index, buckets)
        self._display(
            SearchResult(
                dirty_lines=self._dirty_lines(),
                activation=self._activate(self._active_index)
                if update.action == "activate"
                else None,
                flash_message=update.flash_message,
                flash_style=update.flash_style or "default",
                flash_duration=1.5 if update.action == "clear" else None,
            )
        )

    def refresh(self, lines: Sequence[DiffLine], rows: Sequence[RenderedRow]) -> None:
        """Invalidate background work and rebuild for a changed row projection."""
        self._generation += 1
        matches = build_matches_from_rows(lines, rows, self._query) if self._query else []
        self._install(self._query, matches, -1)
        self.sync_cursor()

    def sync_cursor(self) -> None:
        """Align the active match with the cursor without moving or repainting it."""
        if not self._matches:
            self._active_index = -1
            return
        cursor = self._cursor()
        self._active_index = search_match_index_at_cursor(
            self._matches,
            current_line=cursor.line,
            current_side=cursor.side,
            current_column=cursor.column,
        )

    def _activate(self, index: int) -> SearchActivationUpdate | None:
        activation = search_activation_update(
            self._matches, old_index=self._active_index, target_index=index
        )
        if activation is not None:
            self._active_index = index
        return activation

    def jump(
        self,
        direction: Literal[-1, 1],
        lines: Sequence[DiffLine],
        rows: Sequence[RenderedRow],
    ) -> None:
        """Rebuild and activate the next or previous match from the cursor."""
        if self._query:
            self.refresh(lines, rows)
        update = search_jump_update(
            query=self._query,
            match_count=len(self._matches),
            current_match_index=self._active_index,
            cursor_target_index=self._next_index(self._matches),
            direction=direction,
        )
        self._display(
            SearchResult(
                activation=self._activate(update.target_index)
                if update.action == "activate"
                else None,
                flash_message=update.flash_message,
                flash_style=update.flash_style or "default",
            )
        )

    def highlight(self, content: Content, line_index: int, side: SearchSide) -> Content:
        """Highlight one line using the session's reusable line-side index."""
        if not self._query or not self._matches:
            return content
        if self._buckets is None:
            self._buckets = build_match_buckets(self._matches)
        for index, match in self._buckets.get((line_index, side), ()):
            content = content.stylize(
                search_match_style(
                    match_index=index, active_match_index=self._active_index
                ),
                match.column,
                match.column + len(self._query),
            )
        return content
