from rit.core.types import DiffLine
from rit.ui.widgets.diff_prefix import (
    build_preview_prefix_content,
    build_split_prefix_content,
    build_unified_modified_prefix_content,
    build_unified_prefix_content,
)


def test_build_unified_prefix_content_formats_line_numbers_and_change_marker() -> None:
    added = DiffLine(old_line_no=None, new_line_no=12, is_added=True)
    deleted = DiffLine(old_line_no=3, new_line_no=None, is_deleted=True)

    assert (
        str(
            build_unified_prefix_content(
                added,
                show_line_numbers=True,
                old_line_number_width=2,
                new_line_number_width=3,
            )
        )
        == "    12 + "
    )
    assert (
        str(
            build_unified_prefix_content(
                deleted,
                show_line_numbers=False,
                old_line_number_width=2,
                new_line_number_width=3,
            )
        )
        == "- "
    )


def test_build_preview_prefix_content_formats_preview_markers() -> None:
    line = DiffLine(
        old_line_no=1,
        new_line_no=23,
        preview_change="modified",
        preview_deleted_before=True,
    )

    assert (
        str(
            build_preview_prefix_content(
                line,
                show_line_numbers=True,
                new_line_number_width=3,
            )
        )
        == " 23 ▸┃ "
    )
    assert (
        str(
            build_preview_prefix_content(
                line,
                show_line_numbers=False,
                new_line_number_width=3,
            )
        )
        == "▸┃ "
    )


def test_build_split_prefix_content_formats_side_specific_markers() -> None:
    modified = DiffLine(old_line_no=7, new_line_no=8, is_modified=True)
    added = DiffLine(old_line_no=None, new_line_no=9, is_added=True)

    assert (
        str(
            build_split_prefix_content(
                modified,
                side="old",
                show_line_numbers=True,
                line_number_width=2,
            )
        )
        == " 7 - "
    )
    assert (
        str(
            build_split_prefix_content(
                added,
                side="old",
                show_line_numbers=False,
                line_number_width=2,
            )
        )
        == "  "
    )


def test_build_unified_modified_prefix_content_keeps_opposite_column_blank() -> None:
    modified = DiffLine(old_line_no=7, new_line_no=80, is_modified=True)

    assert (
        str(
            build_unified_modified_prefix_content(
                modified,
                side="old",
                show_line_numbers=True,
                old_line_number_width=2,
                new_line_number_width=3,
            )
        )
        == " 7     - "
    )
    assert (
        str(
            build_unified_modified_prefix_content(
                modified,
                side="new",
                show_line_numbers=True,
                old_line_number_width=2,
                new_line_number_width=3,
            )
        )
        == "    80 + "
    )
