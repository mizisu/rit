from rit.ui.widgets import diff_search


EXPECTED_DIFF_SEARCH_EXPORTS = {
    "_refresh_search_display",
    "DiffSearchMatch",
    "FlashStyle",
    "SearchActivationPlacementAction",
    "SearchActivationPlacementUpdate",
    "SearchActivationUpdate",
    "SearchChangeUpdate",
    "SearchCloseAction",
    "SearchCloseUpdate",
    "SearchHighlightSpan",
    "SearchJumpAction",
    "SearchJumpUpdate",
    "SearchMatchRefresh",
    "SearchPane",
    "SearchRefreshUpdate",
    "SearchRevealAction",
    "SearchRevealUpdate",
    "SearchSide",
    "SearchStartAction",
    "SearchStartUpdate",
    "SearchSubmissionAction",
    "SearchSubmissionRequest",
    "SearchSubmitAction",
    "SearchSubmitUpdate",
    "SearchSubmittedInputUpdate",
    "activate_match",
    "apply_search_highlights",
    "build_matches",
    "clear_state",
    "close_search",
    "handle_changed",
    "handle_submitted",
    "handle_submitted_input",
    "jump_match",
    "next_match_index_from_cursor",
    "next_search_match_index",
    "refresh_matches",
    "reveal_match",
    "search_activation_placement_update",
    "search_activation_update",
    "search_change_update",
    "search_close_update",
    "search_highlight_spans",
    "search_jump_target_index",
    "search_jump_update",
    "search_match_columns",
    "search_match_index_at_cursor",
    "search_match_refresh",
    "search_match_style",
    "search_matches_for_text",
    "search_refresh_update",
    "search_reveal_update",
    "search_sides_for_line",
    "search_sides_for_row",
    "search_start_update",
    "search_submission_request",
    "search_submit_update",
    "search_submitted_input_update",
    "start_search",
    "sync_match_index_to_cursor",
}


def test_diff_search_exports_documented_compatibility_surface() -> None:
    exports = tuple(diff_search.__all__)

    assert set(exports) == EXPECTED_DIFF_SEARCH_EXPORTS
    assert len(exports) == len(set(exports))
    assert exports == tuple(sorted(exports))
    for name in exports:
        assert hasattr(diff_search, name)
