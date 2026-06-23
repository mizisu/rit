from types import SimpleNamespace

import pytest

from rit.ui.widgets import diff_highlight
from rit.ui.widgets.diff_view import DiffView


def test_current_highlight_dark_mode_defaults_to_dark_without_active_app() -> None:
    assert diff_highlight._current_highlight_dark_mode(DiffView()) is True


def test_current_highlight_dark_mode_uses_current_theme_dark_flag() -> None:
    view = SimpleNamespace(
        app=SimpleNamespace(
            available_themes={"light": SimpleNamespace(dark=False)},
            theme="light",
        )
    )

    assert diff_highlight._current_highlight_dark_mode(view) is False


def test_current_highlight_dark_mode_reraises_unexpected_theme_errors() -> None:
    class BrokenThemes:
        def get(self, _theme: str) -> object:
            raise RuntimeError("theme registry failed")

    view = SimpleNamespace(
        app=SimpleNamespace(
            available_themes=BrokenThemes(),
            theme="broken",
        )
    )

    with pytest.raises(RuntimeError, match="theme registry failed"):
        diff_highlight._current_highlight_dark_mode(view)
