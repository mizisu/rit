from __future__ import annotations

from typing import Literal

from rit.core.types import DiffLine

__all__ = (
    "cursor_side_for_line",
    "resolve_active_pane_for_line",
)


def resolve_active_pane_for_line(
    line: DiffLine,
    pane: Literal["old", "new"],
) -> Literal["old", "new"]:
    """Return the pane that can host the cursor for a diff line."""
    if line.is_added and not line.is_modified:
        return "new"
    if line.is_deleted and not line.is_modified:
        return "old"
    return pane


def cursor_side_for_line(
    line: DiffLine,
    *,
    split: bool,
    cursor_pane: Literal["old", "new"],
) -> Literal["old", "new", "auto"]:
    """Return the line side used for cursor/text operations."""
    if split:
        return resolve_active_pane_for_line(line, cursor_pane)
    if line.is_modified:
        return resolve_active_pane_for_line(line, cursor_pane)
    if line.is_deleted:
        return "old"
    if line.is_added:
        return "new"
    return "auto"
