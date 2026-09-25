from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from textual.content import Content

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.ui.widgets import diff_highlight
from rit.ui.widgets.diff_view import DiffView


@pytest.mark.parametrize(
    "ready,expected",
    [
        ((), (0, 4)),
        ((0, 1), (2, 4)),
        ((3, 4), (0, 2)),
        ((0, 2, 4), (1, 3)),
        ((0, 1, 2, 3, 4), None),
    ],
)
def test_visible_highlight_skips_ready_edges_but_honors_invalidation(
    ready: tuple[int, ...],
    expected: tuple[int, int] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = DiffView()
    lines = [DiffLine(i, i, "old", "new", is_modified=True) for i in range(5)]
    diff = FileDiff("test.py", hunks=[DiffHunk(1, 5, 1, 5, lines=lines)])
    view._diff = diff
    view.current_file = diff.filename
    view._all_lines = lines
    view._virt.active = True
    view._virt.rendered_start, view._virt.rendered_end = 0, 4
    monkeypatch.setattr(view, "WINDOW_HIGHLIGHT_BUFFER", 0)
    monkeypatch.setattr(view, "BLOCK_RENDER_LINE_THRESHOLD", 1)
    for index in ready:
        lines[index].highlighted_old_content = Content("old")
        lines[index].highlighted_new_content = Content("new")
    queue = Mock()
    monkeypatch.setattr(diff_highlight, "_queue_highlight_diff_range", queue)

    diff_highlight._ensure_visible_highlight(view)

    if expected is None:
        queue.assert_not_called()
    else:
        queue.assert_called_once_with(view, diff.filename, diff, *expected)
    queue.reset_mock()
    diff_highlight._clear_highlighted_content(view, diff)
    diff_highlight._ensure_visible_highlight(view)
    queue.assert_called_once_with(view, diff.filename, diff, 0, 4)


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
