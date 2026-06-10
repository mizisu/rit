from __future__ import annotations

from typing import Literal, cast

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from rit.state.models import PendingReviewComment
from rit.ui.widgets.comment_card import CommentCard

ReviewEvent = Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"]


class ReviewSubmitScreen(ModalScreen[tuple[ReviewEvent, str] | None]):
    """Modal for selecting and submitting a top-level review."""

    def __init__(
        self,
        pending_comments_count: int = 0,
        pending_comments: list[PendingReviewComment] | None = None,
        initial_body: str = "",
    ) -> None:
        super().__init__()
        self._pending_comments = pending_comments or []
        self._pending_comments_count = max(
            pending_comments_count,
            len(self._pending_comments),
        )
        self._initial_body = initial_body

    DEFAULT_CSS = """
    ReviewSubmitScreen {
        align: center middle;
    }

    #review-submit-dialog {
        width: 88;
        max-width: 96%;
        max-height: 90%;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    #review-submit-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #review-submit-body {
        height: 8;
        min-height: 6;
        max-height: 14;
        margin-bottom: 1;
    }

    #review-submit-actions {
        height: 5;
        min-height: 5;
        margin-bottom: 1;
    }

    #review-submit-pending {
        height: auto;
        margin-bottom: 1;
    }

    #review-submit-pending-list {
        height: 14;
        min-height: 8;
        max-height: 18;
        border: round $panel;
        padding: 0 1;
        background: $panel;
    }

    .review-submit-pending-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .review-submit-pending-empty {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Next", show=False),
        Binding("k", "cursor_up", "Prev", show=False),
        Binding("tab", "focus_next", "Next Field", show=False),
        Binding("shift+tab", "focus_prev", "Prev Field", show=False),
        Binding("ctrl+s", "submit", "Submit", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="review-submit-dialog"):
            yield Static("Submit review", id="review-submit-title")
            yield TextArea(
                id="review-submit-body",
                soft_wrap=True,
                show_line_numbers=False,
                placeholder="Write a review summary...",
            )
            yield OptionList(
                Option("Comment", id="COMMENT"),
                Option("Approve", id="APPROVE"),
                Option("Request changes", id="REQUEST_CHANGES"),
                id="review-submit-actions",
            )
            if self._pending_comments_count:
                with Vertical(id="review-submit-pending"):
                    yield Static(
                        f"Pending inline comments ({self._pending_comments_count})",
                        classes="review-submit-pending-title",
                    )
                    with VerticalScroll(id="review-submit-pending-list"):
                        if self._pending_comments:
                            for index, comment in enumerate(self._pending_comments):
                                yield CommentCard(
                                    self._pending_comment_meta(comment),
                                    comment.body.strip(),
                                    id=f"review-submit-pending-item-{index}",
                                    classes="pending-draft review-submit-pending-item",
                                )
                        else:
                            yield Static(
                                f"{self._pending_comments_count} pending comments ready to submit",
                                classes="review-submit-pending-empty",
                            )
            yield Static(
                "Write summary • Tab to action • Enter/Ctrl+S to submit • Esc to cancel"
            )

    def on_mount(self) -> None:
        options = self.query_one("#review-submit-actions", OptionList)
        options.action_first()
        body = self.query_one("#review-submit-body", TextArea)
        body.text = self._initial_body
        body.focus()

    def action_cursor_down(self) -> None:
        self.query_one("#review-submit-actions", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#review-submit-actions", OptionList).action_cursor_up()

    def action_focus_next(self) -> None:
        options = self.query_one("#review-submit-actions", OptionList)
        body = self.query_one("#review-submit-body", TextArea)
        if body.has_focus:
            options.focus()
        else:
            body.focus()

    def action_focus_prev(self) -> None:
        self.action_focus_next()

    def _pending_comment_meta(self, comment: PendingReviewComment) -> str:
        return f"{comment.path}:{comment.line} • {comment.anchor_side} side"

    def action_submit(self) -> None:
        options = self.query_one("#review-submit-actions", OptionList)
        highlighted = options.highlighted_option
        option_id = highlighted.id if highlighted is not None else "COMMENT"
        event = cast(ReviewEvent, option_id)
        body = self.query_one("#review-submit-body", TextArea).text.strip()
        if event == "REQUEST_CHANGES" and not body:
            self.notify("Review body cannot be empty", severity="warning")
            return
        if event == "COMMENT" and not body and self._pending_comments_count == 0:
            self.notify("Review body cannot be empty", severity="warning")
            return
        self.dismiss((event, body))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"cursor_down", "cursor_up"} and isinstance(
            self.focused, TextArea
        ):
            return False
        return super().check_action(action, parameters)

    @on(OptionList.OptionSelected, "#review-submit-actions")
    def on_option_selected(self, _event: OptionList.OptionSelected) -> None:
        self.action_submit()
