from rit.core.types import DiffLine
from rit.ui.widgets.diff_prefix import (
    build_preview_prefix_content,
    build_split_prefix_content,
    build_unified_modified_prefix_content,
    build_unified_prefix_content,
)
from textual.content import Content


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


def test_prefix_builders_without_line_numbers_avoid_join(
    monkeypatch,
) -> None:
    def fail_join(self: Content, lines) -> Content:
        raise AssertionError("line-number-free prefixes should not join parts")

    monkeypatch.setattr(Content, "join", fail_join)

    added = DiffLine(old_line_no=None, new_line_no=12, is_added=True)
    preview = DiffLine(
        old_line_no=11,
        new_line_no=12,
        preview_change="modified",
        preview_deleted_before=True,
    )
    modified = DiffLine(old_line_no=3, new_line_no=4, is_modified=True)

    assert (
        str(
            build_unified_prefix_content(
                added,
                show_line_numbers=False,
                old_line_number_width=2,
                new_line_number_width=2,
            )
        )
        == "+ "
    )
    assert (
        str(
            build_preview_prefix_content(
                preview,
                show_line_numbers=False,
                new_line_number_width=2,
            )
        )
        == "▸┃ "
    )
    assert (
        str(
            build_split_prefix_content(
                modified,
                side="old",
                show_line_numbers=False,
                line_number_width=2,
            )
        )
        == "- "
    )
    assert (
        str(
            build_unified_modified_prefix_content(
                modified,
                side="new",
                show_line_numbers=False,
                old_line_number_width=2,
                new_line_number_width=2,
            )
        )
        == "+ "
    )
