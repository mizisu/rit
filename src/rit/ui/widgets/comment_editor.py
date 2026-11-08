from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.document._history import EditHistory
from textual.geometry import Region
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Button, OptionList, Static, TextArea
from textual.widgets.text_area import Selection

from rit.ui.widgets.action_buttons import ActionButtons
from rit.ui.widgets.emoji_picker import EMOJI_PICKER_BINDINGS, EmojiPicker

EditorKind = Literal["issue", "inline", "file", "reply"]
SubmitMode = Literal["queue", "post"]


__all__ = (
    "EditorKind",
    "InlineCommentEditor",
    "SubmitMode",
)


@dataclass
class CommentEditorBuffer:
    """Retain unfinished input independently of virtualized widgets."""

    text: str = ""
    selection: Selection | None = None
    history: EditHistory | None = None
    submitting: bool = False
    submit_mode: SubmitMode = "queue"
    error: str = ""


class _CommentTextArea(TextArea):
    """Avoid styling removed editors retained in Textual's hit-test cache."""

    def render_lines(self, crop: Region) -> list[Strip]:
        if not self.is_attached:
            return []
        return super().render_lines(crop)


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

    InlineCommentEditor .comment-editor-actions {
        height: auto;
        align-horizontal: right;
    }

    InlineCommentEditor .comment-editor-actions Button {
        min-width: 12;
        margin-left: 1;
    }

    InlineCommentEditor.-compact {
        min-width: 0;
        border: solid #363a4f;
        padding: 1;
        margin: 0;
        background: #1e2030;
    }
    InlineCommentEditor.-compact:focus-within {
        border: solid #8aadf4;
    }
    InlineCommentEditor.-compact .comment-editor-title {
        margin: 0 0 1 0;
        color: #8aadf4;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    InlineCommentEditor.-compact .comment-editor-context {
        display: none;
    }
    InlineCommentEditor.-compact .comment-editor-body {
        min-height: 3;
        max-height: 6;
        margin: 0 0 1 0;
        border: none;
        padding: 0;
        background: #1e2030;
    }
    InlineCommentEditor.-compact .comment-editor-actions {
        width: 100%;
        align-horizontal: left;
    }
    InlineCommentEditor.-compact .comment-editor-actions Button,
    InlineCommentEditor.-compact .comment-editor-actions Button.-primary {
        height: auto;
        text-wrap: wrap;
        text-style: none;
        min-width: 0;
        max-width: 100%;
        border: none;
        padding: 0;
        margin: 0 0 0 1;
        background: #24273a;
        color: #cad3f5;
    }
    InlineCommentEditor.-compact .comment-editor-actions Button:first-child,
    InlineCommentEditor.-compact .comment-editor-actions.-stacked Button {
        margin-left: 0;
    }
    InlineCommentEditor.-compact .comment-editor-actions Button:hover {
        background: #363a4f;
    }
    InlineCommentEditor.-compact .comment-editor-actions Button:focus {
        background: #8aadf4;
        color: #1e2030;
        text-style: bold;
    }
    InlineCommentEditor.-compact .comment-editor-actions Button:disabled {
        background: #24273a;
        color: #6e738d;
    }
    InlineCommentEditor .comment-editor-status {
        height: auto;
        color: $text-muted;
    }
    InlineCommentEditor .comment-editor-status.-error {
        color: $error;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        *EMOJI_PICKER_BINDINGS,
        Binding("ctrl+s,ctrl+enter", "submit('queue')", "Save draft", show=False),
        Binding("tab", "focus_next", "Next action", show=False),
        Binding("shift+tab", "focus_previous", "Previous action", show=False),
        Binding("escape", "leave", "Back", show=False),
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
    class Suspended(Message):
        kind: EditorKind

    def __init__(
        self,
        *,
        kind: EditorKind,
        title: str,
        placeholder: str,
        initial_text: str = "",
        context: str = "",
        update_existing: bool = False,
        editing_draft: bool = False,
        post_disabled_reason: str = "",
        buffer: CommentEditorBuffer | None = None,
        compact: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="-hidden")
        self._kind: EditorKind = kind
        self._title = title
        self._placeholder = placeholder
        self._selection_context = context
        self._update_existing = update_existing
        self._editing_draft = editing_draft
        self._post_disabled_reason = post_disabled_reason
        self._pending_focus = False
        self._compact = compact
        self.buffer = buffer or CommentEditorBuffer(text=initial_text)
        self.set_class(compact, "-compact")

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="comment-editor-title")
        if self._selection_context:
            yield Static(
                self._selection_context,
                classes="comment-editor-context",
                markup=False,
            )
        yield _CommentTextArea(
            id="comment-editor-body",
            classes="comment-editor-body",
            soft_wrap=True,
            show_line_numbers=False,
            placeholder=self._placeholder,
        )
        yield EmojiPicker(id="comment-editor-emoji-options")
        with ActionButtons(classes="comment-editor-actions"):
            if self._update_existing:
                yield Button(
                    "Update  [dim]Ctrl+S[/]",
                    id="comment-editor-submit",
                    variant="primary",
                )
            elif self._kind in {"inline", "file", "reply"}:
                action = (
                    ("Save changes" if self._editing_draft else "Save draft")
                    if self._compact
                    else "Add to review"
                )
                yield Button(
                    f"{action}  [dim]Ctrl+S[/]",
                    id="comment-editor-queue",
                    variant="primary",
                )
                if not self._post_disabled_reason:
                    yield Button(
                        "Post now",
                        id="comment-editor-post",
                        tooltip="Publish this comment immediately",
                    )
            else:
                yield Button(
                    "Submit  [dim]Ctrl+S[/]",
                    id="comment-editor-submit",
                    variant="primary",
                )
            yield Button(
                "Discard" if self._compact else "Cancel  [dim]Esc[/]",
                id="comment-editor-cancel",
            )
        if self._compact:
            yield Static(
                self._idle_hint,
                classes="comment-editor-status",
                markup=False,
            )

    def on_mount(self) -> None:
        body = self.query_one("#comment-editor-body", TextArea)
        body.text = self.buffer.text
        if self.buffer.history is not None:
            body.history = self.buffer.history
        if self.buffer.selection is not None:
            body.selection = self.buffer.selection
        else:
            body.move_cursor(body.document.end)
        self.set_submitting(self.buffer.submitting, error=self.buffer.error)
        if self._pending_focus:
            self._pending_focus = False
            self._focus_body()

    def _focus_body(self) -> None:
        self.remove_class("-hidden")
        self.query_one("#comment-editor-body", TextArea).focus()
        if self._compact:
            self.call_after_refresh(lambda: self.scroll_visible(animate=False))

    def capture(self) -> None:
        if self.is_mounted:
            body = self.query_one("#comment-editor-body", TextArea)
            self.buffer.text = body.text
            self.buffer.selection = body.selection
            self.buffer.history = body.history

    def set_submitting(self, submitting: bool, *, error: str = "") -> None:
        self.buffer.submitting = submitting
        self.buffer.error = error
        if not self.is_mounted:
            return
        body = self.query_one(TextArea)
        if submitting and self.has_focus_within:
            self.screen.set_focus(body, scroll_visible=False)
        body.read_only = submitting
        self._refresh_controls()

    @property
    def _idle_hint(self) -> str:
        hint = "Esc close"
        if self._post_disabled_reason:
            hint += f"\n{self._post_disabled_reason}"
        return hint

    def _refresh_controls(self) -> None:
        buffer = self.buffer
        for button in self.query(Button):
            is_submit = button.id != "comment-editor-cancel"
            button.disabled = buffer.submitting or (
                is_submit and not buffer.text.strip()
            )
        if self._compact:
            busy = "Posting…" if buffer.submit_mode == "post" else "Saving…"
            status = self.query_one(".comment-editor-status", Static)
            status.update(
                buffer.error or (busy if buffer.submitting else self._idle_hint)
            )
            status.set_class(bool(buffer.error), "-error")

    def open(self, initial_text: str | None = None) -> None:
        if initial_text is not None:
            self.buffer.text = initial_text
            self.buffer.selection = None
            if self.is_mounted:
                self.query_one(TextArea).text = initial_text
        if not self.is_mounted:
            self._pending_focus = True
            self.remove_class("-hidden")
            return
        self._focus_body()

    def close(self) -> None:
        if self.is_mounted:
            body = self.query_one("#comment-editor-body", TextArea)
            body.text = ""
        self.add_class("-hidden")
        self.screen.set_focus(None)

    @property
    def is_open(self) -> bool:
        return not self.has_class("-hidden")

    def action_submit(self, mode: SubmitMode = "queue") -> None:
        if self.buffer.submitting:
            return
        body = self.query_one("#comment-editor-body", TextArea).text.strip()
        if not body:
            self.notify("Comment cannot be empty", severity="warning")
            return
        self.post_message(self.Submitted(self._kind, body, mode))

    @on(TextArea.Changed, "#comment-editor-body")
    def _on_body_changed(self, event: TextArea.Changed) -> None:
        if self.buffer.text != event.text_area.text and not self.buffer.submitting:
            self.buffer.error = ""
        self.buffer.text = event.text_area.text
        self._refresh_controls()
        self._emoji_picker().refresh_for(event.text_area)

    @on(TextArea.SelectionChanged, "#comment-editor-body")
    def _on_body_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self.buffer.selection = event.text_area.selection
        self._emoji_picker().refresh_for(event.text_area)

    @on(OptionList.OptionSelected, "#comment-editor-emoji-options")
    def _on_emoji_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        body = self.query_one("#comment-editor-body", TextArea)
        self._emoji_picker().accept(body, event.option_id)

    @on(Button.Pressed, "#comment-editor-post")
    def _on_post_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit("post")

    @on(Button.Pressed, "#comment-editor-queue")
    @on(Button.Pressed, "#comment-editor-submit")
    def _on_submit_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit("queue")

    @on(Button.Pressed, "#comment-editor-cancel")
    def _on_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

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

    def _focus_targets(self) -> tuple[Widget, ...]:
        return (
            self.query_one("#comment-editor-body", TextArea),
            *(button for button in self.query(Button) if not button.disabled),
        )

    def _move_focus(self, offset: int) -> None:
        targets = self._focus_targets()
        focused = self.screen.focused
        try:
            index = targets.index(focused)
        except ValueError:
            index = -1 if offset > 0 else 0
        targets[(index + offset) % len(targets)].focus()

    def action_focus_next(self) -> None:
        self._move_focus(1)

    def action_focus_previous(self) -> None:
        self._move_focus(-1)

    def action_leave(self) -> None:
        if self.buffer.submitting:
            return
        if self._compact:
            self.capture()
            self.post_message(self.Suspended(self._kind))
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        if not self.buffer.submitting:
            self.post_message(self.Cancelled(self._kind))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("emoji_"):
            return self.is_mounted and self._emoji_picker().is_open
        return super().check_action(action, parameters)
