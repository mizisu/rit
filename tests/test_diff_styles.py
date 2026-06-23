from rit.core.types import DiffLine, InlineSegment, SegmentType
from rit.ui.widgets.diff_styles import (
    split_annotation_style,
    split_code_classes,
    split_line_style,
    split_prefix_classes,
    split_side_missing,
    unified_code_classes,
    unified_line_style,
)
from rit.ui.widgets.diff_visual import MISSING_SIDE_BACKGROUND_STYLE


def test_unified_line_style_uses_change_backgrounds_outside_full_preview() -> None:
    added = DiffLine(old_line_no=None, new_line_no=1, is_added=True)
    deleted = DiffLine(old_line_no=1, new_line_no=None, is_deleted=True)
    modified = DiffLine(old_line_no=1, new_line_no=1, is_modified=True)

    assert unified_line_style(added, showing_full_file=False) == "on $success 6%"
    assert unified_line_style(deleted, showing_full_file=False) == "on $error 6%"
    assert (
        unified_line_style(modified, side="old", showing_full_file=False)
        == "on $error 6%"
    )
    assert (
        unified_line_style(modified, side="new", showing_full_file=False)
        == "on $success 6%"
    )
    assert unified_line_style(added, showing_full_file=True) == ""


def test_split_line_style_lets_inline_word_diff_carry_modified_background() -> None:
    modified = DiffLine(
        old_line_no=1,
        new_line_no=1,
        is_modified=True,
        old_segments=[InlineSegment("old", SegmentType.DELETED)],
        new_segments=[InlineSegment("new", SegmentType.ADDED)],
    )

    assert split_line_style(modified, side="old", word_diff_enabled=True) == ""
    assert split_line_style(modified, side="new", word_diff_enabled=True) == ""
    assert (
        split_line_style(modified, side="old", word_diff_enabled=False)
        == "on $error 6%"
    )
    assert (
        split_line_style(modified, side="new", word_diff_enabled=False)
        == "on $success 6%"
    )


def test_split_missing_side_policy_uses_quiet_annotation_background() -> None:
    added = DiffLine(old_line_no=None, new_line_no=1, is_added=True)

    assert split_side_missing(added, side="old") is True
    assert split_side_missing(added, side="new") is False
    assert split_annotation_style(added, side="old") == MISSING_SIDE_BACKGROUND_STYLE
    assert split_annotation_style(added, side="new") == ""
    assert split_prefix_classes(added, side="old") == "line-prefix -placeholder"
    assert split_prefix_classes(added, side="new") == "line-prefix"


def test_code_classes_mark_change_sides_and_placeholders() -> None:
    modified = DiffLine(old_line_no=1, new_line_no=1, is_modified=True)
    added = DiffLine(old_line_no=None, new_line_no=1, is_added=True)

    assert unified_code_classes(modified, side="old") == "code-content -removed"
    assert unified_code_classes(modified, side="new") == "code-content -added"
    assert split_code_classes(added, side="old") == "code-content -old-side -placeholder"
    assert split_code_classes(added, side="new") == "code-content -new-side -added"
