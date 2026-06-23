import pytest

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
