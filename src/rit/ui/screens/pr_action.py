from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from rit.state.models import PR
from rit.state.pr_overview import PRAction
from rit.ui.widgets.action_buttons import ActionButtons


class PRActionScreen(ModalScreen[bool]):
    """Confirm exactly the PR action selected in the sidebar."""

    AUTO_FOCUS = "#pr-action-cancel"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=False)
    ]
    DEFAULT_CSS = """
    PRActionScreen { align: center middle; }
    PRActionScreen VerticalScroll {
        width: 62; max-width: 95%; height: auto; max-height: 95%;
        background: $surface; border: round $primary; padding: 1 2;
    }
    PRActionScreen .title { text-style: bold; }
    PRActionScreen ActionButtons { margin-top: 1; align-horizontal: right; }
    PRActionScreen Button { margin-left: 1; }
    """

    def __init__(self, pr: PR, action: PRAction) -> None:
        super().__init__()
        self.pr = pr
        self.pr_action = action

    def compose(self) -> ComposeResult:
        pr, action = self.pr, self.pr_action
        close = action is PRAction.CLOSE
        with VerticalScroll():
            verb = "Close" if close else "Merge"
            yield Static(f"{verb} PR #{pr.number}?", classes="title")
            yield Static(pr.title, markup=False)
            if close:
                yield Static("Changes will not be merged. The branch will be kept.")
            else:
                yield Static(
                    f"{pr.head_ref} → {pr.base_ref}\nCommit: {pr.head_sha[:12]}",
                    markup=False,
                )
            with ActionButtons():
                yield Button("Cancel", id="pr-action-cancel")
                yield Button(
                    action.label,
                    id="pr-action-confirm",
                    variant="error" if close else "primary",
                )

    @on(Button.Pressed)
    def _answer(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "pr-action-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)
