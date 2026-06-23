"""Prefix Content builders for diff lines."""

from __future__ import annotations

from typing import Literal

from textual.content import Content

from rit.core.types import DiffLine

__all__ = (
    "build_preview_prefix_content",
    "build_split_prefix",
    "build_split_prefix_content",
    "build_unified_modified_prefix_content",
    "build_unified_prefix_content",
    "preview_change_marker_content",
)


def build_unified_prefix_content(
    line: DiffLine,
    *,
    show_line_numbers: bool,
    old_line_number_width: int,
    new_line_number_width: int,
) -> Content:
    """Build the prefix for a normal unified diff line."""
    prefix_parts: list[Content] = []
    if show_line_numbers:
        old_no = str(line.old_line_no) if line.old_line_no else ""
        new_no = str(line.new_line_no) if line.new_line_no else ""
        prefix_parts.append(
            Content.styled(f"{old_no:>{old_line_number_width}} ", "$text-disabled")
        )
        prefix_parts.append(
            Content.styled(f"{new_no:>{new_line_number_width}} ", "$text-disabled")
        )

    prefix = " "
    if line.is_added:
        prefix = "+"
    elif line.is_deleted:
        prefix = "-"
    prefix_parts.append(Content(prefix + " "))
    return Content("").join(prefix_parts)


def build_preview_prefix_content(
    line: DiffLine,
    *,
    show_line_numbers: bool,
    new_line_number_width: int,
) -> Content:
    """Build the prefix for full-file preview lines."""
    line_no = str(line.new_line_no) if line.new_line_no else ""
    delete_marker = (
        Content.styled("▸", "$error")
        if line.preview_deleted_before
        else Content(" ")
    )
    change_marker = preview_change_marker_content(line)
    if not show_line_numbers:
        return Content("").join([delete_marker, change_marker, Content(" ")])
    return Content("").join(
        [
            Content.styled(f"{line_no:>{new_line_number_width}} ", "$text-disabled"),
            delete_marker,
            change_marker,
            Content(" "),
        ]
    )


def preview_change_marker_content(line: DiffLine) -> Content:
    """Build the full-file preview change marker."""
    if line.preview_change == "added":
        return Content.styled("┃", "$success")
    if line.preview_change == "modified":
        return Content.styled("┃", "$warning")
    return Content(" ")


def build_split_prefix(
    line_no: int | None,
    prefix: str,
    *,
    show_line_numbers: bool,
    line_number_width: int,
) -> Content:
    """Build the prefix for one split diff side."""
    parts: list[Content] = []
    if show_line_numbers:
        line_text = str(line_no) if line_no is not None else ""
        parts.append(
            Content.styled(f"{line_text:>{line_number_width}} ", "$text-disabled")
        )
    parts.append(Content(prefix + " "))
    return Content("").join(parts)


def build_split_prefix_content(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
    show_line_numbers: bool,
    line_number_width: int,
) -> Content:
    """Build the split prefix for a diff line side."""
    if side == "old":
        prefix = "-" if line.is_deleted or line.is_modified else " "
        line_no = line.old_line_no
    else:
        prefix = "+" if line.is_added or line.is_modified else " "
        line_no = line.new_line_no

    return build_split_prefix(
        line_no,
        prefix,
        show_line_numbers=show_line_numbers,
        line_number_width=line_number_width,
    )


def build_unified_modified_prefix_content(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
    show_line_numbers: bool,
    old_line_number_width: int,
    new_line_number_width: int,
) -> Content:
    """Build the prefix for one side of a modified unified diff line."""
    prefix_parts: list[Content] = []
    if show_line_numbers:
        if side == "old":
            prefix_parts.append(
                Content.styled(
                    f"{line.old_line_no:>{old_line_number_width}} ",
                    "$text-disabled",
                )
            )
            prefix_parts.append(
                Content.styled(" " * (new_line_number_width + 1), "$text-disabled")
            )
        else:
            prefix_parts.append(
                Content.styled(" " * (old_line_number_width + 1), "$text-disabled")
            )
            prefix_parts.append(
                Content.styled(
                    f"{line.new_line_no:>{new_line_number_width}} ",
                    "$text-disabled",
                )
            )
    prefix_parts.append(Content("- " if side == "old" else "+ "))
    return Content("").join(prefix_parts)
