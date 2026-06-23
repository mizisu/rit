import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_bar_helpers() -> None:
    bar = importlib.import_module("rit.ui.widgets.diff_search_bar")

    assert diff_search.handle_submitted_input is bar.handle_submitted_input
    assert diff_search.close_search is bar.close_search
    assert diff_search.start_search is bar.start_search
