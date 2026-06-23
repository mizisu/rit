"""Line style policy for diff rendering."""

from __future__ import annotations

from typing import Literal

from rit.core.types import DiffLine
from rit.ui.widgets.diff_visual import MISSING_SIDE_BACKGROUND_STYLE

__all__ = (
    "split_annotation_style",
    "split_code_classes",
    "split_line_style",
    "split_prefix_classes",
    "split_side_missing",
    "unified_code_classes",
    "unified_line_style",
)


def unified_line_style(
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
    showing_full_file: bool,
) -> str:
    """Return the background style for a unified diff line."""
    if showing_full_file:
        return ""
    if side == "old" and line.is_modified:
        return "on $error 6%"
    if side == "new" and line.is_modified:
        return "on $success 6%"
    if line.is_added:
        return "on $success 6%"
    if line.is_deleted:
        return "on $error 6%"
    return ""


def split_line_style(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
    word_diff_enabled: bool,
) -> str:
    """Return the background style for one split diff side."""
    if line.is_modified and line.has_word_diff and word_diff_enabled:
        return ""
    if side == "old" and (line.is_deleted or line.is_modified):
        return "on $error 6%"
    if side == "new" and (line.is_added or line.is_modified):
        return "on $success 6%"
    return ""


def split_side_missing(line: DiffLine, *, side: Literal["old", "new"]) -> bool:
    """Return whether a split side has no matching source line."""
    return not (line.has_old_side if side == "old" else line.has_new_side)


def split_annotation_style(line: DiffLine, *, side: Literal["old", "new"]) -> str:
    """Return the annotation gutter style for one split side."""
    if split_side_missing(line, side=side):
        return MISSING_SIDE_BACKGROUND_STYLE
    return ""


def split_prefix_classes(line: DiffLine, *, side: Literal["old", "new"]) -> str:
    """Return CSS classes for one split prefix side."""
    classes = "line-prefix"
    if split_side_missing(line, side=side):
        classes += " -placeholder"
    return classes


def unified_code_classes(
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> str:
    """Return CSS classes for unified code content."""
    classes = "code-content"
    if side == "old" or line.is_deleted:
        if side == "old" or line.is_modified or line.is_deleted:
            classes += " -removed"
    elif side == "new" or line.is_added:
        if side == "new" or line.is_modified or line.is_added:
            classes += " -added"
    return classes


def split_code_classes(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
    word_diff_enabled: bool = True,
) -> str:
    """Return CSS classes for one split code side."""
    classes = f"code-content -{side}-side"
    inline_word_diff = line.is_modified and word_diff_enabled and line.has_word_diff

    if side == "old":
        if line.is_deleted or (line.is_modified and not inline_word_diff):
            classes += " -removed"
        if line.is_added:
            classes += " -placeholder"
    else:
        if line.is_added or (line.is_modified and not inline_word_diff):
            classes += " -added"
        if line.is_deleted:
            classes += " -placeholder"

    return classes
