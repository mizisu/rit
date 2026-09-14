from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive, var
from textual.widgets import Static

from rit.ui.widgets.branch_info import BranchInfo

if TYPE_CHECKING:
    from rit.state.models import PR


PRStatus = Literal["Open", "Merged", "Closed", "Draft"]

__all__ = (
    "Header",
    "PRStatus",
)


class Header(Vertical):
    """Application header showing PR identity and comparison branches."""

    DEFAULT_CSS = """
    Header {
        dock: top;
        height: auto;
        padding: 1 1 0 1;
        background: $surface;
        border-bottom: solid $primary;
    }

    Header #header-title-row {
        height: 1;
    }

    Header .pr-title {
        width: 1fr;
        height: 1;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }

    Header .pr-status {
        width: auto;
        padding: 0 1;
    }

    Header .status-open {
        color: $success;
    }

    Header .status-merged {
        color: $secondary;
    }

    Header .status-closed {
        color: $error;
    }

    Header .status-draft {
        color: $text-muted;
    }
    """

    @dataclass
    class PRInfoUpdated(Message):
        title: str
        status: PRStatus

    pr_title: reactive[str] = reactive("Loading...")
    pr_status: reactive[PRStatus] = reactive("Open")

    _status_open: var[bool] = var(True, toggle_class="status-open")
    _status_merged: var[bool] = var(False, toggle_class="status-merged")
    _status_closed: var[bool] = var(False, toggle_class="status-closed")
    _status_draft: var[bool] = var(False, toggle_class="status-draft")

    def __init__(
        self,
        owner: str | None = None,
        repo: str | None = None,
        pr_number: int = 0,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self._branch_info = BranchInfo(id="header-branch-row")

    def compose(self) -> ComposeResult:
        repo_str = f"{self.owner}/{self.repo}" if self.owner else "(current repo)"
        with Horizontal(id="header-title-row"):
            yield Static(
                f"PR #{self.pr_number} - {repo_str}",
                classes="pr-title",
                id="header-title",
            )
            yield Static(
                f"[{self.pr_status}]",
                classes="pr-status status-open",
                id="header-status",
            )
        yield self._branch_info

    def watch_pr_status(self, new_status: PRStatus) -> None:
        self._status_open = new_status == "Open"
        self._status_merged = new_status == "Merged"
        self._status_closed = new_status == "Closed"
        self._status_draft = new_status == "Draft"

        try:
            status_widget = self.query_one("#header-status", Static)
            status_widget.update(f"[{new_status}]")
            status_widget.remove_class(
                "status-open", "status-merged", "status-closed", "status-draft"
            )
            status_class = f"status-{new_status.lower()}"
            status_widget.add_class(status_class)
        except NoMatches:
            pass

    def watch_pr_title(self, new_title: str) -> None:
        try:
            self._update_title_display(new_title)
        except NoMatches:
            pass

    def update_from_pr(self, pr: PR) -> None:
        self.pr_title = pr.title
        self.pr_status = pr.state_display
        self._branch_info.update_branches(pr.base_ref, pr.head_ref)

        self.post_message(self.PRInfoUpdated(title=pr.title, status=pr.state_display))

    def update_pr_info(
        self,
        title: str,
        status: PRStatus,
    ) -> None:
        self.pr_title = title
        self.pr_status = status

        self.post_message(self.PRInfoUpdated(title=title, status=status))

    def _update_title_display(self, title: str) -> None:
        title_widget = self.query_one("#header-title", Static)
        repo_str = f"{self.owner}/{self.repo}" if self.owner else "(current repo)"

        max_title_len = 50
        if len(title) > max_title_len:
            display_title = f"{title[:max_title_len]}..."
        else:
            display_title = title

        title_widget.update(f"{display_title} - {repo_str}")
