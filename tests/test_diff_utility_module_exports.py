import importlib


EXPECTED_UTILITY_MODULE_EXPORTS = {
    "rit.ui.widgets.diff_cursor_content": {
        "apply_cursor_to_code_content",
    },
    "rit.ui.widgets.diff_cursor_side": {
        "cursor_side_for_line",
        "resolve_active_pane_for_line",
    },
    "rit.ui.widgets.diff_location": {
        "full_preview_location_label",
        "line_index_for_location",
        "row_for_line_and_pane",
    },
    "rit.ui.widgets.diff_prefix": {
        "build_preview_prefix_content",
        "build_split_prefix",
        "build_split_prefix_content",
        "build_unified_modified_prefix_content",
        "build_unified_prefix_content",
        "preview_change_marker_content",
    },
    "rit.ui.widgets.diff_selection_content": {
        "apply_selection_to_code_content",
    },
    "rit.ui.widgets.diff_status": {
        "build_status_line",
    },
    "rit.ui.widgets.diff_styles": {
        "split_annotation_style",
        "split_code_classes",
        "split_line_style",
        "split_prefix_classes",
        "split_side_missing",
        "unified_code_classes",
        "unified_line_style",
    },
    "rit.ui.widgets.diff_word_motion": {
        "first_word_start",
        "is_word_char",
        "last_word_start",
        "next_word_end",
        "next_word_start",
        "previous_word_start",
    },
}


def test_small_diff_utility_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_UTILITY_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)
