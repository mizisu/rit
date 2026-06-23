import importlib
import pkgutil

import rit
import rit.core


EXPECTED_ROOT_MODULE_EXPORTS = {
    "rit": {"__version__"},
    "rit.__main__": set(),
    "rit.app": {"RitApp"},
    "rit.cli": {"main", "parse_pr_reference"},
}

EXPECTED_CORE_MODULE_EXPORTS = {
    "rit.core": {
        "DiffHunk",
        "DiffLine",
        "DiffSide",
        "FileDiff",
        "InlineSegment",
        "SegmentType",
        "compute_line_diff",
        "compute_word_diff",
        "parse_patch",
    },
    "rit.core.datetime_utils": {
        "DATETIME_MIN_UTC",
        "datetime_min_utc",
        "datetime_sort_key",
        "is_min_datetime",
    },
    "rit.core.diff": {
        "ParsedFilePatch",
        "ParsedFilePatchSummary",
        "compute_line_diff",
        "compute_word_diff",
        "parse_file_patch_summary",
        "parse_multi_file_patch",
        "parse_patch",
    },
    "rit.core.highlight_theme": {
        "RitHighlightTheme",
        "RitLightHighlightTheme",
    },
    "rit.core.highlighting": {
        "WORD_DIFF_ADDED_STYLE",
        "WORD_DIFF_DELETED_STYLE",
        "apply_word_diff_spans",
        "highlight_diff",
        "highlight_lines_for_diff",
        "highlight_lines_for_diff_range",
        "prewarm_highlighter",
    },
    "rit.core.types": {
        "DiffHunk",
        "DiffLine",
        "DiffSide",
        "FileDiff",
        "InlineSegment",
        "SegmentType",
    },
}


def test_root_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_ROOT_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)


def test_core_modules_export_documented_surfaces() -> None:
    for module_name, expected_exports in EXPECTED_CORE_MODULE_EXPORTS.items():
        module = importlib.import_module(module_name)
        exports = tuple(module.__all__)

        assert set(exports) == expected_exports
        assert len(exports) == len(set(exports))
        assert exports == tuple(sorted(exports))
        for name in exports:
            assert hasattr(module, name)


def test_every_core_module_defines_explicit_exports() -> None:
    missing_exports = [
        module_info.name
        for module_info in pkgutil.iter_modules(
            rit.core.__path__,
            prefix=f"{rit.core.__name__}.",
        )
        if not hasattr(importlib.import_module(module_info.name), "__all__")
    ]

    assert missing_exports == []
