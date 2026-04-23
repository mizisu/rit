from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EditorKind = Literal["issue", "inline"]

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, TextArea


class InlineCommentEditor(Vertical):
    """Inline comment editor shared by PR and file views."""

    DEFAULT_CSS = """
    InlineCommentEditor {
        height: auto;
        border: solid $primary;
        padding: 1;
        margin: 1 0;
        background: $surface;
    }

    InlineCommentEditor.-hidden {
        display: none;
    }

    InlineCommentEditor .comment-editor-title {
        text-style: bold;
        margin-bottom: 1;
    }

    InlineCommentEditor .comment-editor-body {
        height: 6;
        min-height: 4;
        max-height: 12;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+s", "submit", "Submit", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    @dataclass
    class Submitted(Message):
        kind: EditorKind
        body: str

    @dataclass
    class Cancelled(Message):
        kind: EditorKind

    def __init__(
        self,
        *,
        kind: EditorKind,
        title: str,
        placeholder: str,
        initial_text: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="-hidden")
        self._kind = kind
        self._title = title
        self._placeholder = placeholder
        self._initial_text = initial_text
        self._pending_focus = False

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="comment-editor-title")
        yield TextArea(
            id="comment-editor-body",
            classes="comment-editor-body",
            soft_wrap=True,
            show_line_numbers=False,
            placeholder=self._placeholder,
        )
        yield Static("Ctrl+S to submit • Esc to cancel")

    def on_mount(self) -> None:
        if self._pending_focus:
            self._pending_focus = False
            self._focus_body()

    def _focus_body(self) -> None:
        body = self.query_one("#comment-editor-body", TextArea)
        body.text = self._initial_text
        self.remove_class("-hidden")
        body.focus()

    def open(self, initial_text: str | None = None) -> None:
        if initial_text is not None:
            self._initial_text = initial_text
        if not self.is_mounted:
            self._pending_focus = True
            self.remove_class("-hidden")
            return
        self._focus_body()

    def close(self) -> None:
        if self.is_mounted:
            body = self.query_one("#comment-editor-body", TextArea)
            body.text = ""
        self._initial_text = ""
        self.add_class("-hidden")
        self.screen.set_focus(None)

    @property
    def is_open(self) -> bool:
        return not self.has_class("-hidden")

    def action_submit(self) -> None:
        body = self.query_one("#comment-editor-body", TextArea).text.strip()
        if not body:
            self.notify("Comment cannot be empty", severity="warning")
            return
        self.post_message(self.Submitted(self._kind, body))

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled(self._kind))
