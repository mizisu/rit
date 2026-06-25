import pytest

from rit.core.types import DiffLine
from rit.ui.messages import Flash
from rit.ui.widgets import diff_selection
from rit.ui.widgets.diff_selection_text import VisualYank


class _YankView:
    def __init__(self) -> None:
        self.copied: str | None = None
        self.messages: list[Flash] = []

    def _copy_to_clipboard(self, text: str) -> None:
        self.copied = text

    def post_message(self, message: Flash) -> None:
        self.messages.append(message)


class _NoIterLines(list):
    def __iter__(self):
        raise AssertionError("visual yank should not scan every diff line")


class _VisualYankView(_YankView):
    def __init__(self) -> None:
        super().__init__()
        self._all_lines = _NoIterLines(
            [
                DiffLine(1, 1, new_content="line0"),
                DiffLine(2, 2, new_content="line1"),
                DiffLine(3, 3, new_content="line2"),
                DiffLine(4, 4, new_content="line3"),
            ]
        )
        self.visual_mode = True
        self.visual_type = "line"
        self.visual_anchor_line = 1
        self.visual_anchor_column = 0
        self.cursor_line = 2
        self.cursor_column = 0

    def _get_line_text(self, line: DiffLine) -> str:
        return line.new_content


def test_update_selection_highlighting_uses_dirty_lines_without_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DirtyLines:
        def __bool__(self) -> bool:
            return True

        def __iter__(self):
            return iter((2, 4))

        def __len__(self) -> int:
            raise AssertionError("selection update should not copy dirty lines")

    class View:
        visual_mode = True
        visual_anchor_line = 1
        _all_lines = [DiffLine(1, 1), DiffLine(2, 2), DiffLine(3, 3)]
        is_mounted = True
        _visual_selection_specs = {1: (0, 1, "char")}

    computed: list[int] = []
    cleared: list[int] = []
    applied: list[int] = []

    monkeypatch.setattr(
        diff_selection,
        "_compute_selection_spec_for_line",
        lambda _view, line_idx: computed.append(line_idx) or (0, line_idx, "char"),
    )
    monkeypatch.setattr(
        diff_selection,
        "set",
        lambda _values: (_ for _ in ()).throw(
            AssertionError("selection update should not copy dirty lines")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        diff_selection,
        "_clear_line_selection",
        lambda _view, line_idx: cleared.append(line_idx),
    )
    monkeypatch.setattr(
        diff_selection,
        "_apply_line_selection",
        lambda _view, line_idx, _start, _end: applied.append(line_idx),
    )

    view = View()

    diff_selection._update_selection_highlighting(view, DirtyLines())

    assert computed == [2, 4]
    assert cleared == []
    assert applied == [2, 4]
    assert view._visual_selection_specs == {
        1: (0, 1, "char"),
        2: (0, 2, "char"),
        4: (0, 4, "char"),
    }


def test_update_selection_highlighting_applies_dirty_specs_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        visual_mode = True
        visual_anchor_line = 1
        _all_lines = [
            DiffLine(1, 1),
            DiffLine(2, 2),
            DiffLine(3, 3),
            DiffLine(4, 4),
            DiffLine(5, 5),
        ]
        is_mounted = True

        def __init__(self) -> None:
            self._visual_selection_specs = {
                1: (0, None, "line"),
                2: (0, 2, "char"),
                4: (0, 4, "char"),
            }

    computed: list[int] = []
    cleared: list[int] = []
    applied: list[tuple[int, int, int | None]] = []

    def compute_spec(_view: View, line_idx: int):
        computed.append(line_idx)
        if line_idx == 2:
            return (0, 20, "char")
        return None

    monkeypatch.setattr(
        diff_selection,
        "_compute_selection_spec_for_line",
        compute_spec,
    )
    monkeypatch.setattr(
        diff_selection._selection_range,
        "visual_selection_specs_with_dirty_lines",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("incremental selection update should not copy all specs")
        ),
    )
    monkeypatch.setattr(
        diff_selection,
        "_clear_line_selection",
        lambda _view, line_idx: cleared.append(line_idx),
    )
    monkeypatch.setattr(
        diff_selection,
        "_apply_line_selection",
        lambda _view, line_idx, start, end: applied.append((line_idx, start, end)),
    )

    view = View()
    original_specs = view._visual_selection_specs

    diff_selection._update_selection_highlighting(view, {2, 4})

    assert computed == [2, 4]
    assert cleared == [4]
    assert applied == [(2, 0, 20)]
    assert view._visual_selection_specs is original_specs
    assert view._visual_selection_specs == {
        1: (0, None, "line"),
        2: (0, 20, "char"),
    }


def test_update_selection_highlighting_skips_sort_for_single_dirty_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        visual_mode = True
        visual_anchor_line = 1
        _all_lines = [DiffLine(1, 1), DiffLine(2, 2), DiffLine(3, 3)]
        is_mounted = True
        _visual_selection_specs = {1: (0, 1, "char")}

    applied: list[int] = []

    monkeypatch.setattr(
        diff_selection,
        "_compute_selection_spec_for_line",
        lambda _view, line_idx: (0, line_idx, "char"),
    )
    monkeypatch.setattr(
        diff_selection,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single-line selection repaint should not sort")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        diff_selection,
        "_clear_line_selection",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("nothing should be cleared")
        ),
    )
    monkeypatch.setattr(
        diff_selection,
        "_apply_line_selection",
        lambda _view, line_idx, _start, _end: applied.append(line_idx),
    )

    view = View()

    diff_selection._update_selection_highlighting(view, {2})

    assert applied == [2]
    assert view._visual_selection_specs == {
        1: (0, 1, "char"),
        2: (0, 2, "char"),
    }


def test_update_selection_highlighting_skips_sort_for_two_dirty_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        visual_mode = True
        visual_anchor_line = 1
        _all_lines = [
            DiffLine(1, 1),
            DiffLine(2, 2),
            DiffLine(3, 3),
            DiffLine(4, 4),
            DiffLine(5, 5),
        ]
        is_mounted = True

        def __init__(self) -> None:
            self._visual_selection_specs = {
                1: (0, 1, "char"),
            }

    applied: list[int] = []
    cleared: list[int] = []

    def compute_spec(_view: View, line_idx: int):
        return (0, line_idx, "char")

    monkeypatch.setattr(
        diff_selection,
        "_compute_selection_spec_for_line",
        compute_spec,
    )
    monkeypatch.setattr(
        diff_selection,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("two-line selection repaint should not sort")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        diff_selection,
        "_clear_line_selection",
        lambda _view, line_idx: cleared.append(line_idx),
    )
    monkeypatch.setattr(
        diff_selection,
        "_apply_line_selection",
        lambda _view, line_idx, _start, _end: applied.append(line_idx),
    )

    view = View()

    diff_selection._update_selection_highlighting(view, {4, 2})

    assert cleared == []
    assert applied == [2, 4]
    assert view._visual_selection_specs == {
        1: (0, 1, "char"),
        2: (0, 2, "char"),
        4: (0, 4, "char"),
    }


def test_single_line_selection_refresh_uses_singleton_tuple_for_grouped_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        _all_lines = [DiffLine(old_line_no=1, new_line_no=1)]

    calls: list[tuple[int, ...]] = []

    def refresh_grouped_blocks(_view: View, line_indices) -> bool:
        assert not isinstance(line_indices, set)
        calls.append(tuple(line_indices))
        return True

    monkeypatch.setattr(
        diff_selection._blocks,
        "_refresh_grouped_blocks_for_lines",
        refresh_grouped_blocks,
    )

    view = View()

    diff_selection._clear_line_selection(view, 0)
    diff_selection._apply_line_selection(view, 0, 0, None)

    assert calls == [(0,), (0,)]


def test_visual_yank_does_not_scan_every_diff_line() -> None:
    view = _VisualYankView()

    diff_selection._yank(view)

    assert view.copied == "line1\nline2\n"
    assert view.visual_mode is False


def test_copy_yank_to_clipboard_posts_error_when_clipboard_copy_fails() -> None:
    class CopyFailingView(_YankView):
        def _copy_to_clipboard(self, text: str) -> None:
            raise RuntimeError("clipboard failed")

    view = CopyFailingView()

    diff_selection._copy_yank_to_clipboard(
        view,
        VisualYank(text="line\n", success_message="Copied 1 line"),
    )

    assert len(view.messages) == 1
    assert view.messages[0].content == "Failed to copy: clipboard failed"
    assert view.messages[0].style == "error"


def test_copy_yank_to_clipboard_reraises_unexpected_success_flash_errors() -> None:
    class SuccessFlashFailingView(_YankView):
        def post_message(self, message: Flash) -> None:
            if message.style == "success":
                raise RuntimeError("flash dispatch failed")
            super().post_message(message)

    view = SuccessFlashFailingView()

    with pytest.raises(RuntimeError, match="flash dispatch failed"):
        diff_selection._copy_yank_to_clipboard(
            view,
            VisualYank(text="line\n", success_message="Copied 1 line"),
        )

    assert view.messages == []
