import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_update_types() -> None:
    search_types = importlib.import_module("rit.ui.widgets.diff_search_types")

    assert diff_search.SearchRefreshUpdate is search_types.SearchRefreshUpdate
    assert diff_search.SearchActivationUpdate is search_types.SearchActivationUpdate
    assert (
        diff_search.SearchActivationPlacementUpdate
        is search_types.SearchActivationPlacementUpdate
    )
    assert diff_search.SearchRevealUpdate is search_types.SearchRevealUpdate
    assert diff_search.SearchSubmitUpdate is search_types.SearchSubmitUpdate
    assert diff_search.SearchJumpUpdate is search_types.SearchJumpUpdate
