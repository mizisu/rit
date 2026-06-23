import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_matching_helpers() -> None:
    matching = importlib.import_module("rit.ui.widgets.diff_search_matching")

    assert diff_search.search_sides_for_line is matching.search_sides_for_line
    assert diff_search.search_match_columns is matching.search_match_columns
    assert diff_search.search_matches_for_text is matching.search_matches_for_text
    assert diff_search.search_match_style is matching.search_match_style
    assert diff_search.search_highlight_spans is matching.search_highlight_spans
