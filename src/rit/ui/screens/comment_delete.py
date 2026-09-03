from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from rit.state.models import PendingReviewComment, PRComment

__all__ = ("CommentDeleteScreen",)


class CommentDeleteScreen(ModalScreen[bool]):
    """Confirm deletion of a review comment or pending draft."""

    DEFAULT_CSS = """
    CommentDeleteScreen {
        align: center middle;
    }

    #comment-delete-dialog {
        width: 68;
        max-width: 85%;
        height: auto;
        background: $surface;
        border: round $error;
        padding: 1 2;
    }

    #comment-delete-title {
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }

    #comment-delete-meta {
        color: $text-muted;
        margin-bottom: 1;
    }

    #comment-delete-preview {
        max-height: 4;
        margin-bottom: 1;
    }

    #comment-delete-actions {
        height: 3;
        width: 1fr;
        align-horizontal: right;
    }

    #comment-delete-actions Button {
        min-width: 10;
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "confirm", "Delete", show=False),
        Binding("y", "confirm", "Delete", show=False),
        Binding("n", "cancel", "Cancel", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, comment: PRComment | PendingReviewComment) -> None:
        super().__init__()
        self._comment = comment

    def compose(self) -> ComposeResult:
        location = self._comment.path
        if self._comment.is_file_level:
            location = f"{location} (entire file)"
        elif self._comment.anchor_line is not None:
            location = f"{location}:{self._comment.anchor_line}"

        if isinstance(self._comment, PendingReviewComment):
            title = "Delete this draft?"
            meta = f"Pending draft • {location}"
        else:
            author = self._comment.user.login if self._comment.user else "unknown"
            title = "Delete this comment?"
            meta = f"@{author} • {location}"

        with Vertical(id="comment-delete-dialog"):
            yield Static(title, id="comment-delete-title", markup=False)
            yield Static(
                meta,
                id="comment-delete-meta",
                markup=False,
            )
            yield Static(
                _comment_preview(self._comment.body),
                id="comment-delete-preview",
                markup=False,
            )
            with Horizontal(id="comment-delete-actions"):
                yield Button("Cancel", id="comment-delete-cancel")
                yield Button(
                    "Delete",
                    id="comment-delete-confirm",
                    variant="error",
                )

    @on(Button.Pressed, "#comment-delete-confirm")
    def _confirm_from_button(self) -> None:
        self.action_confirm()

    @on(Button.Pressed, "#comment-delete-cancel")
    def _cancel_from_button(self) -> None:
        self.action_cancel()

    def on_mount(self) -> None:
        self.query_one("#comment-delete-confirm", Button).focus()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def _comment_preview(body: str, limit: int = 180) -> str:
    preview = " ".join(body.split())
    if len(preview) <= limit:
        return preview or "(empty comment)"
    return f"{preview[:limit].rstrip()}…"
