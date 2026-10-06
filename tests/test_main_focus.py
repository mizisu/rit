from types import SimpleNamespace
from typing import Literal
from unittest.mock import Mock, patch

import pytest
from textual.events import Key

from rit.ui.screens.main import _FILES_BINDINGS, _PR_INFO_BINDINGS, MainScreen


def test_pane_focus_bindings_use_only_ctrl_h_l() -> None:
    for bindings in (_PR_INFO_BINDINGS, _FILES_BINDINGS):
        keys = {binding.key: binding.action for binding in bindings}
        assert keys["ctrl+h"] == "focus_left"
        assert keys["ctrl+l"] == "focus_right"
        assert "H" not in keys and "L" not in keys


@pytest.mark.parametrize(
    ("key", "expected"),
    [("ctrl+h", ["left"]), ("ctrl+l", ["right"]), ("H", []), ("L", [])],
)
def test_files_focus_key_dispatch(
    monkeypatch: pytest.MonkeyPatch, key: str, expected: list[str]
) -> None:
    screen = MainScreen()
    screen.current_tab = 1
    called: list[str] = []
    monkeypatch.setattr(screen, "_text_entry_has_focus", lambda: False)
    monkeypatch.setattr(screen, "_comment_editor_has_focus", lambda: False)
    monkeypatch.setattr(screen, "action_focus_left", lambda: called.append("left"))
    monkeypatch.setattr(screen, "action_focus_right", lambda: called.append("right"))
    screen.on_key(Key(key, None))
    assert called == expected


@pytest.mark.parametrize(
    "guard", ["_text_entry_has_focus", "_comment_editor_has_focus"]
)
def test_ctrl_navigation_does_not_steal_editor_input(
    monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    screen = MainScreen()
    screen.current_tab = 1
    called: list[str] = []
    monkeypatch.setattr(screen, "_text_entry_has_focus", lambda: False)
    monkeypatch.setattr(screen, "_comment_editor_has_focus", lambda: False)
    monkeypatch.setattr(screen, guard, lambda: True)
    monkeypatch.setattr(screen, "action_focus_left", lambda: called.append("left"))
    monkeypatch.setattr(screen, "action_focus_right", lambda: called.append("right"))
    screen.on_key(Key("ctrl+h", None))
    screen.on_key(Key("ctrl+l", None))
    assert called == []
    assert screen.check_action("focus_left", ()) is False
    assert screen.check_action("focus_right", ()) is False


@pytest.mark.parametrize("tab", [0, 1])
@pytest.mark.parametrize("direction", ["left", "right"])
@pytest.mark.parametrize("moved", [True, False])
def test_focus_delegates_to_selected_tab_before_tmux(
    tab: int, direction: Literal["left", "right"], moved: bool
) -> None:
    screen = MainScreen()
    screen.current_tab = tab
    panels = [Mock(spec=["move_focus"]), Mock(spec=["move_focus"])]
    panels[tab].move_focus.return_value = moved
    navigation = Mock(spec=["select_pane"])
    with (
        patch.object(MainScreen, "app", SimpleNamespace(tmux_navigation=navigation)),
        patch.object(MainScreen, "pr_info", panels[0]),
        patch.object(MainScreen, "file_changes", panels[1]),
    ):
        if direction == "left":
            screen.action_focus_left()
        else:
            screen.action_focus_right()

    panels[tab].move_focus.assert_called_once_with(direction)
    panels[1 - tab].move_focus.assert_not_called()
    if moved:
        navigation.select_pane.assert_not_called()
    else:
        navigation.select_pane.assert_called_once_with(direction)
