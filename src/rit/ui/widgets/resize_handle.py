from __future__ import annotations

from dataclasses import dataclass

from textual.events import MouseDown, MouseMove, MouseUp
from textual.message import Message
from textual.reactive import var
from textual.widget import Widget


class ResizeHandle(Widget):

    DEFAULT_CSS = """
    ResizeHandle {
        width: 1;
        height: 100%;
        background: $surface;
    }
    ResizeHandle:hover {
        background: $primary 40%;
    }
    ResizeHandle.-dragging {
        background: $primary;
    }
    """

    mouse_over = var(False)

    @dataclass
    class Drag(Message):

        delta_x: int

    @dataclass
    class DragEnd(Message):

        pass

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.dragging = False

    def on_mouse_down(self, event: MouseDown) -> None:
        if self.disabled or self.display == "none":
            return
        self.dragging = True
        self.add_class("-dragging")
        self.capture_mouse()

    def on_mouse_up(self, event: MouseUp) -> None:
        if self.dragging:
            self.dragging = False
            self.remove_class("-dragging")
            self.release_mouse()
            self.post_message(self.DragEnd())

    def on_mouse_move(self, event: MouseMove) -> None:
        if self.dragging:
            self.post_message(self.Drag(event.delta_x))

    def render(self) -> str:
        return ""
