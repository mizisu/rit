from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import var
from textual.widgets import Rule, Static

from rit.state.reviewer_status import ReviewerDisplayState, derive_reviewer_states
from rit.state.store import PRStore
from rit.ui.components.pr_timeline import PRTimeline

__all__ = ("PRInfo",)


_CSS_PATH = Path(__file__).parent / "pr_info.tcss"
_DEFAULT_CSS = _CSS_PATH.read_text() if _CSS_PATH.exists() else ""
_PR_STATUS_LABELS: dict[str, str] = {
    "Open": "[#a6da95]◎ Open[/]",
    "Merged": "[#c6a0f6]◉ Merged[/]",
    "Closed": "[#ed8796]⊘ Closed[/]",
    "Draft": "[#6e738d]◌ Draft[/]",
}
_DEFAULT_PR_STATUS_LABEL = _PR_STATUS_LABELS["Open"]


class PRInfo(Container):
    DEFAULT_CSS = _DEFAULT_CSS

    wide: var[bool] = var(False, toggle_class="-wide")

    @dataclass
    class ResolveToggled(Message):
        thread_id: str
        root_comment_id: int
        is_resolved: bool  # New state after toggle

    def __init__(self, store: PRStore) -> None:
        super().__init__()
        self.store = store
        self._timeline: PRTimeline | None = None
        self._title_widget: Static | None = None
        self._status_widget: Static | None = None
        self._branch_widget: Static | None = None
        self._stats_widget: Static | None = None
        self._labels_widget: Static | None = None
        self._assignees_widget: Static | None = None
        self._reviewers_widget: Static | None = None
        self._header_render_signature: tuple[str, int, str, str, str, int, int] | None = (
            None
        )
        self._labels_render_signature: tuple[int, int, int, int, int] | None = None
        self._labels_render_text: str | None = None
        self._assignees_render_signature: tuple[int, int, int, int, int] | None = None
        self._assignees_render_text: str | None = None
        self._reviewers_render_signature: tuple[int, int, int, int, int, int] | None = (
            None
        )
        self._reviewers_render_text: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="pr-info-layout"):
            with VerticalScroll(id="main-scroll"):
                with Vertical(classes="main-content", id="main-content"):
                    yield Static("Loading...", classes="pr-title", id="pr-title")
                    yield Static("", id="pr-status")
                    yield Static("", classes="branch-info", id="branch-info")
                    yield Static("", classes="stats-bar", id="pr-stats")
                    yield Rule()
                    yield PRTimeline(self.store, id="pr-timeline")

            with Vertical(classes="sidebar", id="sidebar"):
                with Vertical(classes="sidebar-section"):
                    yield Static("Reviewers", classes="sidebar-section-title")
                    yield Static("Loading...", classes="placeholder", id="pr-reviewers")

                with Vertical(classes="sidebar-section"):
                    yield Static("Assignees", classes="sidebar-section-title")
                    yield Static("Loading...", classes="placeholder", id="pr-assignees")

                with Vertical(classes="sidebar-section"):
                    yield Static("Labels", classes="sidebar-section-title")
                    yield Static("Loading...", classes="placeholder", id="pr-labels")

    def on_mount(self) -> None:
        self._update_wide_state()

        try:
            main_scroll = self.query_one("#main-scroll", VerticalScroll)
            timeline = self._timeline_widget()
            timeline.set_scroll_container(main_scroll)
        except NoMatches:
            pass

        if self.store.state.pr:
            self.refresh_pr_data()

    def on_resize(self, event: events.Resize) -> None:
        self._update_wide_state()

    def on_click(self, event: events.Click) -> None:
        pass

    def _update_wide_state(self) -> None:
        self.wide = self.size.width >= 120

    @on(PRTimeline.ResolveToggled)
    def on_timeline_resolve_toggled(self, event: PRTimeline.ResolveToggled) -> None:
        event.stop()
        self.post_message(
            self.ResolveToggled(
                thread_id=event.thread_id,
                root_comment_id=event.root_comment_id,
                is_resolved=event.is_resolved,
            )
        )

    def refresh_summary(self) -> None:
        self._update_header()
        self._update_sidebar()

    def refresh_pr_data(self) -> None:
        self.refresh_summary()

        timeline = self._timeline_widget()
        timeline.refresh_description(self.store.state.pr)

    def refresh_comments(self) -> None:
        timeline = self._timeline_widget()
        timeline.refresh_timeline()

    def refresh_thread_metadata(self) -> None:
        timeline = self._timeline_widget()
        timeline.refresh_thread_metadata()

    def cancel_comment_refresh(self) -> bool:
        return self._timeline_widget().cancel_refresh()

    def start_issue_comment(self) -> None:
        self._timeline_widget().start_issue_comment()

    def close_issue_comment(self) -> None:
        self._timeline_widget().close_issue_comment()

    def refresh_reviewers(self) -> None:
        self._update_reviewers()

    def _timeline_widget(self) -> PRTimeline:
        if self._timeline is None:
            self._timeline = self.query_one(PRTimeline)
        return self._timeline

    def _static_widget(self, attr_name: str, selector: str) -> Static:
        widget = getattr(self, attr_name)
        if widget is None:
            widget = self.query_one(selector, Static)
            setattr(self, attr_name, widget)
        return widget

    def _update_header(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        signature = (
            pr.title,
            pr.number,
            pr.state_display,
            pr.base_ref,
            pr.head_ref,
            pr.additions,
            pr.deletions,
        )
        if signature == self._header_render_signature:
            return

        title_widget = self._static_widget("_title_widget", "#pr-title")
        title_widget.update(
            f"[bold underline]{pr.title}[/bold underline] [#6e738d]#{pr.number}[/]"
        )

        status_widget = self._static_widget("_status_widget", "#pr-status")
        status_widget.update(
            _PR_STATUS_LABELS.get(pr.state_display, _DEFAULT_PR_STATUS_LABEL)
        )

        branch_widget = self._static_widget("_branch_widget", "#branch-info")
        branch_widget.update(f"[#8aadf4]{pr.base_ref}[/] ← [#8aadf4]{pr.head_ref}[/]")

        stats_widget = self._static_widget("_stats_widget", "#pr-stats")
        total_changes = pr.additions + pr.deletions
        if total_changes > 0:
            add_ratio = pr.additions / total_changes
            add_blocks = int(add_ratio * 10)
            del_blocks = 10 - add_blocks
            graph = f"[#a6da95]{'█' * add_blocks}[/][#ed8796]{'█' * del_blocks}[/]"
        else:
            graph = "[#6e738d]──────────[/]"

        stats_widget.update(
            f"[#a6da95]+{pr.additions}[/] [#ed8796]-{pr.deletions}[/]  {graph}"
        )
        self._header_render_signature = signature

    def _update_sidebar(self) -> None:
        self._update_labels()
        self._update_assignees()
        self._update_reviewers()

    def _update_labels(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        labels = pr.labels
        signature = self._sidebar_collection_signature(labels)
        if (
            signature == self._labels_render_signature
            and self._labels_render_text is not None
        ):
            return

        labels_widget = self._static_widget("_labels_widget", "#pr-labels")
        label_count = len(labels)
        if label_count == 0:
            labels_text = "[#6e738d]None yet[/]"
        elif label_count == 1:
            labels_text = f"● {labels[0].name}"
        else:
            labels_text = "\n".join(f"● {label.name}" for label in labels)
        labels_widget.update(labels_text)
        self._labels_render_signature = signature
        self._labels_render_text = labels_text

    def _update_assignees(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        assignees = pr.assignees
        signature = self._sidebar_collection_signature(assignees)
        if (
            signature == self._assignees_render_signature
            and self._assignees_render_text is not None
        ):
            return

        assignees_widget = self._static_widget("_assignees_widget", "#pr-assignees")
        assignee_count = len(assignees)
        if assignee_count == 0:
            assignees_text = "[#6e738d]None yet[/]"
        elif assignee_count == 1:
            assignees_text = f"@{assignees[0].login}"
        else:
            assignees_text = "\n".join(f"@{user.login}" for user in assignees)
        assignees_widget.update(assignees_text)
        self._assignees_render_signature = signature
        self._assignees_render_text = assignees_text

    def _update_reviewers(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        signature = self._reviewers_signature()
        if (
            signature == self._reviewers_render_signature
            and self._reviewers_render_text is not None
        ):
            return

        reviewers_widget = self._static_widget("_reviewers_widget", "#pr-reviewers")
        reviewers = derive_reviewer_states(pr, state.reviews)
        if not reviewers:
            reviewers_text = "[#6e738d]None yet[/]"
            reviewers_widget.update(reviewers_text)
            self._reviewers_render_signature = signature
            self._reviewers_render_text = reviewers_text
            return

        reviewer_count = len(reviewers)
        if reviewer_count == 1:
            reviewers_text = self._format_reviewer_line(reviewers[0])
        else:
            reviewers_text = "\n".join(
                self._format_reviewer_line(reviewer) for reviewer in reviewers
            )
        reviewers_widget.update(reviewers_text)
        self._reviewers_render_signature = signature
        self._reviewers_render_text = reviewers_text

    def _reviewers_signature(self) -> tuple[int, int, int, int, int, int]:
        state = self.store.state
        reviews = state.reviews
        return (
            id(state.pr),
            id(reviews),
            len(reviews),
            id(reviews[0]) if reviews else 0,
            id(reviews[-1]) if reviews else 0,
            len(state.pr.requested_reviewers) if state.pr else 0,
        )

    def _sidebar_collection_signature(
        self,
        values: Sequence[object],
    ) -> tuple[int, int, int, int, int]:
        return (
            id(self.store.state.pr),
            id(values),
            len(values),
            id(values[0]) if values else 0,
            id(values[-1]) if values else 0,
        )

    def _format_reviewer_line(self, reviewer: ReviewerDisplayState) -> str:
        name = (
            reviewer.display_name if reviewer.is_team else f"@{reviewer.display_name}"
        )
        if reviewer.kind == "approved":
            return f"[#a6da95]✓[/] {name}"
        if reviewer.kind == "changes_requested":
            return f"[#ed8796]●[/] {name}"
        if reviewer.kind == "commented":
            return f"[#6e738d]○[/] {name}"
        if reviewer.kind == "dismissed":
            return f"[#6e738d]—[/] {name}"
        if reviewer.kind == "pending":
            return f"[#6e738d]○[/] {name}"
        return f"[#eed49f]●[/] {name}"

    def next_item(self) -> None:
        self._timeline_widget().next_item()

    def prev_item(self) -> None:
        self._timeline_widget().prev_item()

    def toggle_current(self) -> None:
        self._timeline_widget().toggle_current()

    def clear_selection(self) -> None:
        self._timeline_widget().clear_selection()

    @property
    def has_selection(self) -> bool:
        return self._timeline_widget().has_selection

    @property
    def current_item(self):
        return self._timeline_widget().current_item

    def next_comment(self) -> None:
        self._timeline_widget().next_comment()

    def prev_comment(self) -> None:
        self._timeline_widget().prev_comment()

    def select_first_item(self) -> None:
        self._timeline_widget().select_first_item()

    def select_last_item(self) -> None:
        self._timeline_widget().select_last_item()

    def select_first_visible_item(self) -> None:
        self._timeline_widget().select_first_visible_item()

    def get_current_thread_info(self) -> tuple[str, int, bool] | None:
        return self._timeline_widget().get_current_thread_info()

    def get_current_thread_location(
        self,
    ) -> tuple[str, int, Literal["LEFT", "RIGHT"]] | None:
        return self._timeline_widget().get_current_thread_location()

    async def toggle_resolve(self) -> tuple[bool, bool]:
        return await self._timeline_widget().toggle_resolve()
