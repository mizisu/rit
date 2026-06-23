import importlib


EXPECTED_MODULE_EXPORTS = {
    "rit.ui.widgets.diff_search_bar": {
        "close_search",
        "handle_submitted_input",
        "start_search",
    },
    "rit.ui.widgets.diff_search_commands": {
        "clear_state",
        "handle_changed",
        "handle_submitted",
    },
    "rit.ui.widgets.diff_search_display": {
        "refresh_search_display",
    },
    "rit.ui.widgets.diff_search_match_index": {
        "apply_search_highlights",
        "build_matches",
        "next_match_index_from_cursor",
        "refresh_matches",
        "search_sides_for_row",
        "sync_match_index_to_cursor",
    },
    "rit.ui.widgets.diff_search_matching": {
        "search_highlight_spans",
        "search_match_columns",
        "search_match_style",
        "search_matches_for_text",
        "search_sides_for_line",
    },
    "rit.ui.widgets.diff_search_navigation": {
        "activate_match",
        "jump_match",
        "reveal_match",
    },
    "rit.ui.widgets.diff_search_policy": {
        "next_search_match_index",
        "search_activation_placement_update",
        "search_activation_update",
        "search_change_update",
        "search_close_update",
        "search_jump_target_index",
        "search_jump_update",
        "search_match_index_at_cursor",
        "search_match_refresh",
        "search_refresh_update",
        "search_reveal_update",
        "search_start_update",
        "search_submission_request",
        "search_submit_update",
        "search_submitted_input_update",
    },
    "rit.ui.widgets.diff_search_types": {
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
    },
}


def test_canonical_search_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)
