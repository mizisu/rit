import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_match_index_helpers() -> None:
    match_index = importlib.import_module("rit.ui.widgets.diff_search_match_index")

    assert diff_search.search_sides_for_row is match_index.search_sides_for_row
    assert diff_search.build_matches is match_index.build_matches
    assert diff_search.apply_search_highlights is match_index.apply_search_highlights
    assert diff_search.refresh_matches is match_index.refresh_matches
    assert diff_search.sync_match_index_to_cursor is match_index.sync_match_index_to_cursor
    assert diff_search.next_match_index_from_cursor is match_index.next_match_index_from_cursor
