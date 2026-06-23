import importlib

from rit.ui.widgets import diff_search


def test_diff_search_reexports_canonical_command_helpers() -> None:
    commands = importlib.import_module("rit.ui.widgets.diff_search_commands")

    assert diff_search.clear_state is commands.clear_state
    assert diff_search.handle_changed is commands.handle_changed
    assert diff_search.handle_submitted is commands.handle_submitted
