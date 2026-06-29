"""Split/unified layout policy for diff rendering."""

from __future__ import annotations

from collections.abc import Collection, Sequence

from rich.cells import cell_len

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile

__all__ = (
    "can_fit_auto_split_content",
    "code_widths_for_layout",
    "file_header_width_for_layout",
    "line_number_width_for_layout",
    "preview_prefix_width_for_layout",
    "should_force_unified_for_file",
    "should_force_unified_for_hunk",
    "split_placeholder_width_for_layout",
    "split_prefix_width_for_layout",
    "unified_prefix_width_for_layout",
)


def should_force_unified_for_file(
    *,
    showing_full_file: bool,
    file: PRFile | None,
    diff: FileDiff | None,
    lines: Sequence[DiffLine],
) -> bool:
    """Return whether the current file should render as unified."""
    if showing_full_file:
        return True
    if file is not None:
        if file.status in {"added", "removed"}:
            return True
        if _change_stats_are_single_sided(file.additions, file.deletions):
            return True
    if diff is None:
        return False
    if diff.is_new or diff.is_deleted:
        return True
    return diff.is_fully_refined and _has_only_added_deleted_changes(lines)


def should_force_unified_for_hunk(hunk: DiffHunk) -> bool:
    """Return whether a combined-file hunk should render as unified."""
    if hunk.file_status in {"added", "removed"}:
        return True
    return _change_stats_are_single_sided(hunk.file_additions, hunk.file_deletions)


def can_fit_auto_split_content(
    lines: Sequence[DiffLine],
    *,
    old_prefix_width: int,
    new_prefix_width: int,
    available_width: int,
) -> bool:
    """Return whether split panes can fit the current diff content."""
    if not lines:
        return True

    split_gap = 2
    fixed_width = old_prefix_width + new_prefix_width + split_gap + split_gap
    if available_width < fixed_width:
        return False

    max_old_width = 0
    max_new_width = 0
    for line in lines:
        if line.old_content:
            max_old_width = max(max_old_width, cell_len(line.old_content))
        if line.new_content:
            max_new_width = max(max_new_width, cell_len(line.new_content))
        if available_width < fixed_width + max_old_width + max_new_width:
            return False

    return True


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
        return 2
    return line_number_width + 2


def unified_prefix_width_for_layout(
    *,
    show_line_numbers: bool,
    old_line_number_width: int,
    new_line_number_width: int,
) -> int:
    """Return prefix cell width for unified diff lines."""
    if not show_line_numbers:
        return 2
    return old_line_number_width + new_line_number_width + 4


def preview_prefix_width_for_layout(
    *,
    show_line_numbers: bool,
    new_line_number_width: int,
) -> int:
    """Return prefix cell width for full-file preview lines."""
    if not show_line_numbers:
        return 3
    return new_line_number_width + 4


def line_number_width_for_layout(
    *,
    show_line_numbers: bool,
    numbers: Collection[int],
) -> int:
    """Return the cell width needed for a line-number column."""
    if not show_line_numbers:
        return 0
    if not numbers:
        return 1
    if len(numbers) == 1:
        width = len(str(next(iter(numbers))))
        return width if width > 1 else 1
    return max(1, len(str(max(numbers))))


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


def _has_only_added_deleted_changes(lines: Sequence[DiffLine]) -> bool:
    has_change = False

    for line in lines:
        if line.is_modified:
            return False
        if line.is_added or line.is_deleted:
            has_change = True

    return has_change


def _change_stats_are_single_sided(additions: int, deletions: int) -> bool:
    return (additions > 0 and deletions == 0) or (deletions > 0 and additions == 0)


def _line_code_width(text: str) -> int:
    return max(1, cell_len(text)) if text else 1
