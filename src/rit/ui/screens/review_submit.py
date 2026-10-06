from __future__ import annotations

from typing import ClassVar, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from rit.state.models import PendingReviewComment
from rit.state.store import PRStore
from rit.ui.widgets.action_buttons import ActionButtons
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
        store: PRStore | None = None,
    ) -> None:
        super().__init__()
        self._pending_comments = pending_comments or []
        self._pending_comments_count = max(
            pending_comments_count,
            len(self._pending_comments),
        )
        self._store = store
        self._initial_body = store.state.pending_review.body if store else initial_body
        self._save_timer: Timer | None = None
        self._saving_to_close = False

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
        height: auto;
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
        Binding("ctrl+s", "save_draft", "Save draft", show=False),
        Binding("ctrl+enter", "submit", "Submit", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="review-submit-dialog"):
            yield Static("Submit review", id="review-submit-title")
            yield TextArea(
                self._initial_body,
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
                        f"Pending comments ({self._pending_comments_count})",
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
            with ActionButtons(id="review-submit-buttons"):
                yield Button(
                    "Submit review  [dim]Ctrl+Enter[/]",
                    id="review-submit-confirm",
                    variant="primary",
                )
                yield Button("Cancel  [dim]Esc[/]", id="review-submit-cancel")

    def on_mount(self) -> None:
        options = self.query_one("#review-submit-actions", OptionList)
        options.action_first()
        self.query_one("#review-submit-body", TextArea).focus()

    @on(TextArea.Changed, "#review-submit-body")
    def _on_body_changed(self, event: TextArea.Changed) -> None:
        self._emoji_picker().refresh_for(event.text_area)
        if self._store is None or self._saving_to_close:
            return
        if not self._store.set_pending_review_body(event.text_area.text):
            return
        self._stop_save_timer()
        self._save_timer = self.set_timer(0.8, self.action_save_draft)

    def _stop_save_timer(self) -> None:
        if self._save_timer is not None:
            self._save_timer.stop()
            self._save_timer = None

    def on_unmount(self) -> None:
        self._stop_save_timer()

    def action_save_draft(self) -> None:
        if self._saving_to_close or self._store is None:
            return
        self._stop_save_timer()
        self._store.set_pending_review_body(
            self.query_one("#review-submit-body", TextArea).text
        )
        self.run_worker(
            self._save_draft(), group="review-autosave", exit_on_error=False
        )

    async def _save_draft(self) -> bool:
        if self._store is None:
            return True
        try:
            await self._store.save_pending_review_body()
        except (RuntimeError, ValueError, OSError) as error:
            self.notify(
                f"Failed to save review draft: {error}",
                severity="error",
                markup=False,
            )
            return False
        return True

    def _finish(self, result: tuple[ReviewEvent, str] | None) -> None:
        if self._saving_to_close:
            return
        self._stop_save_timer()
        self._saving_to_close = True
        body = self.query_one("#review-submit-body", TextArea)
        if self._store is not None:
            self._store.set_pending_review_body(body.text)
        body.read_only = True
        self.query(Button).set(disabled=True)
        self.run_worker(
            self._save_and_dismiss(result), group="review-close", exit_on_error=False
        )

    async def _save_and_dismiss(self, result: tuple[ReviewEvent, str] | None) -> None:
        if result is None:
            await self._save_draft()
        else:
            for worker in list(self.workers):
                if worker.node is self and worker.group == "review-autosave":
                    await worker.wait()
        self.dismiss(result)

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
        if comment.is_reply:
            return f"{comment.path} • reply to #{comment.reply_to_id}"
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
        self._finish((event, body))

    def action_cancel(self) -> None:
        self._finish(None)

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
