import rit.ui.widgets.diff_layout as diff_layout_module
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile
from rit.ui.widgets.diff_layout import (
    code_widths_for_layout,
    file_header_width_for_layout,
    line_number_columns_for_change_counts,
    line_number_width_for_layout,
    preview_prefix_width_for_layout,
    should_force_unified_for_file,
    should_force_unified_for_hunk,
    split_placeholder_width_for_layout,
    split_prefix_width_for_layout,
    unified_prefix_width_for_layout,
)


def test_should_force_unified_for_full_preview_and_added_file() -> None:
    assert (
        should_force_unified_for_file(
            showing_full_file=True,
            file=None,
            diff=None,
        )
        is True
    )
    assert (
        should_force_unified_for_file(
            showing_full_file=False,
            file=PRFile(filename="new.py", status="added", additions=2),
            diff=None,
        )
        is True
    )


def test_should_force_unified_for_new_and_deleted_diff_flags() -> None:
    assert (
        should_force_unified_for_file(
            showing_full_file=False,
            file=None,
            diff=FileDiff(filename="new.py", is_new=True),
        )
        is True
    )
    assert (
        should_force_unified_for_file(
            showing_full_file=False,
            file=None,
            diff=FileDiff(filename="deleted.py", is_deleted=True),
        )
        is True
    )


def test_should_not_force_unified_for_modified_files() -> None:
    assert (
        should_force_unified_for_file(
            showing_full_file=False,
            file=PRFile(filename="changed.py", additions=1, deletions=1),
            diff=FileDiff(filename="changed.py"),
        )
        is False
    )
    assert (
        should_force_unified_for_file(
            showing_full_file=False,
            file=PRFile(filename="changed.py", additions=1, deletions=0),
            diff=FileDiff(filename="changed.py"),
        )
        is False
    )
    assert (
        should_force_unified_for_file(
            showing_full_file=False,
            file=PRFile(filename="changed.py", additions=1, deletions=1),
            diff=FileDiff(filename="changed.py"),
        )
        is False
    )


def test_should_force_unified_only_for_added_or_removed_hunks() -> None:
    assert (
        should_force_unified_for_hunk(
            DiffHunk(
                old_start=0,
                old_count=0,
                new_start=1,
                new_count=1,
                file_status="added",
            )
        )
        is True
    )
    assert (
        should_force_unified_for_hunk(
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=0,
                new_count=0,
                file_status="removed",
            )
        )
        is True
    )
    assert (
        should_force_unified_for_hunk(
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                file_additions=0,
                file_deletions=1,
            )
        )
        is False
    )
    assert (
        should_force_unified_for_hunk(
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                file_additions=1,
                file_deletions=1,
            )
        )
        is False
    )


def test_code_widths_for_layout_use_display_cell_widths() -> None:
    widths = code_widths_for_layout(
        [
            DiffLine(old_line_no=1, new_line_no=1, old_content="old", new_content="n"),
            DiffLine(
                old_line_no=2,
                new_line_no=2,
                old_content="界界",
                new_content="newer",
            ),
        ]
    )

    assert widths == (5, 4, 5)


def test_code_widths_for_layout_keep_empty_columns_addressable() -> None:
    assert code_widths_for_layout([]) == (1, 1, 1)
    assert code_widths_for_layout([DiffLine(old_line_no=None, new_line_no=None)]) == (
        1,
        1,
        1,
    )


def test_split_prefix_width_for_layout_respects_line_number_visibility() -> None:
    assert (
        split_prefix_width_for_layout(
            show_line_numbers=False,
            line_number_width=3,
        )
        == 3
    )
    assert (
        split_prefix_width_for_layout(
            show_line_numbers=True,
            line_number_width=3,
        )
        == 7
    )


def test_unified_and_preview_prefix_widths_for_layout() -> None:
    assert (
        unified_prefix_width_for_layout(
            show_line_numbers=False,
            old_line_number_width=4,
            new_line_number_width=5,
        )
        == 3
    )
    assert (
        unified_prefix_width_for_layout(
            show_line_numbers=True,
            old_line_number_width=4,
            new_line_number_width=5,
        )
        == 14
    )
    assert (
        unified_prefix_width_for_layout(
            show_line_numbers=True,
            old_line_number_width=4,
            new_line_number_width=5,
            line_number_columns="old",
        )
        == 8
    )
    assert (
        unified_prefix_width_for_layout(
            show_line_numbers=True,
            old_line_number_width=4,
            new_line_number_width=5,
            line_number_columns="new",
        )
        == 9
    )
    assert (
        preview_prefix_width_for_layout(
            show_line_numbers=False,
            new_line_number_width=5,
        )
        == 4
    )
    assert (
        preview_prefix_width_for_layout(
            show_line_numbers=True,
            new_line_number_width=5,
        )
        == 10
    )


def test_line_number_width_for_layout_reserves_four_digits() -> None:
    assert line_number_width_for_layout(show_line_numbers=False, numbers=[100]) == 0
    assert line_number_width_for_layout(show_line_numbers=True, numbers=[]) == 4
    assert line_number_width_for_layout(show_line_numbers=True, numbers=[1, 9]) == 4
    assert line_number_width_for_layout(show_line_numbers=True, numbers=[1, 120]) == 4
    assert line_number_width_for_layout(show_line_numbers=True, numbers=[1, 12000]) == 5


def test_line_number_columns_for_change_counts_hides_unused_side() -> None:
    assert line_number_columns_for_change_counts(3, 0) == "new"
    assert line_number_columns_for_change_counts(0, 3) == "old"
    assert line_number_columns_for_change_counts(3, 2) == "both"
    assert line_number_columns_for_change_counts(0, 0) == "both"


def test_line_number_width_for_layout_single_number_skips_max(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        diff_layout_module,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single line number width should not call max")
        ),
        raising=False,
    )

    assert line_number_width_for_layout(show_line_numbers=True, numbers=[120]) == 4


def test_split_placeholder_width_for_layout_uses_visible_pane_budget() -> None:
    assert (
        split_placeholder_width_for_layout(
            side_code_width=0,
            viewport_width=0,
        )
        == 1
    )
    assert (
        split_placeholder_width_for_layout(
            side_code_width=20,
            viewport_width=10,
        )
        == 20
    )
    assert (
        split_placeholder_width_for_layout(
            side_code_width=4,
            viewport_width=30,
        )
        == 15
    )


def test_file_header_width_for_layout_prefers_visible_viewport() -> None:
    assert (
        file_header_width_for_layout(
            fallback_width=20,
            viewport_width=80,
            split=False,
            unified_content_width=40,
            old_split_prefix_width=4,
            old_split_code_width=10,
            new_split_prefix_width=5,
            new_split_code_width=11,
        )
        == 80
    )
    assert (
        file_header_width_for_layout(
            fallback_width=90,
            viewport_width=80,
            split=True,
            unified_content_width=40,
            old_split_prefix_width=4,
            old_split_code_width=10,
            new_split_prefix_width=5,
            new_split_code_width=11,
        )
        == 90
    )


def test_file_header_width_for_layout_uses_render_mode_when_viewport_is_unknown() -> (
    None
):
    assert (
        file_header_width_for_layout(
            fallback_width=20,
            viewport_width=0,
            split=False,
            unified_content_width=40,
            old_split_prefix_width=4,
            old_split_code_width=10,
            new_split_prefix_width=5,
            new_split_code_width=11,
        )
        == 40
    )
    assert (
        file_header_width_for_layout(
            fallback_width=20,
            viewport_width=0,
            split=True,
            unified_content_width=40,
            old_split_prefix_width=4,
            old_split_code_width=10,
            new_split_prefix_width=5,
            new_split_code_width=11,
        )
        == 34
    )
