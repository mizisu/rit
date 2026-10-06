from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget

from rit.ui.widgets.comment_editor import InlineCommentEditor


class CommentSubmitScreen(ModalScreen[None]):
    """Compose a comment without changing the underlying discussion layout."""

    DEFAULT_CSS = """
    CommentSubmitScreen {
        align: center middle;
    }

    #comment-submit-dialog {
        width: 88;
        max-width: 96%;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    CommentSubmitScreen InlineCommentEditor {
        border: none;
        margin: 0;
        padding: 0;
    }
    """

    def __init__(self, editor: InlineCommentEditor, *, owner: Widget) -> None:
        super().__init__()
        self.editor = editor
        self._owner = owner

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="comment-submit-dialog"):
            yield self.editor

    def on_mount(self) -> None:
        self.editor.open()

    @on(InlineCommentEditor.Submitted)
    def _on_submitted(self, event: InlineCommentEditor.Submitted) -> None:
        event.stop()
        self._owner.post_message(
            InlineCommentEditor.Submitted(event.kind, event.body, event.mode)
        )

    @on(InlineCommentEditor.Cancelled)
    def _on_cancelled(self, event: InlineCommentEditor.Cancelled) -> None:
        event.stop()
        self._owner.post_message(InlineCommentEditor.Cancelled(event.kind))
