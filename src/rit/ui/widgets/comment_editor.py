from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static, TextArea

from rit.ui.widgets.emoji_picker import EMOJI_PICKER_BINDINGS, EmojiPicker

EditorKind = Literal["issue", "inline", "file"]
SubmitMode = Literal["queue", "post"]


__all__ = (
    "EditorKind",
    "InlineCommentEditor",
    "SubmitMode",
)


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

    InlineCommentEditor .comment-editor-context {
        color: $text-muted;
        margin-bottom: 1;
    }

    InlineCommentEditor .comment-editor-body {
        height: auto;
        min-height: 5;
        max-height: 12;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        *EMOJI_PICKER_BINDINGS,
        Binding("ctrl+s,ctrl+enter", "submit('queue')", "Save draft", show=False),
        Binding(
            "ctrl+shift+s,ctrl+shift+enter",
            "submit('post')",
            "Post now",
            show=False,
        ),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    @dataclass
    class Submitted(Message):
        kind: EditorKind
        body: str
        mode: SubmitMode = "queue"

    @dataclass
    class Cancelled(Message):
        kind: EditorKind

    @dataclass
    class LayoutHeightChanged(Message):
        editor: InlineCommentEditor
        height: int

        @property
        def control(self) -> InlineCommentEditor:
            return self.editor

    def __init__(
        self,
        *,
        kind: EditorKind,
        title: str,
        placeholder: str,
        initial_text: str = "",
        context: str = "",
        update_existing: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="-hidden")
        self._kind: EditorKind = kind
        self._title = title
        self._placeholder = placeholder
        self._initial_text = initial_text
        self._selection_context = context
        self._update_existing = update_existing
        self._pending_focus = False
        self._reported_layout_height = 0

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="comment-editor-title")
        if self._selection_context:
            yield Static(
                self._selection_context,
                classes="comment-editor-context",
                markup=False,
            )
        yield TextArea(
            id="comment-editor-body",
            classes="comment-editor-body",
            soft_wrap=True,
            show_line_numbers=False,
            placeholder=self._placeholder,
        )
        yield EmojiPicker(id="comment-editor-emoji-options")
        if self._update_existing:
            hint = "Ctrl+S update • Esc cancel"
        elif self._kind in {"inline", "file"}:
            hint = "Ctrl+S pending • Ctrl+Shift+S post now • Esc cancel"
        else:
            hint = "Ctrl+S submit • Esc cancel"
        yield Static(hint)

    def on_mount(self) -> None:
        if self._pending_focus:
            self._pending_focus = False
            self._focus_body()

    def on_resize(self, event: events.Resize) -> None:
        if not self.is_open:
            return
        layout_height = event.size.height + self.styles.margin.height
        if layout_height == self._reported_layout_height:
            return
        self._reported_layout_height = layout_height
        self.post_message(self.LayoutHeightChanged(self, layout_height))

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

    def action_submit(self, mode: SubmitMode = "queue") -> None:
        body = self.query_one("#comment-editor-body", TextArea).text.strip()
        if not body:
            self.notify("Comment cannot be empty", severity="warning")
            return
        self.post_message(self.Submitted(self._kind, body, mode))

    @on(TextArea.Changed, "#comment-editor-body")
    def _on_body_changed(self, event: TextArea.Changed) -> None:
        self._emoji_picker().refresh_for(event.text_area)

    @on(TextArea.SelectionChanged, "#comment-editor-body")
    def _on_body_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._emoji_picker().refresh_for(event.text_area)

    @on(OptionList.OptionSelected, "#comment-editor-emoji-options")
    def _on_emoji_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        body = self.query_one("#comment-editor-body", TextArea)
        self._emoji_picker().accept(body, event.option_id)

    def action_emoji_next(self) -> None:
        self._emoji_picker().action_cursor_down()

    def action_emoji_previous(self) -> None:
        self._emoji_picker().action_cursor_up()

    def action_emoji_accept(self) -> None:
        body = self.query_one("#comment-editor-body", TextArea)
        self._emoji_picker().accept_highlighted(body)

    def action_emoji_hide(self) -> None:
        self._emoji_picker().hide_picker()
        self.query_one("#comment-editor-body", TextArea).focus()

    def _emoji_picker(self) -> EmojiPicker:
        return self.query_one("#comment-editor-emoji-options", EmojiPicker)

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled(self._kind))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("emoji_"):
            return self.is_mounted and self._emoji_picker().is_open
        return super().check_action(action, parameters)
