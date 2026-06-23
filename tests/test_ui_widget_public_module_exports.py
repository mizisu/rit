import importlib


EXPECTED_WIDGET_FACADE_EXPORTS = {
    "CommentCard",
    "DiffView",
    "FileTree",
    "FlashWidget",
    "Header",
}

EXPECTED_WIDGET_MODULE_EXPORTS = {
    "rit.ui.widgets.comment_card": {
        "BODY_PREVIEW_RETIRE_DELAY",
        "CommentCard",
    },
    "rit.ui.widgets.comment_editor": {
        "EditorKind",
        "InlineCommentEditor",
        "SubmitMode",
    },
    "rit.ui.widgets.diff_full_file_preview": {
        "FullFilePreviewAction",
        "FullFileRestorePosition",
        "build_full_file_diff",
        "choose_full_file_preview_action",
        "full_file_anchor_line_index",
        "full_file_preview_target",
        "full_file_restore_line_index",
        "nearest_full_file_anchor_for_deleted_line",
        "selected_full_file_anchor",
    },
    "rit.ui.widgets.diff_geometry": {
        "DiffGeometry",
        "FILE_DIFF_HEADER_HEIGHT",
        "ViewportGeometry",
        "build_diff_geometry",
        "cursor_viewport_offset",
        "hunk_lines_for_window",
        "is_line_rendered",
        "line_index_at_vertical_offset",
        "merge_line_ranges",
        "render_height_for_line",
        "rendered_line_bounds",
        "row_is_visible",
        "row_vertical_bounds",
        "scroll_target_for_row_bottom",
        "scroll_target_for_row_viewport_offset",
        "scroll_target_for_span",
        "should_render_hunk_header",
        "viewport_center_line",
        "virtual_bottom_buffer_height",
        "virtual_top_buffer_height",
    },
    "rit.ui.widgets.diff_plan": {
        "DiffPlan",
        "RenderedRowsPlan",
        "build_diff_plan",
        "build_rendered_rows",
    },
    "rit.ui.widgets.diff_types": {
        "CursorUIState",
        "DEFAULT_DIFF_LAYOUT",
        "DiffLayout",
        "DiffSearchMatch",
        "HighlightState",
        "RenderedRow",
        "SplitBlockLineStaticData",
        "SplitDiffBlock",
        "UnifiedBlockRowStaticData",
        "UnifiedDiffBlock",
        "VirtualState",
    },
    "rit.ui.widgets.diff_view": {
        "DiffView",
        "SplitDiffBlock",
        "UnifiedDiffBlock",
    },
    "rit.ui.widgets.diff_visual": {
        "DiffCode",
        "LineAnnotations",
        "LineContent",
        "MISSING_SIDE_BACKGROUND_STYLE",
        "MISSING_SIDE_HATCH",
        "MISSING_SIDE_HATCH_STEP",
        "MISSING_SIDE_HATCH_STYLE",
        "MISSING_SIDE_STYLE",
        "SyncedCodeScroll",
        "missing_side_hatch_text",
    },
    "rit.ui.widgets.file_tree": {"FileTree"},
    "rit.ui.widgets.flash": {"FlashWidget"},
    "rit.ui.widgets.header": {"Header", "PRStatus"},
    "rit.ui.widgets.resize_handle": {"ResizeHandle"},
    "rit.ui.widgets.review_thread_card": {
        "ReviewThreadCard",
        "ReviewThreadItem",
    },
}


def test_widgets_facade_exports_documented_surface() -> None:
    module = importlib.import_module("rit.ui.widgets")
    exports = tuple(module.__all__)

    assert set(exports) == EXPECTED_WIDGET_FACADE_EXPORTS
    assert len(exports) == len(set(exports))
    assert exports == tuple(sorted(exports))
    for name in exports:
        assert hasattr(module, name)


def test_public_widget_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_WIDGET_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)
