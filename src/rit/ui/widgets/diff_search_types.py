"""Typed policy records for DiffView in-file search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rit.ui.widgets.diff_types import DiffSearchMatch

SearchSide = Literal["old", "new", "auto"]
SearchPane = Literal["old", "new"]
SearchSubmissionAction = Literal["ignore", "clear", "search"]
SearchSubmitAction = Literal["ignore", "clear", "no_matches", "activate"]
FlashStyle = Literal["default", "warning", "success", "error"]
SearchJumpAction = Literal["inactive", "no_matches", "activate"]
SearchStartAction = Literal["ignore", "open"]
SearchCloseAction = Literal["ignore", "close"]
SearchRevealAction = Literal["ignore", "scroll_widget", "scroll_row"]
SearchActivationPlacementAction = Literal["jump_anchor", "move_cursor"]

__all__ = (
    "FlashStyle",
    "SearchActivationPlacementAction",
    "SearchActivationPlacementUpdate",
    "SearchActivationUpdate",
    "SearchChangeUpdate",
    "SearchCloseAction",
    "SearchCloseUpdate",
    "SearchHighlightSpan",
    "SearchJumpAction",
    "SearchJumpUpdate",
    "SearchMatchRefresh",
    "SearchPane",
    "SearchRefreshUpdate",
    "SearchRevealAction",
    "SearchRevealUpdate",
    "SearchSide",
    "SearchStartAction",
    "SearchStartUpdate",
    "SearchSubmissionAction",
    "SearchSubmissionRequest",
    "SearchSubmitAction",
    "SearchSubmitUpdate",
    "SearchSubmittedInputUpdate",
)


@dataclass(frozen=True)
class SearchRefreshUpdate:
    """Line refresh policy after search matches change."""

    dirty_lines: frozenset[int]
    previous_match_lines: frozenset[int]


@dataclass(frozen=True)
class SearchActivationUpdate:
    """Search match activation policy before UI side effects."""

    match: DiffSearchMatch
    dirty_lines: frozenset[int]
    pane: SearchPane | None
    update_active_pane: bool


@dataclass(frozen=True)
class SearchActivationPlacementUpdate:
    """Search match placement action after activation."""

    action: SearchActivationPlacementAction
    viewport_offset: int
    reveal_horizontal: bool


@dataclass(frozen=True)
class SearchSubmissionRequest:
    """Normalized search submission before UI side effects."""

    action: SearchSubmissionAction
    query: str


@dataclass(frozen=True)
class SearchHighlightSpan:
    """Search highlight span for one rendered line side."""

    start: int
    end: int
    style: str


@dataclass(frozen=True)
class SearchMatchRefresh:
    """Search matches and active index after rebuilding matches."""

    matches: list[DiffSearchMatch]
    match_index: int


@dataclass(frozen=True)
class SearchChangeUpdate:
    """Search state after inline search input changes."""

    query: str
    matches: list[DiffSearchMatch]
    match_index: int
    reveal_index: int | None


@dataclass(frozen=True)
class SearchSubmitUpdate:
    """Search state action after inline search submission."""

    action: SearchSubmitAction
    query: str
    match_index: int
    flash_message: str | None
    flash_style: FlashStyle | None


@dataclass(frozen=True)
class SearchJumpUpdate:
    """Search state action for repeated search navigation."""

    action: SearchJumpAction
    target_index: int
    flash_message: str | None
    flash_style: FlashStyle | None
    update_status: bool


@dataclass(frozen=True)
class SearchStartUpdate:
    """Search bar action for opening the inline search input."""

    action: SearchStartAction
    input_value: str
    focus_input: bool


@dataclass(frozen=True)
class SearchCloseUpdate:
    """Search bar action for closing the inline search input."""

    action: SearchCloseAction
    clear_state: bool
    refresh_display: bool
    update_status: bool
    focus_view: bool


@dataclass(frozen=True)
class SearchSubmittedInputUpdate:
    """Search bar action after submitting the inline search input."""

    close_bar: bool
    focus_view: bool
    submit_query: str


@dataclass(frozen=True)
class SearchRevealUpdate:
    """Search reveal action for scrolling a match into view."""

    action: SearchRevealAction
    viewport_offset: int
