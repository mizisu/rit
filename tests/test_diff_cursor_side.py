from rit.core.types import DiffLine
from rit.ui.widgets.diff_cursor_side import (
    cursor_side_for_line,
    resolve_active_pane_for_line,
)


def test_resolve_active_pane_uses_only_available_side_for_single_sided_lines() -> None:
    assert (
        resolve_active_pane_for_line(
            DiffLine(old_line_no=None, new_line_no=1, is_added=True),
            "old",
        )
        == "new"
    )
    assert (
        resolve_active_pane_for_line(
            DiffLine(old_line_no=1, new_line_no=None, is_deleted=True),
            "new",
        )
        == "old"
    )


def test_resolve_active_pane_keeps_preferred_pane_for_modified_lines() -> None:
    modified = DiffLine(
        old_line_no=1,
        new_line_no=1,
        is_modified=True,
    )

    assert resolve_active_pane_for_line(modified, "old") == "old"
    assert resolve_active_pane_for_line(modified, "new") == "new"


def test_cursor_side_for_split_mode_never_returns_auto() -> None:
    context = DiffLine(old_line_no=1, new_line_no=1)

    assert cursor_side_for_line(context, split=True, cursor_pane="old") == "old"


def test_cursor_side_for_unified_mode_uses_auto_only_for_context_lines() -> None:
    context = DiffLine(old_line_no=1, new_line_no=1)
    added = DiffLine(old_line_no=None, new_line_no=2, is_added=True)
    deleted = DiffLine(old_line_no=2, new_line_no=None, is_deleted=True)
    modified = DiffLine(old_line_no=3, new_line_no=3, is_modified=True)

    assert cursor_side_for_line(context, split=False, cursor_pane="new") == "auto"
    assert cursor_side_for_line(added, split=False, cursor_pane="old") == "new"
    assert cursor_side_for_line(deleted, split=False, cursor_pane="new") == "old"
    assert cursor_side_for_line(modified, split=False, cursor_pane="old") == "old"
