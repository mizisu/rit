import importlib
import pkgutil

import rit.ui
import rit.ui.screens


EXPECTED_UI_MODULE_EXPORTS = {
    "rit.ui": set(),
    "rit.ui.collapsible_markdown": {
        "CopyableCodeBlock",
        "DetailsBlock",
        "LAZY_LOAD_THRESHOLD",
        "LazyCollapsible",
        "MarkdownCodePart",
        "MarkdownPart",
        "mount_markdown_code_parts",
        "mount_markdown_with_details",
        "parse_details_blocks",
        "parse_fenced_code_blocks",
    },
    "rit.ui.icons": {
        "DEFAULT_FILE_ICON",
        "DIR_ICON",
        "get_file_icon",
    },
    "rit.ui.markdown_images": {
        "AvailableWidthProvider",
        "ImageFetchError",
        "ImageFetcher",
        "ImageViewerScreen",
        "MarkdownImageBlock",
        "MarkdownImagePart",
        "MarkdownImageRef",
        "MarkdownImageTable",
        "MarkdownImageTableCell",
        "MarkdownImageTableData",
        "MarkdownImageTableRow",
        "fetch_image_bytes",
        "fetch_image_bytes_async",
        "mount_markdown_image_parts",
        "parse_markdown_image_parts",
    },
    "rit.ui.messages": {
        "Flash",
        "SettingChanged",
    },
    "rit.ui.protocols": {
        "NavigableProtocol",
    },
    "rit.ui.terminal_graphics": {
        "TerminalGraphicsTransport",
        "configure_terminal_graphics",
        "detect_terminal_graphics_transport",
        "terminal_graphics_status_message",
        "wrap_tmux_passthrough_sequence",
    },
}

EXPECTED_SCREEN_MODULE_EXPORTS = {
    "rit.ui.screens": set(),
    "rit.ui.screens.branch_picker": {"BranchPickerScreen"},
    "rit.ui.screens.file_picker": {
        "FilePickerMatch",
        "FilePickerScreen",
        "rank_file_matches",
    },
    "rit.ui.screens.main": {"MainScreen"},
    "rit.ui.screens.multi_select_picker": {
        "MultiSelectItem",
        "MultiSelectPickerScreen",
        "MultiSelectResult",
    },
    "rit.ui.screens.review_submit": {
        "ReviewEvent",
        "ReviewSubmitScreen",
    },
    "rit.ui.screens.settings": {"SettingsScreen"},
}


def test_ui_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_UI_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)


def test_screen_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_SCREEN_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)


def test_every_ui_leaf_module_defines_explicit_exports() -> None:
    missing_exports = [
        module_info.name
        for module_info in pkgutil.iter_modules(
            rit.ui.__path__,
            prefix=f"{rit.ui.__name__}.",
        )
        if not hasattr(importlib.import_module(module_info.name), "__all__")
    ]

    assert missing_exports == []


def test_every_screen_module_defines_explicit_exports() -> None:
    missing_exports = [
        module_info.name
        for module_info in pkgutil.iter_modules(
            rit.ui.screens.__path__,
            prefix=f"{rit.ui.screens.__name__}.",
        )
        if not hasattr(importlib.import_module(module_info.name), "__all__")
    ]

    assert missing_exports == []
