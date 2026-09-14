from __future__ import annotations

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.message import Message
from textual.widgets import Button, Static


class BranchInfo(Horizontal):
    """PR comparison branches with a compact copy action."""

    DEFAULT_CSS = """
    BranchInfo {
        height: 1;
        width: 100%;
    }

    BranchInfo .branch-info {
        color: #8aadf4;
        width: auto;
        height: 1;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }

    BranchInfo Button.branch-copy,
    BranchInfo Button.branch-copy.-active {
        width: 3;
        min-width: 3;
        height: 1;
        margin-left: 1;
        border: none;
        padding: 0;
        background: transparent;
        color: $text-muted;
        content-align: center middle;
    }

    BranchInfo Button.branch-copy:hover {
        border: none;
        background: #363a4f;
        color: $text;
    }

    BranchInfo Button.branch-copy:focus {
        background: $primary;
        color: $background;
    }
    """

    class CopyRequested(Message):
        """Request the shared branch-copy picker."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._branch_label = Static(
            "", classes="branch-info", id="branch-info", markup=False
        )
        self._copy_button = Button(
            "\U000f018f",
            id="copy-branch",
            classes="branch-copy",
            name="Copy branch",
            tooltip="Copy branch",
            compact=True,
            flat=True,
        )
        self._branches: tuple[str, str] | None = None
        self.update_branches("", "")

    def compose(self) -> ComposeResult:
        yield self._branch_label
        yield self._copy_button

    def on_resize(self, _event: events.Resize) -> None:
        self._branch_label.styles.max_width = max(0, self.content_size.width - 4)

    def update_branches(self, base: str, head: str) -> None:
        """Refresh the comparison without interpreting branch names as markup."""
        branches = (base, head)
        if branches == self._branches:
            return
        self._branches = branches
        self.display = bool(base or head)
        self._copy_button.disabled = not self.display
        base_label = base or "(unavailable)"
        head_label = head or "(unavailable)"
        self._branch_label.update(
            Content.assemble((base_label, "#8aadf4"), " ← ", (head_label, "#8aadf4"))
        )
        self._branch_label.tooltip = f"Base: {base_label}\nHead: {head_label}"

    @on(Button.Pressed)
    def _request_copy(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.CopyRequested())
