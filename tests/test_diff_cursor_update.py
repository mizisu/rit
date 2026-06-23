from rit.ui.widgets import diff_cursor_update as cursor_update
from rit.ui.widgets.diff_cursor_update import (
    ActivePaneUpdate,
    CursorColumnUpdate,
    CursorFlushRequest,
    CursorLineUpdate,
    active_pane_update,
    cursor_column_update,
    cursor_line_update,
    cursor_flush_request,
    cursor_lines_for_flush,
    cursor_move_update,
)


def test_cursor_lines_for_flush_keeps_lines_in_normal_mode() -> None:
    assert cursor_lines_for_flush(
        cursor_lines={1, 2},
        selection_dirty_lines={2},
        selection_full_refresh=True,
        visual_mode=False,
    ) == {1, 2}


def test_cursor_lines_for_flush_clears_lines_for_visual_selection_full_refresh() -> None:
    assert (
        cursor_lines_for_flush(
            cursor_lines={1, 2},
            selection_dirty_lines={2},
            selection_full_refresh=True,
            visual_mode=True,
        )
        == set()
    )


def test_cursor_lines_for_flush_removes_visual_selection_dirty_lines() -> None:
    assert cursor_lines_for_flush(
        cursor_lines={1, 2, 3},
        selection_dirty_lines={2, 3},
        selection_full_refresh=False,
        visual_mode=True,
    ) == {1}


def test_cursor_lines_for_flush_keeps_visual_lines_without_selection_dirty() -> None:
    assert cursor_lines_for_flush(
        cursor_lines={1, 2},
        selection_dirty_lines=set(),
        selection_full_refresh=False,
        visual_mode=True,
    ) == {1, 2}


def test_cursor_flush_request_filters_lines_to_diff_bounds() -> None:
    request = cursor_flush_request(
        line_count=3,
        cursor_lines={-1, 0, 2, 3},
        selection_dirty_lines={-5, 1, 7},
        selection_full_refresh=False,
        sync_search_match=False,
        update_status_line=False,
    )

    assert request == CursorFlushRequest(
        cursor_lines=frozenset({0, 2}),
        selection_dirty_lines=frozenset({1}),
        selection_full_refresh=False,
        sync_search_match=False,
        update_status_line=False,
    )


def test_cursor_flush_request_preserves_flags_without_lines() -> None:
    request = cursor_flush_request(
        line_count=0,
        cursor_lines=None,
        selection_dirty_lines=None,
        selection_full_refresh=True,
        sync_search_match=True,
        update_status_line=True,
    )

    assert request == CursorFlushRequest(
        cursor_lines=frozenset(),
        selection_dirty_lines=frozenset(),
        selection_full_refresh=True,
        sync_search_match=True,
        update_status_line=True,
    )


def test_cursor_move_update_marks_new_line_only_for_column_move() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=2,
        old_column=4,
        new_column=8,
        old_pane="new",
        new_pane="new",
        visual_mode=False,
        search_query="",
    )

    assert update.cursor_lines == frozenset({2})
    assert update.selection_dirty_lines is None
    assert update.update_status_line is False
    assert update.changed is True
    assert update.line_changed is False
    assert update.column_changed is True
    assert update.pane_changed is False


def test_cursor_move_update_marks_old_and_new_lines_for_line_move() -> None:
    update = cursor_move_update(
        old_line=1,
        new_line=3,
        old_column=0,
        new_column=0,
        old_pane="new",
        new_pane="new",
        visual_mode=False,
        search_query="",
    )

    assert update.cursor_lines == frozenset({1, 3})
    assert update.selection_dirty_lines is None
    assert update.update_status_line is True
    assert update.changed is True
    assert update.line_changed is True
    assert update.column_changed is False
    assert update.pane_changed is False


def test_cursor_move_update_tracks_visual_selection_dirty_lines() -> None:
    update = cursor_move_update(
        old_line=1,
        new_line=3,
        old_column=0,
        new_column=0,
        old_pane="new",
        new_pane="new",
        visual_mode=True,
        search_query="",
    )

    assert update.cursor_lines == frozenset({1, 3})
    assert update.selection_dirty_lines == frozenset({1, 3})
    assert update.update_status_line is True


def test_cursor_move_update_updates_status_for_search_column_move() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=2,
        old_column=4,
        new_column=8,
        old_pane="new",
        new_pane="new",
        visual_mode=False,
        search_query="needle",
    )

    assert update.update_status_line is True
    assert update.changed is True


def test_cursor_move_update_updates_status_for_pane_change() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=2,
        old_column=4,
        new_column=4,
        old_pane="old",
        new_pane="new",
        visual_mode=False,
        search_query="",
    )

    assert update.cursor_lines == frozenset({2})
    assert update.update_status_line is True
    assert update.changed is True
    assert update.line_changed is False
    assert update.column_changed is False
    assert update.pane_changed is True


def test_active_pane_update_marks_cursor_line_in_normal_mode() -> None:
    assert active_pane_update(
        cursor_line=4,
        visual_mode=False,
    ) == ActivePaneUpdate(
        cursor_lines=frozenset({4}),
        selection_dirty_lines=None,
        sync_search_match=True,
        update_status_line=True,
    )


def test_active_pane_update_marks_selection_line_in_visual_mode() -> None:
    assert active_pane_update(
        cursor_line=4,
        visual_mode=True,
    ) == ActivePaneUpdate(
        cursor_lines=frozenset({4}),
        selection_dirty_lines=frozenset({4}),
        sync_search_match=True,
        update_status_line=True,
    )


def test_cursor_queue_update_preserves_active_pane_flush_policy() -> None:
    update = active_pane_update(
        cursor_line=4,
        visual_mode=True,
    )

    assert cursor_update.cursor_queue_update(update) == cursor_update.CursorQueueUpdate(
        cursor_lines=frozenset({4}),
        selection_dirty_lines=frozenset({4}),
        sync_search_match=True,
        update_status_line=True,
    )


def test_cursor_line_update_marks_old_and_new_lines_in_normal_mode() -> None:
    assert cursor_line_update(
        old_line=2,
        new_line=5,
        visual_mode=False,
    ) == CursorLineUpdate(
        cursor_lines=frozenset({2, 5}),
        selection_dirty_lines=None,
        sync_search_match=True,
        update_status_line=True,
    )


def test_cursor_line_update_marks_selection_lines_in_visual_mode() -> None:
    assert cursor_line_update(
        old_line=2,
        new_line=5,
        visual_mode=True,
    ) == CursorLineUpdate(
        cursor_lines=frozenset({2, 5}),
        selection_dirty_lines=frozenset({2, 5}),
        sync_search_match=True,
        update_status_line=True,
    )


def test_cursor_column_update_clamps_empty_text_to_zero() -> None:
    assert cursor_column_update(
        cursor_line=4,
        new_column=8,
        text_length=0,
        visual_mode=False,
    ) == CursorColumnUpdate(
        corrected_column=0,
        cursor_lines=frozenset(),
        selection_dirty_lines=None,
        sync_search_match=False,
        scroll_horizontal=False,
    )


def test_clamp_cursor_column_keeps_columns_within_text_bounds() -> None:
    assert cursor_update.clamp_cursor_column(
        requested_column=-3,
        text_length=5,
    ) == 0
    assert cursor_update.clamp_cursor_column(
        requested_column=2,
        text_length=5,
    ) == 2
    assert cursor_update.clamp_cursor_column(
        requested_column=9,
        text_length=5,
    ) == 4


def test_clamp_cursor_column_returns_zero_for_empty_text() -> None:
    assert cursor_update.clamp_cursor_column(
        requested_column=3,
        text_length=0,
    ) == 0


def test_cursor_column_update_clamps_negative_column_to_start() -> None:
    assert cursor_column_update(
        cursor_line=4,
        new_column=-1,
        text_length=5,
        visual_mode=False,
    ) == CursorColumnUpdate(
        corrected_column=0,
        cursor_lines=frozenset(),
        selection_dirty_lines=None,
        sync_search_match=False,
        scroll_horizontal=False,
    )


def test_cursor_column_update_clamps_past_end_to_last_column() -> None:
    assert cursor_column_update(
        cursor_line=4,
        new_column=8,
        text_length=5,
        visual_mode=False,
    ) == CursorColumnUpdate(
        corrected_column=4,
        cursor_lines=frozenset(),
        selection_dirty_lines=None,
        sync_search_match=False,
        scroll_horizontal=False,
    )


def test_cursor_column_update_repaints_current_line_in_normal_mode() -> None:
    assert cursor_column_update(
        cursor_line=4,
        new_column=3,
        text_length=5,
        visual_mode=False,
    ) == CursorColumnUpdate(
        corrected_column=None,
        cursor_lines=frozenset({4}),
        selection_dirty_lines=None,
        sync_search_match=True,
        scroll_horizontal=True,
    )


def test_cursor_column_update_marks_selection_line_in_visual_mode() -> None:
    assert cursor_column_update(
        cursor_line=4,
        new_column=3,
        text_length=5,
        visual_mode=True,
    ) == CursorColumnUpdate(
        corrected_column=None,
        cursor_lines=frozenset({4}),
        selection_dirty_lines=frozenset({4}),
        sync_search_match=True,
        scroll_horizontal=True,
    )


def test_cursor_queue_update_maps_column_flush_policy_without_status_update() -> None:
    update = cursor_column_update(
        cursor_line=4,
        new_column=3,
        text_length=5,
        visual_mode=True,
    )

    assert cursor_update.cursor_queue_update(update) == cursor_update.CursorQueueUpdate(
        cursor_lines=frozenset({4}),
        selection_dirty_lines=frozenset({4}),
        sync_search_match=True,
        update_status_line=False,
    )


def test_cursor_queue_update_maps_move_flush_policy() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=5,
        old_column=1,
        new_column=1,
        old_pane="old",
        new_pane="new",
        visual_mode=True,
        search_query="",
    )

    assert cursor_update.cursor_queue_update(update) == cursor_update.CursorQueueUpdate(
        cursor_lines=frozenset({2, 5}),
        selection_dirty_lines=frozenset({2, 5}),
        sync_search_match=True,
        update_status_line=True,
    )


def test_cursor_move_scroll_update_scrolls_line_moves_in_normal_mode() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=5,
        old_column=1,
        new_column=1,
        old_pane="new",
        new_pane="new",
        visual_mode=False,
        search_query="",
    )

    assert cursor_update.cursor_move_scroll_update(
        update,
        visual_mode=False,
        scroll_in_visual=False,
        suppress_scroll=False,
    ) == cursor_update.CursorMoveScrollUpdate(
        scroll_vertical=True,
        scroll_horizontal=False,
    )


def test_cursor_move_scroll_update_holds_visual_scroll_without_opt_in() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=5,
        old_column=1,
        new_column=1,
        old_pane="new",
        new_pane="new",
        visual_mode=True,
        search_query="",
    )

    assert cursor_update.cursor_move_scroll_update(
        update,
        visual_mode=True,
        scroll_in_visual=False,
        suppress_scroll=False,
    ) == cursor_update.CursorMoveScrollUpdate(
        scroll_vertical=False,
        scroll_horizontal=False,
    )


def test_cursor_move_scroll_update_scrolls_pane_moves_horizontally() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=2,
        old_column=1,
        new_column=1,
        old_pane="old",
        new_pane="new",
        visual_mode=False,
        search_query="",
    )

    assert cursor_update.cursor_move_scroll_update(
        update,
        visual_mode=False,
        scroll_in_visual=False,
        suppress_scroll=False,
    ) == cursor_update.CursorMoveScrollUpdate(
        scroll_vertical=True,
        scroll_horizontal=True,
    )


def test_cursor_move_scroll_update_suppresses_all_scroll() -> None:
    update = cursor_move_update(
        old_line=2,
        new_line=5,
        old_column=1,
        new_column=3,
        old_pane="old",
        new_pane="new",
        visual_mode=False,
        search_query="",
    )

    assert cursor_update.cursor_move_scroll_update(
        update,
        visual_mode=False,
        scroll_in_visual=True,
        suppress_scroll=True,
    ) == cursor_update.CursorMoveScrollUpdate(
        scroll_vertical=False,
        scroll_horizontal=False,
    )
