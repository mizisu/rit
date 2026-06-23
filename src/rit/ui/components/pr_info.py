from __future__ import annotations

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
            timeline = self.query_one(PRTimeline)
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

        timeline = self.query_one(PRTimeline)
        timeline.refresh_description(self.store.state.pr)

    def refresh_comments(self) -> None:
        timeline = self.query_one(PRTimeline)
        timeline.refresh_timeline()

    def refresh_thread_metadata(self) -> None:
        timeline = self.query_one(PRTimeline)
        timeline.refresh_thread_metadata()

    def cancel_comment_refresh(self) -> bool:
        return self.query_one(PRTimeline).cancel_refresh()

    def start_issue_comment(self) -> None:
        self.query_one(PRTimeline).start_issue_comment()

    def close_issue_comment(self) -> None:
        self.query_one(PRTimeline).close_issue_comment()

    def refresh_reviewers(self) -> None:
        self._update_reviewers()

    def _update_header(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        title_widget = self.query_one("#pr-title", Static)
        title_widget.update(
            f"[bold underline]{pr.title}[/bold underline] [#6e738d]#{pr.number}[/]"
        )

        status_widget = self.query_one("#pr-status", Static)
        status_icon = {
            "Open": "[#a6da95]◎ Open[/]",
            "Merged": "[#c6a0f6]◉ Merged[/]",
            "Closed": "[#ed8796]⊘ Closed[/]",
            "Draft": "[#6e738d]◌ Draft[/]",
        }.get(pr.state_display, "[#a6da95]◎ Open[/]")
        status_widget.update(status_icon)

        branch_widget = self.query_one("#branch-info", Static)
        branch_widget.update(f"[#8aadf4]{pr.base_ref}[/] ← [#8aadf4]{pr.head_ref}[/]")

        stats_widget = self.query_one("#pr-stats", Static)
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

    def _update_sidebar(self) -> None:
        self._update_labels()
        self._update_assignees()
        self._update_reviewers()

    def _update_labels(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        labels_widget = self.query_one("#pr-labels", Static)
        if pr.labels:
            labels_text = "\n".join(f"● {label.name}" for label in pr.labels)
            labels_widget.update(labels_text)
        else:
            labels_widget.update("[#6e738d]None yet[/]")

    def _update_assignees(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        assignees_widget = self.query_one("#pr-assignees", Static)
        if pr.assignees:
            assignees_text = "\n".join(f"@{u.login}" for u in pr.assignees)
            assignees_widget.update(assignees_text)
        else:
            assignees_widget.update("[#6e738d]None yet[/]")

    def _update_reviewers(self) -> None:
        state = self.store.state
        pr = state.pr

        if not pr:
            return

        reviewers_widget = self.query_one("#pr-reviewers", Static)
        reviewers = derive_reviewer_states(pr, state.reviews)
        if not reviewers:
            reviewers_widget.update("[#6e738d]None yet[/]")
            return

        reviewers_widget.update(
            "\n".join(self._format_reviewer_line(reviewer) for reviewer in reviewers)
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
        self.query_one(PRTimeline).next_item()

    def prev_item(self) -> None:
        self.query_one(PRTimeline).prev_item()

    def toggle_current(self) -> None:
        self.query_one(PRTimeline).toggle_current()

    def clear_selection(self) -> None:
        self.query_one(PRTimeline).clear_selection()

    @property
    def has_selection(self) -> bool:
        return self.query_one(PRTimeline).has_selection

    @property
    def current_item(self):
        return self.query_one(PRTimeline).current_item

    def next_comment(self) -> None:
        self.query_one(PRTimeline).next_comment()

    def prev_comment(self) -> None:
        self.query_one(PRTimeline).prev_comment()

    def select_first_item(self) -> None:
        self.query_one(PRTimeline).select_first_item()

    def select_last_item(self) -> None:
        self.query_one(PRTimeline).select_last_item()

    def select_first_visible_item(self) -> None:
        self.query_one(PRTimeline).select_first_visible_item()

    def get_current_thread_info(self) -> tuple[str, int, bool] | None:
        return self.query_one(PRTimeline).get_current_thread_info()

    def get_current_thread_location(
        self,
    ) -> tuple[str, int, Literal["LEFT", "RIGHT"]] | None:
        return self.query_one(PRTimeline).get_current_thread_location()

    async def toggle_resolve(self) -> tuple[bool, bool]:
        return await self.query_one(PRTimeline).toggle_resolve()
