"""Split/unified layout policy for diff rendering."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Literal

from rich.cells import cell_len

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile

__all__ = (
    "MIN_LINE_NUMBER_WIDTH",
    "LineNumberColumns",
    "code_widths_for_layout",
    "file_header_width_for_layout",
    "line_number_columns_for_change_counts",
    "line_number_width_for_layout",
    "preview_prefix_width_for_layout",
    "should_force_unified_for_file",
    "should_force_unified_for_hunk",
    "split_placeholder_width_for_layout",
    "split_prefix_width_for_layout",
    "unified_prefix_width_for_layout",
)


type LineNumberColumns = Literal["old", "new", "both"]


MIN_LINE_NUMBER_WIDTH = 4
_CODE_GAP_WIDTH = 2


def should_force_unified_for_file(
    *,
    showing_full_file: bool,
    file: PRFile | None,
    diff: FileDiff | None,
) -> bool:
    """Return whether the current file cannot use a two-sided layout."""
    if showing_full_file:
        return True
    if file is not None and file.status in {"added", "removed"}:
        return True
    if diff is None:
        return False
    if diff.is_new or diff.is_deleted:
        return True
    return bool(diff.hunks) and (
        all(hunk.old_count == 0 for hunk in diff.hunks)
        or all(hunk.new_count == 0 for hunk in diff.hunks)
    )


def should_force_unified_for_hunk(hunk: DiffHunk) -> bool:
    """Return whether a combined-file hunk cannot use a two-sided layout."""
    return hunk.file_status in {"added", "removed"}


def code_widths_for_layout(lines: Sequence[DiffLine]) -> tuple[int, int, int]:
    """Return unified, old, and new code cell widths for diff layout."""
    old_width = 1
    new_width = 1
    for line in lines:
        old_width = max(old_width, _line_code_width(line.old_content))
        new_width = max(new_width, _line_code_width(line.new_content))
    return max(old_width, new_width), old_width, new_width


def split_prefix_width_for_layout(
    *,
    show_line_numbers: bool,
    line_number_width: int,
) -> int:
    """Return prefix cell width for one split diff side."""
    if not show_line_numbers:
        return 1 + _CODE_GAP_WIDTH
    return line_number_width + 2 + _CODE_GAP_WIDTH


def unified_prefix_width_for_layout(
    *,
    show_line_numbers: bool,
    old_line_number_width: int,
    new_line_number_width: int,
    line_number_columns: LineNumberColumns = "both",
) -> int:
    """Return prefix cell width for unified diff lines."""
    if not show_line_numbers:
        return 1 + _CODE_GAP_WIDTH
    width = 1 + _CODE_GAP_WIDTH
    if line_number_columns in {"old", "both"}:
        width += old_line_number_width + 1
    if line_number_columns in {"new", "both"}:
        width += new_line_number_width + 1
    return width


def line_number_columns_for_change_counts(
    additions: int,
    deletions: int,
) -> LineNumberColumns:
    """Return the useful unified line-number columns for file change counts."""
    if additions > 0 and deletions == 0:
        return "new"
    if deletions > 0 and additions == 0:
        return "old"
    return "both"


def preview_prefix_width_for_layout(
    *,
    show_line_numbers: bool,
    new_line_number_width: int,
) -> int:
    """Return prefix cell width for full-file preview lines."""
    if not show_line_numbers:
        return 2 + _CODE_GAP_WIDTH
    return new_line_number_width + 3 + _CODE_GAP_WIDTH


def line_number_width_for_layout(
    *,
    show_line_numbers: bool,
    numbers: Collection[int],
) -> int:
    """Return the cell width needed for a line-number column."""
    if not show_line_numbers:
        return 0
    if not numbers:
        return MIN_LINE_NUMBER_WIDTH
    if len(numbers) == 1:
        width = len(str(next(iter(numbers))))
        if width < MIN_LINE_NUMBER_WIDTH:
            return MIN_LINE_NUMBER_WIDTH
        return width
    return max(MIN_LINE_NUMBER_WIDTH, len(str(max(numbers))))


def split_placeholder_width_for_layout(
    *,
    side_code_width: int,
    viewport_width: int,
) -> int:
    """Return the hatch width for a missing split side."""
    return max(1, side_code_width, viewport_width // 2)


def file_header_width_for_layout(
    *,
    fallback_width: int,
    viewport_width: int,
    split: bool,
    unified_content_width: int,
    old_split_prefix_width: int,
    old_split_code_width: int,
    new_split_prefix_width: int,
    new_split_code_width: int,
) -> int:
    """Return the rendered file header width for the active diff layout."""
    if viewport_width > 0:
        return max(fallback_width, viewport_width)
    if not split:
        return max(fallback_width, unified_content_width)
    split_content_width = (
        old_split_prefix_width
        + old_split_code_width
        + new_split_prefix_width
        + new_split_code_width
        + 4
    )
    return max(fallback_width, split_content_width)


def _line_code_width(text: str) -> int:
    return max(1, cell_len(text)) if text else 1
