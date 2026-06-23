from __future__ import annotations

from typing import Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

__all__ = ("BranchPickerScreen",)


class BranchPickerScreen(ModalScreen[Literal["head", "base"] | None]):
    DEFAULT_CSS = """
    BranchPickerScreen {
        align: center middle;
    }

    #branch-picker-dialog {
        width: 60;
        max-width: 80%;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    #branch-picker-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #branch-picker-help {
        margin-top: 1;
    }

    #branch-options {
        height: auto;
        max-height: 6;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Next", show=False),
        Binding("k", "cursor_up", "Prev", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, *, head: str | None, base: str | None) -> None:
        super().__init__()
        self._head = head
        self._base = base

    def compose(self) -> ComposeResult:
        with Vertical(id="branch-picker-dialog"):
            yield Static("Copy branch", id="branch-picker-title")
            yield OptionList(
                Option(
                    f"Head: {self._head or '(unavailable)'}",
                    id="head",
                    disabled=not bool(self._head),
                ),
                Option(
                    f"Base: {self._base or '(unavailable)'}",
                    id="base",
                    disabled=not bool(self._base),
                ),
                id="branch-options",
            )
            yield Static(
                "↑/↓ or j/k to choose • Enter to copy • Esc to cancel",
                id="branch-picker-help",
            )

    def on_mount(self) -> None:
        options = self.query_one("#branch-options", OptionList)
        options.action_first()
        options.focus()

    def action_cursor_down(self) -> None:
        self.query_one("#branch-options", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#branch-options", OptionList).action_cursor_up()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#branch-options")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option_id
        if option_id == "head":
            self.dismiss("head")
        elif option_id == "base":
            self.dismiss("base")
