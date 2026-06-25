"""Visual mode state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rit.ui.widgets.diff_selection_range import SelectionKind

VisualLineSelectionRole = Literal["none", "selected", "anchor"]
_EMPTY_LINE_SET: frozenset[int] = frozenset()

__all__ = (
    "VisualAnchorUIUpdate",
    "VisualLineSelectionRole",
    "VisualModeState",
    "VisualModeUIUpdate",
    "VisualQueueUpdate",
    "VisualQueuedUpdate",
    "VisualTypeUIUpdate",
    "allows_column_motion",
    "enter_visual_mode",
    "exit_visual_mode",
    "toggle_visual_mode",
    "visual_anchor_ui_update",
    "visual_line_selection_role",
    "visual_mode_sub_title",
    "visual_mode_ui_update",
    "visual_queue_update",
    "visual_type_ui_update",
)


@dataclass(frozen=True)
class VisualModeState:
    """Visual mode state after a transition."""

    visual_mode: bool
    visual_type: SelectionKind
    visual_anchor_line: int | None
    visual_anchor_column: int | None


@dataclass(frozen=True)
class VisualModeUIUpdate:
    """UI update policy after visual mode changes."""

    sub_title: str
    selection_refresh_lines: frozenset[int]
    clear_selection: bool
    update_status_line: bool


@dataclass(frozen=True)
class VisualTypeUIUpdate:
    """UI update policy after visual type changes."""

    sub_title: str | None
    selection_dirty_lines: frozenset[int]
    update_status_line: bool


@dataclass(frozen=True)
class VisualAnchorUIUpdate:
    """UI update policy after visual anchor changes."""

    selection_dirty_lines: frozenset[int]


@dataclass(frozen=True)
class VisualQueueUpdate:
    """Cursor UI flush policy for visual-mode updates."""

    selection_dirty_lines: frozenset[int] | None
    update_status_line: bool


type VisualQueuedUpdate = VisualTypeUIUpdate | VisualAnchorUIUpdate


def _empty_line_set() -> frozenset[int]:
    return _EMPTY_LINE_SET


def _single_line_set(line: int) -> frozenset[int]:
    return frozenset((line,))


def enter_visual_mode(
    *,
    visual_type: SelectionKind,
    current_visual_mode: bool,
    current_visual_anchor_line: int | None,
    current_visual_anchor_column: int | None,
    cursor_line: int,
    cursor_column: int,
) -> VisualModeState:
    """Return state after entering visual mode."""
    if not current_visual_mode:
        return VisualModeState(
            visual_mode=True,
            visual_type=visual_type,
            visual_anchor_line=cursor_line,
            visual_anchor_column=cursor_column,
        )

    return VisualModeState(
        visual_mode=True,
        visual_type=visual_type,
        visual_anchor_line=(
            cursor_line
            if current_visual_anchor_line is None
            else current_visual_anchor_line
        ),
        visual_anchor_column=(
            cursor_column
            if current_visual_anchor_column is None
            else current_visual_anchor_column
        ),
    )


def exit_visual_mode(*, current_visual_type: SelectionKind) -> VisualModeState:
    """Return state after exiting visual mode."""
    return VisualModeState(
        visual_mode=False,
        visual_type=current_visual_type,
        visual_anchor_line=None,
        visual_anchor_column=None,
    )


def toggle_visual_mode(
    *,
    requested_visual_type: SelectionKind,
    current_visual_mode: bool,
    current_visual_type: SelectionKind,
    current_visual_anchor_line: int | None,
    current_visual_anchor_column: int | None,
    cursor_line: int,
    cursor_column: int,
) -> VisualModeState:
    """Return state after toggling a visual mode type."""
    if current_visual_mode and current_visual_type == requested_visual_type:
        return exit_visual_mode(current_visual_type=current_visual_type)

    return enter_visual_mode(
        visual_type=requested_visual_type,
        current_visual_mode=current_visual_mode,
        current_visual_anchor_line=current_visual_anchor_line,
        current_visual_anchor_column=current_visual_anchor_column,
        cursor_line=cursor_line,
        cursor_column=cursor_column,
    )


def visual_line_selection_role(
    *,
    line_index: int,
    visual_type: SelectionKind,
    visual_anchor_line: int | None,
) -> VisualLineSelectionRole:
    """Return the selection class role for one visual line."""
    if visual_type != "line":
        return "none"
    if line_index == visual_anchor_line:
        return "anchor"
    return "selected"


def allows_column_motion(*, visual_mode: bool, visual_type: SelectionKind) -> bool:
    """Return whether cursor column motion is allowed."""
    return not (visual_mode and visual_type == "line")


def visual_mode_sub_title(*, visual_mode: bool, visual_type: SelectionKind) -> str:
    """Return the app subtitle for the current visual mode."""
    if not visual_mode:
        return ""
    if visual_type == "line":
        return "-- VISUAL LINE --"
    return "-- VISUAL --"


def visual_mode_ui_update(
    *,
    visual_mode: bool,
    visual_type: SelectionKind,
    cursor_line: int,
) -> VisualModeUIUpdate:
    """Return UI update policy after visual mode changes."""
    return VisualModeUIUpdate(
        sub_title=visual_mode_sub_title(
            visual_mode=visual_mode,
            visual_type=visual_type,
        ),
        selection_refresh_lines=(
            _single_line_set(cursor_line) if visual_mode else _empty_line_set()
        ),
        clear_selection=not visual_mode,
        update_status_line=True,
    )


def visual_type_ui_update(
    *,
    visual_mode: bool,
    visual_type: SelectionKind,
    cursor_line: int,
) -> VisualTypeUIUpdate:
    """Return UI update policy after visual type changes."""
    return VisualTypeUIUpdate(
        sub_title=(
            visual_mode_sub_title(visual_mode=True, visual_type=visual_type)
            if visual_mode
            else None
        ),
        selection_dirty_lines=(
            _single_line_set(cursor_line) if visual_mode else _empty_line_set()
        ),
        update_status_line=True,
    )


def visual_anchor_ui_update(
    *,
    visual_mode: bool,
    cursor_line: int,
) -> VisualAnchorUIUpdate:
    """Return UI update policy after visual anchor changes."""
    return VisualAnchorUIUpdate(
        selection_dirty_lines=(
            _single_line_set(cursor_line) if visual_mode else _empty_line_set()
        )
    )


def visual_queue_update(update: VisualQueuedUpdate) -> VisualQueueUpdate:
    """Return the cursor queue policy for a visual-mode update."""
    return VisualQueueUpdate(
        selection_dirty_lines=(
            update.selection_dirty_lines if update.selection_dirty_lines else None
        ),
        update_status_line=(
            update.update_status_line
            if isinstance(update, VisualTypeUIUpdate)
            else False
        ),
    )
