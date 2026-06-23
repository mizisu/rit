import importlib
import pkgutil

import rit.ui.widgets


EXPECTED_INTERNAL_WIDGET_MODULE_EXPORTS = {
    "rit.ui.widgets.diff_blocks": set(),
    "rit.ui.widgets.diff_comments": {
        "COLLAPSED_THREAD_HEIGHT",
        "COMMENT_HEIGHT_ESTIMATE",
        "PENDING_DRAFT_HEIGHT_ESTIMATE",
        "active_comment_widget",
        "active_pending_draft",
        "active_thread",
        "build_comment_map",
        "clear_state",
        "comment_widgets_in_order",
        "estimate_pending_draft_height",
        "estimate_thread_height",
        "mount_comments_for_line",
        "mount_pending_drafts_for_line",
        "mount_side_aware_widget",
        "next_comment",
        "prev_comment",
        "toggle_resolve",
        "total_comments_at_line",
        "try_toggle_current",
        "update_cursor_highlight",
    },
    "rit.ui.widgets.diff_cursor": set(),
    "rit.ui.widgets.diff_highlight": set(),
    "rit.ui.widgets.diff_render": {"PREVIEW_PREFIX_WIDTH"},
    "rit.ui.widgets.diff_selection": set(),
    "rit.ui.widgets.diff_virtual": set(),
}


def test_internal_widget_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_INTERNAL_WIDGET_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)


def test_every_widget_module_defines_explicit_exports() -> None:
    missing_exports = [
        module_info.name
        for module_info in pkgutil.iter_modules(
            rit.ui.widgets.__path__,
            prefix=f"{rit.ui.widgets.__name__}.",
        )
        if not hasattr(importlib.import_module(module_info.name), "__all__")
    ]

    assert missing_exports == []
