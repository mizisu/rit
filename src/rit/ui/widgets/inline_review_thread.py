from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from textual import events
from textual.message import Message
from textual.widget import Widget

from rit.state.models import PendingReviewComment, PRComment, ReviewThread
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.review_thread_card import ReviewThreadItem


@dataclass
class InlineDiscussionState:
    """Keep disclosure state when a diff window is remounted."""

    thread_collapsed: dict[int, bool] = field(default_factory=dict)
    collapsed_drafts: dict[int, PendingReviewComment] = field(default_factory=dict)
    history_roots: set[int] = field(default_factory=set)


class InlineReviewThread(ReviewThreadItem):
    """Review card with pending replies and a shared inline composer slot."""

    EXPANDED_REPLY_LIMIT: ClassVar[int] = 5
    RECENT_REPLY_COUNT: ClassVar[int] = 2

    DEFAULT_CSS = """
    InlineReviewThread.--inline CommentCard.earlier-replies {
        background: transparent;
        border: none;
        border-left: blank;
        padding: 0 1;
        margin: 0;
    }
    InlineReviewThread.--inline .earlier-replies .comment-header {
        color: $text-muted;
        margin: 0;
    }
    InlineReviewThread.--inline CommentCard.earlier-replies.--cursor-line {
        background: #363a4f;
        border-left: solid #8aadf4;
    }
    InlineReviewThread.--inline .earlier-replies.--cursor-line .comment-header {
        color: #8aadf4;
        text-style: bold;
    }
    """

    @dataclass
    class DisclosureChanged(Message):
        thread: InlineReviewThread
        target: Widget

    def __init__(
        self,
        thread: ReviewThread,
        drafts: list[PendingReviewComment],
        state: InlineDiscussionState,
        *,
        title: str,
    ) -> None:
        self.root_id = thread.root_comment_id
        self.discussion_state = state
        self._open_title = title.removeprefix("✓ Resolved: ")
        self._resolved_title = f"✓ Resolved: {self._open_title}"
        super().__init__(
            title=title,
            path=thread.path,
            line=thread.anchor_line,
            comments=thread.comments,
            is_resolved=thread.is_resolved,
            show_diff_hunk=False,
            show_path_header=False,
            collapsed=state.thread_collapsed.get(self.root_id, thread.is_resolved),
            classes="--thread --inline" + (" --resolved" if thread.is_resolved else ""),
            id=f"inline-thread-{self.root_id}",
        )
        self._submitted = [
            (self._review_thread_card.comment_at(index), card)
            for index in range(len(thread.comments))
            if (card := self._review_thread_card.comment_card_at(index)) is not None
        ]
        self._draft_cards = [
            (
                draft,
                CommentCard(
                    "[bold]you[/] [dim]· Pending[/]",
                    draft.body,
                    classes="thread-reply pending-draft",
                ),
            )
            for draft in drafts
        ]
        self._history_card = CommentCard("", "", classes="earlier-replies")
        self._history_count = self.history_count(len(self._submitted))
        cards = [card for _, card in self._submitted]
        if self._history_count:
            cards.insert(1, self._history_card)
        cards.extend(card for _, card in self._draft_cards)
        self._review_thread_card._comment_cards = cards
        self._visible_entries: list[
            tuple[PRComment | PendingReviewComment | None, CommentCard]
        ] = []
        self._refresh_history()

    @classmethod
    def history_count(cls, comment_count: int) -> int:
        """Hide older replies only once a submitted thread exceeds the limit."""
        reply_count = comment_count - 1
        if reply_count <= cls.EXPANDED_REPLY_LIMIT:
            return 0
        return reply_count - cls.RECENT_REPLY_COUNT

    def _refresh_history(self) -> None:
        expanded = self.root_id in self.discussion_state.history_roots
        for _, card in self._submitted[1 : self._history_count + 1]:
            card.display = expanded
        count = self._history_count
        label = "reply" if count == 1 else "replies"
        self._history_card.set_content(
            "▾ Hide earlier replies · [dim]Enter[/]"
            if expanded
            else f"▸ {count} earlier {label} · [dim]Enter[/]",
            "",
        )
        self._visible_entries = self._entries()

    def _entries(
        self,
    ) -> list[tuple[PRComment | PendingReviewComment | None, CommentCard]]:
        entries: list[tuple[PRComment | PendingReviewComment | None, CommentCard]] = []
        for index, (comment, card) in enumerate(self._submitted):
            if index == 1 and self._history_count:
                entries.append((None, self._history_card))
            if card.display:
                entries.append((comment, card))
        entries.extend(self._draft_cards)
        return entries

    @property
    def comment_count(self) -> int:
        return len(self._visible_entries)

    def comment_at(self, index: int) -> PRComment | None:
        entries = self._visible_entries
        entry = entries[index][0] if 0 <= index < len(entries) else None
        return entry if isinstance(entry, PRComment) else None

    def pending_at(self, index: int) -> PendingReviewComment | None:
        entries = self._visible_entries
        entry = entries[index][0] if 0 <= index < len(entries) else None
        return entry if isinstance(entry, PendingReviewComment) else None

    def comment_card_at(self, index: int) -> CommentCard | None:
        entries = self._visible_entries
        return entries[index][1] if 0 <= index < len(entries) else None

    def toggle_history(self, target: Widget) -> bool:
        if not self._history_count or target is not self._history_card:
            return False
        roots = self.discussion_state.history_roots
        if self.root_id in roots:
            roots.remove(self.root_id)
        else:
            roots.add(self.root_id)
        self._refresh_history()
        self.post_message(self.DisclosureChanged(self, self._history_card))
        return True

    def on_click(self, event: events.Click) -> None:
        target = event.widget
        if target is self._history_card or (
            target and self._history_card in target.ancestors
        ):
            self.toggle_history(self._history_card)
            event.stop()

    def set_resolved(self, is_resolved: bool, *, title: str | None = None) -> None:
        super().set_resolved(
            is_resolved,
            title=self._resolved_title if is_resolved else self._open_title,
        )
