from __future__ import annotations

from typing import ClassVar, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from rit.state.models import PendingReviewComment
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.emoji_picker import EMOJI_PICKER_BINDINGS, EmojiPicker

ReviewEvent = Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"]

__all__ = (
    "ReviewEvent",
    "ReviewSubmitScreen",
)


def _review_event_from_option_id(option_id: object) -> ReviewEvent:
    if option_id == "APPROVE":
        return "APPROVE"
    if option_id == "REQUEST_CHANGES":
        return "REQUEST_CHANGES"
    return "COMMENT"


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

    #review-submit-buttons {
        height: 3;
        align-horizontal: right;
    }

    #review-submit-buttons Button {
        min-width: 14;
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        *EMOJI_PICKER_BINDINGS,
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
            yield EmojiPicker(id="review-submit-emoji-options")
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
            with HorizontalGroup(id="review-submit-buttons"):
                yield Button(
                    "Submit review",
                    id="review-submit-confirm",
                    variant="primary",
                )
                yield Button("Cancel", id="review-submit-cancel")

    def on_mount(self) -> None:
        options = self.query_one("#review-submit-actions", OptionList)
        options.action_first()
        body = self.query_one("#review-submit-body", TextArea)
        body.text = self._initial_body
        body.focus()

    @on(TextArea.Changed, "#review-submit-body")
    def _on_body_changed(self, event: TextArea.Changed) -> None:
        self._emoji_picker().refresh_for(event.text_area)

    @on(TextArea.SelectionChanged, "#review-submit-body")
    def _on_body_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._emoji_picker().refresh_for(event.text_area)

    @on(OptionList.OptionSelected, "#review-submit-emoji-options")
    def _on_emoji_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        body = self.query_one("#review-submit-body", TextArea)
        self._emoji_picker().accept(body, event.option_id)

    def action_emoji_next(self) -> None:
        self._emoji_picker().action_cursor_down()

    def action_emoji_previous(self) -> None:
        self._emoji_picker().action_cursor_up()

    def action_emoji_accept(self) -> None:
        body = self.query_one("#review-submit-body", TextArea)
        self._emoji_picker().accept_highlighted(body)

    def action_emoji_hide(self) -> None:
        self._emoji_picker().hide_picker()
        self.query_one("#review-submit-body", TextArea).focus()

    def _emoji_picker(self) -> EmojiPicker:
        return self.query_one("#review-submit-emoji-options", EmojiPicker)

    def action_cursor_down(self) -> None:
        self.query_one("#review-submit-actions", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#review-submit-actions", OptionList).action_cursor_up()

    def _focus_targets(self) -> tuple[Widget, ...]:
        return (
            self.query_one("#review-submit-body", TextArea),
            self.query_one("#review-submit-actions", OptionList),
            self.query_one("#review-submit-confirm", Button),
            self.query_one("#review-submit-cancel", Button),
        )

    def _move_focus(self, offset: int) -> None:
        targets = self._focus_targets()
        focused = self.focused
        try:
            index = targets.index(focused)
        except ValueError:
            index = -1 if offset > 0 else 0
        targets[(index + offset) % len(targets)].focus()

    def action_focus_next(self) -> None:
        self._move_focus(1)

    def action_focus_prev(self) -> None:
        self._move_focus(-1)

    def _pending_comment_meta(self, comment: PendingReviewComment) -> str:
        if comment.is_file_level:
            return f"{comment.path} • entire file"
        return f"{comment.path}:{comment.line} • {comment.anchor_side} side"

    def action_submit(self) -> None:
        options = self.query_one("#review-submit-actions", OptionList)
        highlighted = options.highlighted_option
        option_id = highlighted.id if highlighted is not None else "COMMENT"
        event = _review_event_from_option_id(option_id)
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

    @on(Button.Pressed, "#review-submit-confirm")
    def on_submit_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit()

    @on(Button.Pressed, "#review-submit-cancel")
    def on_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("emoji_"):
            return self.is_mounted and self._emoji_picker().is_open
        if action in {"cursor_down", "cursor_up"} and isinstance(
            self.focused, TextArea
        ):
            return False
        return super().check_action(action, parameters)

    @on(OptionList.OptionSelected, "#review-submit-actions")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.query_one("#review-submit-confirm", Button).focus()
