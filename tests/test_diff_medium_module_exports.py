import importlib


EXPECTED_MEDIUM_MODULE_EXPORTS = {
    "rit.ui.widgets.diff_cursor_update": {
        "ActivePaneUpdate",
        "CursorColumnUpdate",
        "CursorFlushRequest",
        "CursorLineUpdate",
        "CursorMoveScrollUpdate",
        "CursorMoveUpdate",
        "CursorQueueUpdate",
        "CursorRepaintUpdate",
        "PaneName",
        "active_pane_update",
        "clamp_cursor_column",
        "cursor_column_update",
        "cursor_flush_request",
        "cursor_line_update",
        "cursor_lines_for_flush",
        "cursor_move_scroll_update",
        "cursor_move_update",
        "cursor_queue_update",
    },
    "rit.ui.widgets.diff_header": {
        "FILE_HEADER_CHROME_WIDTH",
        "aggregate_file_change_stats",
        "append_change_stats",
        "build_diff_header_text",
        "build_file_header_text",
        "change_stats_markup",
        "change_stats_plain",
        "file_header_min_width",
        "truncate_middle",
        "viewed_state_badge",
    },
    "rit.ui.widgets.diff_layout": {
        "can_fit_auto_split_content",
        "code_widths_for_layout",
        "file_header_width_for_layout",
        "line_number_width_for_layout",
        "preview_prefix_width_for_layout",
        "should_force_unified_for_file",
        "should_force_unified_for_hunk",
        "split_placeholder_width_for_layout",
        "split_prefix_width_for_layout",
        "unified_prefix_width_for_layout",
    },
    "rit.ui.widgets.diff_selection_range": {
        "SelectionKind",
        "SelectionSpec",
        "VisualSelectionBounds",
        "VisualSelectionDelta",
        "visible_selection_line_range",
        "visual_selection_bounds",
        "visual_selection_delta",
        "visual_selection_spec_for_line",
        "visual_selection_specs_for_visible_lines",
        "visual_selection_specs_with_dirty_lines",
    },
    "rit.ui.widgets.diff_selection_text": {
        "VisualYank",
        "normal_yank_for_line",
        "selected_text_for_visual_range",
        "visual_yank_for_range",
    },
    "rit.ui.widgets.diff_visual_mode": {
        "VisualAnchorUIUpdate",
        "VisualLineSelectionRole",
        "VisualModeState",
        "VisualModeUIUpdate",
        "VisualQueueUpdate",
        "VisualQueuedUpdate",
        "VisualTypeUIUpdate",
        "allows_column_motion",
        "enter_visual_mode",
        "exit_visual_mode",
        "toggle_visual_mode",
        "visual_anchor_ui_update",
        "visual_line_selection_role",
        "visual_mode_sub_title",
        "visual_mode_ui_update",
        "visual_queue_update",
        "visual_type_ui_update",
    },
}


def test_medium_diff_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_MEDIUM_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)
