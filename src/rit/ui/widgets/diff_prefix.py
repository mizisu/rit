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
    prefix = _unified_change_prefix(line)
    if not show_line_numbers:
        return Content(prefix + " ")

    old_no = str(line.old_line_no) if line.old_line_no else ""
    new_no = str(line.new_line_no) if line.new_line_no else ""
    return Content.assemble(
        (f"{old_no:>{old_line_number_width}} ", "$text-disabled"),
        (f"{new_no:>{new_line_number_width}} ", "$text-disabled"),
        prefix + " ",
    )


def build_preview_prefix_content(
    line: DiffLine,
    *,
    show_line_numbers: bool,
    new_line_number_width: int,
) -> Content:
    """Build the prefix for full-file preview lines."""
    if not show_line_numbers:
        return Content.assemble(
            _preview_deleted_marker_part(line),
            _preview_change_marker_part(line),
            " ",
        )

    line_no = str(line.new_line_no) if line.new_line_no else ""
    return Content.assemble(
        (f"{line_no:>{new_line_number_width}} ", "$text-disabled"),
        _preview_deleted_marker_part(line),
        _preview_change_marker_part(line),
        " ",
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
    if not show_line_numbers:
        return Content(prefix + " ")

    line_text = str(line_no) if line_no is not None else ""
    return Content.assemble(
        (f"{line_text:>{line_number_width}} ", "$text-disabled"),
        prefix + " ",
    )


def build_split_prefix_content(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
    show_line_numbers: bool,
    line_number_width: int,
) -> Content:
    """Build the split prefix for a diff line side."""
    return build_split_prefix(
        _split_line_number(line, side=side),
        _split_prefix_marker(line, side=side),
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
    prefix = "- " if side == "old" else "+ "
    if not show_line_numbers:
        return Content(prefix)

    if side == "old":
        return Content.assemble(
            (f"{line.old_line_no:>{old_line_number_width}} ", "$text-disabled"),
            (" " * (new_line_number_width + 1), "$text-disabled"),
            prefix,
        )

    return Content.assemble(
        (" " * (old_line_number_width + 1), "$text-disabled"),
        (f"{line.new_line_no:>{new_line_number_width}} ", "$text-disabled"),
        prefix,
    )


def _unified_change_prefix(line: DiffLine) -> str:
    if line.is_added:
        return "+"
    if line.is_deleted:
        return "-"
    return " "


def _preview_deleted_marker_part(line: DiffLine) -> tuple[str, str] | str:
    return ("▸", "$error") if line.preview_deleted_before else " "


def _preview_change_marker_part(line: DiffLine) -> tuple[str, str] | str:
    if line.preview_change == "added":
        return ("┃", "$success")
    if line.preview_change == "modified":
        return ("┃", "$warning")
    return " "


def _split_prefix_marker(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> str:
    if side == "old":
        return "-" if line.is_deleted or line.is_modified else " "
    return "+" if line.is_added or line.is_modified else " "


def _split_line_number(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> int | None:
    return line.old_line_no if side == "old" else line.new_line_no
