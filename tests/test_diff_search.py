from typing import Literal

import pytest

from rit.ui.widgets import diff_search
from rit.ui.widgets import diff_search_bar
from rit.ui.widgets.diff_types import DiffSearchMatch


def test_search_sides_for_line_uses_rendered_side_in_unified_mode() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="unified",
        row_side="old",
        line_is_modified=True,
        line_is_deleted=False,
        line_is_added=False,
    ) == ("old",)


def test_search_sides_for_line_searches_both_sides_for_split_modified_line() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="old",
        line_is_modified=True,
        line_is_deleted=False,
        line_is_added=False,
    ) == ("old", "new")


def test_search_sides_for_line_uses_existing_side_for_split_single_sided_lines() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="old",
        line_is_modified=False,
        line_is_deleted=True,
        line_is_added=False,
    ) == ("old",)
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="new",
        line_is_modified=False,
        line_is_deleted=False,
        line_is_added=True,
    ) == ("new",)


def test_search_sides_for_line_uses_auto_for_split_context_line() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="auto",
        line_is_modified=False,
        line_is_deleted=False,
        line_is_added=False,
    ) == ("auto",)


def test_search_match_columns_finds_case_insensitive_non_overlapping_matches() -> None:
    assert diff_search.search_match_columns("Alpha alpha ALPHA", "alpha") == (0, 6, 12)


def test_search_match_columns_advances_by_query_length() -> None:
    assert diff_search.search_match_columns("aaaa", "aa") == (0, 2)


def test_search_match_columns_returns_empty_for_empty_query_or_text() -> None:
    assert diff_search.search_match_columns("alpha", "") == ()
    assert diff_search.search_match_columns("", "alpha") == ()


def test_search_match_style_uses_stronger_warning_for_active_match() -> None:
    assert diff_search.search_match_style(
        match_index=2,
        active_match_index=2,
    ) == "on $warning 45%"


def test_search_match_style_uses_softer_warning_for_inactive_match() -> None:
    assert diff_search.search_match_style(
        match_index=1,
        active_match_index=2,
    ) == "on $warning 25%"


def test_search_matches_for_text_builds_matches_with_row_metadata() -> None:
    assert diff_search.search_matches_for_text(
        text="foo and Foo",
        query="foo",
        row_index=7,
        line_index=3,
        side="new",
    ) == (
        DiffSearchMatch(row_index=7, line_index=3, side="new", column=0),
        DiffSearchMatch(row_index=7, line_index=3, side="new", column=8),
    )


def test_search_matches_for_text_returns_empty_without_matches() -> None:
    assert (
        diff_search.search_matches_for_text(
            text="alpha",
            query="z",
            row_index=7,
            line_index=3,
            side="new",
        )
        == ()
    )


def test_search_reveal_update_ignores_missing_target_row() -> None:
    assert diff_search.search_reveal_update(
        target_exists=False,
        has_target_widget=False,
        target_visible=False,
    ) == diff_search.SearchRevealUpdate(
        action="ignore",
        viewport_offset=0,
    )


def test_search_reveal_update_scrolls_to_target_widget_first() -> None:
    assert diff_search.search_reveal_update(
        target_exists=True,
        has_target_widget=True,
        target_visible=True,
    ) == diff_search.SearchRevealUpdate(
        action="scroll_widget",
        viewport_offset=0,
    )


def test_search_reveal_update_ignores_visible_row_without_widget() -> None:
    assert diff_search.search_reveal_update(
        target_exists=True,
        has_target_widget=False,
        target_visible=True,
    ) == diff_search.SearchRevealUpdate(
        action="ignore",
        viewport_offset=0,
    )


def test_search_reveal_update_scrolls_hidden_row_without_widget() -> None:
    assert diff_search.search_reveal_update(
        target_exists=True,
        has_target_widget=False,
        target_visible=False,
    ) == diff_search.SearchRevealUpdate(
        action="scroll_row",
        viewport_offset=0,
    )


def test_search_highlight_spans_filters_line_side_and_marks_active_match() -> None:
    matches = [
        _match(0, 1, "new", 2),
        _match(0, 1, "old", 3),
        _match(0, 1, "new", 8),
        _match(1, 2, "new", 0),
    ]

    assert diff_search.search_highlight_spans(
        matches,
        line_index=1,
        side="new",
        query_length=4,
        active_match_index=2,
    ) == (
        diff_search.SearchHighlightSpan(
            start=2,
            end=6,
            style="on $warning 25%",
        ),
        diff_search.SearchHighlightSpan(
            start=8,
            end=12,
            style="on $warning 45%",
        ),
    )


def test_search_highlight_spans_returns_empty_without_line_side_matches() -> None:
    assert (
        diff_search.search_highlight_spans(
            [_match(0, 1, "old", 2)],
            line_index=1,
            side="new",
            query_length=4,
            active_match_index=0,
        )
        == ()
    )


def test_search_refresh_update_marks_current_and_previous_match_lines_dirty() -> None:
    matches = [
        _match(0, 1, "auto", 0),
        _match(2, 3, "auto", 5),
    ]

    assert diff_search.search_refresh_update(
        matches,
        previous_match_lines={3, 9},
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=frozenset({1, 3, 9}),
        previous_match_lines=frozenset({1, 3}),
    )


def test_search_refresh_update_keeps_previous_lines_dirty_when_matches_clear() -> None:
    assert diff_search.search_refresh_update(
        [],
        previous_match_lines={3, 9},
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=frozenset({3, 9}),
        previous_match_lines=frozenset(),
    )


def test_search_activation_update_marks_new_and_previous_match_lines_dirty() -> None:
    matches = [
        _match(0, 1, "auto", 0),
        _match(2, 3, "new", 5),
    ]

    assert diff_search.search_activation_update(
        matches,
        old_index=0,
        target_index=1,
    ) == diff_search.SearchActivationUpdate(
        match=matches[1],
        dirty_lines=frozenset({1, 3}),
        pane="new",
        update_active_pane=True,
    )


def test_search_activation_update_keeps_auto_match_pane_neutral() -> None:
    matches = [_match(0, 1, "auto", 0)]

    assert diff_search.search_activation_update(
        matches,
        old_index=-1,
        target_index=0,
    ) == diff_search.SearchActivationUpdate(
        match=matches[0],
        dirty_lines=frozenset({1}),
        pane=None,
        update_active_pane=False,
    )


def test_search_activation_update_returns_none_for_invalid_target() -> None:
    assert (
        diff_search.search_activation_update(
            [_match(0, 1, "auto", 0)],
            old_index=-1,
            target_index=3,
        )
        is None
    )


def test_search_activation_placement_update_jumps_when_target_row_is_hidden() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=False,
        has_current_row=True,
        row_distance=0,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="jump_anchor",
        viewport_offset=0,
        reveal_horizontal=True,
    )


def test_search_activation_placement_update_jumps_without_current_row() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=True,
        has_current_row=False,
        row_distance=0,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="jump_anchor",
        viewport_offset=0,
        reveal_horizontal=True,
    )


def test_search_activation_placement_update_jumps_when_match_is_far() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=True,
        has_current_row=True,
        row_distance=11,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="jump_anchor",
        viewport_offset=0,
        reveal_horizontal=True,
    )


def test_search_activation_placement_update_moves_cursor_when_match_is_near() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=True,
        has_current_row=True,
        row_distance=10,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="move_cursor",
        viewport_offset=0,
        reveal_horizontal=False,
    )


def test_search_activation_placement_update_moves_cursor_without_target_row() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=False,
        target_row_visible=False,
        has_current_row=True,
        row_distance=99,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="move_cursor",
        viewport_offset=0,
        reveal_horizontal=False,
    )


def _match(
    row_index: int,
    line_index: int,
    side: Literal["old", "new", "auto"],
    column: int,
) -> DiffSearchMatch:
    return DiffSearchMatch(
        row_index=row_index,
        line_index=line_index,
        side=side,
        column=column,
    )


def test_search_match_index_at_cursor_finds_exact_line_side_and_column() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "old", 4),
        _match(1, 1, "new", 4),
    ]

    assert (
        diff_search.search_match_index_at_cursor(
            matches,
            current_line=1,
            current_side="new",
            current_column=4,
        )
        == 2
    )


def test_search_match_index_at_cursor_returns_minus_one_without_exact_match() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "old", 4),
    ]

    assert (
        diff_search.search_match_index_at_cursor(
            matches,
            current_line=1,
            current_side="new",
            current_column=4,
        )
        == -1
    )


def test_search_match_refresh_clears_matches_without_query() -> None:
    matches = [_match(0, 0, "auto", 2)]

    assert diff_search.search_match_refresh(
        query="",
        matches=matches,
        current_line=0,
        current_side="auto",
        current_column=2,
    ) == diff_search.SearchMatchRefresh(
        matches=[],
        match_index=-1,
    )


def test_search_match_refresh_sets_index_from_cursor_for_query() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "new", 4),
    ]

    assert diff_search.search_match_refresh(
        query="needle",
        matches=matches,
        current_line=1,
        current_side="new",
        current_column=4,
    ) == diff_search.SearchMatchRefresh(
        matches=matches,
        match_index=1,
    )


def test_search_change_update_clears_state_for_blank_input() -> None:
    assert diff_search.search_change_update(
        "  ",
        matches=[_match(0, 0, "auto", 2)],
        cursor_target_index=0,
    ) == diff_search.SearchChangeUpdate(
        query="",
        matches=[],
        match_index=-1,
        reveal_index=None,
    )


def test_search_change_update_uses_next_cursor_target_for_search_input() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "new", 4),
    ]

    assert diff_search.search_change_update(
        "  needle  ",
        matches=matches,
        cursor_target_index=1,
    ) == diff_search.SearchChangeUpdate(
        query="needle",
        matches=matches,
        match_index=1,
        reveal_index=1,
    )


def test_search_change_update_omits_reveal_without_matches() -> None:
    assert diff_search.search_change_update(
        "needle",
        matches=[],
        cursor_target_index=-1,
    ) == diff_search.SearchChangeUpdate(
        query="needle",
        matches=[],
        match_index=-1,
        reveal_index=None,
    )


def test_next_search_match_index_prefers_later_row_then_later_column() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "auto", 4),
        _match(2, 2, "auto", 0),
    ]

    assert (
        diff_search.next_search_match_index(
            matches,
            current_row_index=1,
            current_side="auto",
            current_column=2,
        )
        == 1
    )
    assert (
        diff_search.next_search_match_index(
            matches,
            current_row_index=1,
            current_side="auto",
            current_column=4,
        )
        == 2
    )


def test_next_search_match_index_wraps_when_cursor_is_after_last_match() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(2, 2, "auto", 4),
    ]

    assert (
        diff_search.next_search_match_index(
            matches,
            current_row_index=9,
            current_side="auto",
            current_column=0,
        )
        == 0
    )


def test_next_search_match_index_returns_minus_one_without_matches() -> None:
    assert (
        diff_search.next_search_match_index(
            [],
            current_row_index=0,
            current_side="auto",
            current_column=0,
        )
        == -1
    )


def test_search_jump_target_index_moves_relative_to_active_match() -> None:
    assert (
        diff_search.search_jump_target_index(
            current_match_index=1,
            match_count=3,
            cursor_target_index=0,
            direction=1,
        )
        == 2
    )
    assert (
        diff_search.search_jump_target_index(
            current_match_index=0,
            match_count=3,
            cursor_target_index=0,
            direction=-1,
        )
        == 2
    )


def test_search_jump_target_index_uses_cursor_when_no_match_is_active() -> None:
    assert (
        diff_search.search_jump_target_index(
            current_match_index=-1,
            match_count=3,
            cursor_target_index=1,
            direction=1,
        )
        == 1
    )
    assert (
        diff_search.search_jump_target_index(
            current_match_index=-1,
            match_count=3,
            cursor_target_index=1,
            direction=-1,
        )
        == 2
    )


def test_search_jump_target_index_returns_minus_one_without_matches() -> None:
    assert (
        diff_search.search_jump_target_index(
            current_match_index=-1,
            match_count=0,
            cursor_target_index=0,
            direction=1,
        )
        == -1
    )


def test_search_jump_update_reports_inactive_search() -> None:
    assert diff_search.search_jump_update(
        query="",
        match_count=0,
        current_match_index=-1,
        cursor_target_index=-1,
        direction=1,
    ) == diff_search.SearchJumpUpdate(
        action="inactive",
        target_index=-1,
        flash_message="No active search",
        flash_style="warning",
        update_status=False,
    )


def test_search_jump_update_reports_active_query_without_matches() -> None:
    assert diff_search.search_jump_update(
        query="needle",
        match_count=0,
        current_match_index=-1,
        cursor_target_index=-1,
        direction=1,
    ) == diff_search.SearchJumpUpdate(
        action="no_matches",
        target_index=-1,
        flash_message="No matches: needle",
        flash_style="warning",
        update_status=True,
    )


def test_search_jump_update_activates_next_target() -> None:
    assert diff_search.search_jump_update(
        query="needle",
        match_count=3,
        current_match_index=1,
        cursor_target_index=0,
        direction=1,
    ) == diff_search.SearchJumpUpdate(
        action="activate",
        target_index=2,
        flash_message=None,
        flash_style=None,
        update_status=False,
    )


def test_search_submission_request_ignores_missing_query() -> None:
    assert diff_search.search_submission_request(None) == diff_search.SearchSubmissionRequest(
        action="ignore",
        query="",
    )


def test_search_submission_request_clears_blank_query() -> None:
    assert diff_search.search_submission_request("  \t ") == diff_search.SearchSubmissionRequest(
        action="clear",
        query="",
    )


def test_search_submission_request_normalizes_search_query() -> None:
    assert diff_search.search_submission_request("  needle  ") == diff_search.SearchSubmissionRequest(
        action="search",
        query="needle",
    )


def test_search_submit_update_ignores_missing_query() -> None:
    assert diff_search.search_submit_update(
        None,
        matches=[],
        cursor_target_index=-1,
    ) == diff_search.SearchSubmitUpdate(
        action="ignore",
        query="",
        match_index=-1,
        flash_message=None,
        flash_style=None,
    )


def test_search_submit_update_clears_blank_query() -> None:
    assert diff_search.search_submit_update(
        "  ",
        matches=[_match(0, 0, "auto", 2)],
        cursor_target_index=0,
    ) == diff_search.SearchSubmitUpdate(
        action="clear",
        query="",
        match_index=-1,
        flash_message="Search cleared",
        flash_style=None,
    )


def test_search_submit_update_reports_no_matches() -> None:
    assert diff_search.search_submit_update(
        " needle ",
        matches=[],
        cursor_target_index=-1,
    ) == diff_search.SearchSubmitUpdate(
        action="no_matches",
        query="needle",
        match_index=-1,
        flash_message="No matches: needle",
        flash_style="warning",
    )


def test_search_submit_update_activates_cursor_target_for_matches() -> None:
    matches = [_match(0, 0, "auto", 2), _match(1, 1, "auto", 4)]

    assert diff_search.search_submit_update(
        "needle",
        matches=matches,
        cursor_target_index=1,
    ) == diff_search.SearchSubmitUpdate(
        action="activate",
        query="needle",
        match_index=1,
        flash_message=None,
        flash_style=None,
    )


def test_handle_submitted_input_closes_bar_focuses_view_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str]] = []

    class SearchBar:
        display = True

    class View:
        _search_bar_widget = SearchBar()
        focused = False

        def focus(self) -> None:
            self.focused = True

    def handle_submitted(view: object, query: str | None) -> None:
        assert query is not None
        calls.append((view, query))

    monkeypatch.setattr(diff_search_bar, "handle_submitted", handle_submitted)

    view = View()
    diff_search.handle_submitted_input(view, "needle")

    assert view._search_bar_widget.display is False
    assert view.focused is True
    assert calls == [(view, "needle")]


def test_search_submitted_input_update_closes_existing_bar_and_submits_value() -> None:
    assert diff_search.search_submitted_input_update(
        has_bar=True,
        value="needle",
    ) == diff_search.SearchSubmittedInputUpdate(
        close_bar=True,
        focus_view=True,
        submit_query="needle",
    )


def test_search_submitted_input_update_submits_without_bar() -> None:
    assert diff_search.search_submitted_input_update(
        has_bar=False,
        value="needle",
    ) == diff_search.SearchSubmittedInputUpdate(
        close_bar=False,
        focus_view=True,
        submit_query="needle",
    )


def test_search_start_update_opens_with_current_query() -> None:
    assert diff_search.search_start_update(
        has_bar=True,
        has_input=True,
        query="needle",
    ) == diff_search.SearchStartUpdate(
        action="open",
        input_value="needle",
        focus_input=True,
    )


def test_search_start_update_ignores_missing_widgets() -> None:
    assert diff_search.search_start_update(
        has_bar=False,
        has_input=True,
        query="needle",
    ) == diff_search.SearchStartUpdate(
        action="ignore",
        input_value="",
        focus_input=False,
    )


def test_search_close_update_ignores_missing_or_hidden_bar() -> None:
    assert diff_search.search_close_update(
        has_bar=True,
        bar_displayed=False,
        clear_query=True,
    ) == diff_search.SearchCloseUpdate(
        action="ignore",
        clear_state=False,
        refresh_display=False,
        update_status=False,
        focus_view=False,
    )


def test_search_close_update_closes_visible_bar_with_requested_clear_state() -> None:
    assert diff_search.search_close_update(
        has_bar=True,
        bar_displayed=True,
        clear_query=True,
    ) == diff_search.SearchCloseUpdate(
        action="close",
        clear_state=True,
        refresh_display=True,
        update_status=True,
        focus_view=True,
    )


def test_start_search_opens_bar_restores_query_and_focuses_input() -> None:
    class SearchBar:
        display = False

    class SearchInput:
        value = ""
        focused = False

        def focus(self) -> None:
            self.focused = True

    class View:
        _search_bar_widget = SearchBar()
        _search_input_widget = SearchInput()
        _search_query = "needle"

    view = View()
    diff_search.start_search(view)

    assert view._search_bar_widget.display is True
    assert view._search_input_widget.value == "needle"
    assert view._search_input_widget.focused is True


def test_start_search_ignores_missing_widgets() -> None:
    class View:
        _search_bar_widget = None
        _search_input_widget = None
        _search_query = "needle"

    diff_search.start_search(View())


def test_close_search_closes_visible_bar_clears_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class SearchBar:
        display = True

    class View:
        _search_bar_widget = SearchBar()

        def focus(self) -> None:
            calls.append("focus")

        def _update_status_line(self) -> None:
            calls.append("status")

    def clear_state(view: object) -> None:
        calls.append("clear")

    def refresh_search_display(view: object) -> None:
        calls.append("refresh")

    monkeypatch.setattr(diff_search_bar, "clear_state", clear_state)
    monkeypatch.setattr(diff_search_bar, "refresh_search_display", refresh_search_display)

    view = View()
    diff_search.close_search(view, clear_query=True)

    assert view._search_bar_widget.display is False
    assert calls == ["clear", "refresh", "status", "focus"]


def test_close_search_ignores_missing_or_hidden_bar() -> None:
    class HiddenView:
        class SearchBar:
            display = False

        _search_bar_widget = SearchBar()

        def focus(self) -> None:
            raise AssertionError("hidden search should not refocus")

    diff_search.close_search(HiddenView(), clear_query=True)
