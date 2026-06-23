import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_policy_helpers() -> None:
    policy = importlib.import_module("rit.ui.widgets.diff_search_policy")

    assert diff_search.search_reveal_update is policy.search_reveal_update
    assert diff_search.search_refresh_update is policy.search_refresh_update
    assert diff_search.search_activation_update is policy.search_activation_update
    assert (
        diff_search.search_activation_placement_update
        is policy.search_activation_placement_update
    )
    assert diff_search.search_change_update is policy.search_change_update
    assert diff_search.search_submit_update is policy.search_submit_update
    assert diff_search.search_jump_update is policy.search_jump_update
    assert diff_search.search_start_update is policy.search_start_update
    assert diff_search.search_close_update is policy.search_close_update
