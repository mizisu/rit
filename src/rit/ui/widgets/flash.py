from __future__ import annotations

from typing import Literal

from textual.content import Content
from textual.reactive import var
from textual.timer import Timer
from textual.widgets import Static

__all__ = ("FlashWidget",)


class FlashWidget(Static):
    """Temporary notification widget that auto-hides after a duration."""

    DEFAULT_CSS = """
    FlashWidget {
        dock: top;
        width: 100%;
        height: auto;
        padding: 0 1;
        display: none;
        background: $surface;
        border-bottom: solid $primary;
    }

    FlashWidget.-visible {
        display: block;
    }

    FlashWidget.-default {
        background: $surface;
        border-bottom: solid $primary;
        color: $text;
    }

    FlashWidget.-success {
        background: $success 20%;
        border-bottom: solid $success;
        color: $text-success;
    }

    FlashWidget.-warning {
        background: $warning 20%;
        border-bottom: solid $warning;
        color: $text-warning;
    }

    FlashWidget.-error {
        background: $error 20%;
        border-bottom: solid $error;
        color: $text-error;
    }
    """

    # Timer for auto-hide
    _flash_timer: var[Timer | None] = var(None)

    # Current style
    _style: var[str] = var("default")

    def __init__(
        self,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__("", id=id, classes=classes)

    def flash(
        self,
        content: str | Content,
        *,
        duration: float = 3.0,
        style: Literal["default", "success", "warning", "error"] = "default",
    ) -> None:
        if self._flash_timer is not None:
            self._flash_timer.stop()
            self._flash_timer = None

        # Update content
        self.update(content)

        # Update style classes
        self.remove_class("-default", "-success", "-warning", "-error")
        self.add_class(f"-{style}")
        self._style = style

        # Show the widget
        self.add_class("-visible")

        # Set auto-hide timer
        if duration is not None and duration > 0:
            self._flash_timer = self.set_timer(duration, self.hide)

    def hide(self) -> None:
        self.remove_class("-visible")

        if self._flash_timer is not None:
            self._flash_timer.stop()
            self._flash_timer = None

    @property
    def is_visible(self) -> bool:
        return self.has_class("-visible")

    def success(self, content: str | Content, duration: float = 3.0) -> None:
        self.flash(content, duration=duration, style="success")

    def warning(self, content: str | Content, duration: float = 3.0) -> None:
        self.flash(content, duration=duration, style="warning")

    def error(self, content: str | Content, duration: float = 5.0) -> None:
        self.flash(content, duration=duration, style="error")
