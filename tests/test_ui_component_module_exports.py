import importlib
import pkgutil

import rit.ui.components


EXPECTED_COMPONENT_EXPORTS = {
    "rit.ui.components.combined_diff": {
        "COMBINED_DIFF_FILENAME",
        "CombinedDiffDocument",
        "build_combined_diff_document",
        "load_missing_combined_file_diffs",
    },
    "rit.ui.components.file_changes": {
        "COMBINED_DIFF_FILENAME",
        "FileChanges",
    },
    "rit.ui.components.files_render_session": {
        "CombinedFileJump",
        "CombinedRenderRequest",
        "FilesRenderSession",
        "FullFilePreviewRestoreTarget",
        "PendingLocationJump",
    },
    "rit.ui.components.pr_info": {"PRInfo"},
    "rit.ui.components.pr_timeline": {
        "INITIAL_TIMELINE_BODY_COUNT",
        "PRTimeline",
        "TIMELINE_BODY_MOUNT_DELAY",
    },
    "rit.ui.components.pr_timeline_formatting": {
        "author_display_name",
        "format_thread_title",
        "pending_review_summary_header",
        "resolved_thread_title",
        "thread_title",
    },
    "rit.ui.components.pr_timeline_projection": {
        "TimelineItem",
        "TimelineItemKind",
        "build_timeline_items",
        "review_timeline_time",
    },
}


def test_component_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_COMPONENT_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)


def test_every_component_module_defines_explicit_exports() -> None:
    missing_exports = [
        module_info.name
        for module_info in pkgutil.iter_modules(
            rit.ui.components.__path__,
            prefix=f"{rit.ui.components.__name__}.",
        )
        if not hasattr(importlib.import_module(module_info.name), "__all__")
    ]

    assert missing_exports == []
