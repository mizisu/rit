import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_display_helper() -> None:
    display = importlib.import_module("rit.ui.widgets.diff_search_display")

    assert diff_search._refresh_search_display is display.refresh_search_display
