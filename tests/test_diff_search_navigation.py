import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_navigation_helpers() -> None:
    navigation = importlib.import_module("rit.ui.widgets.diff_search_navigation")

    assert diff_search.reveal_match is navigation.reveal_match
    assert diff_search.activate_match is navigation.activate_match
    assert diff_search.jump_match is navigation.jump_match
