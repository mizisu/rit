"""Repository inbox before a PR has been selected."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from rit.ui.widgets.pr_sidebar import PRSidebar


class PRListScreen(Screen[None]):
    """Entry screen for `rit` without a PR reference."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield PRSidebar(id="pr-sidebar")
            with Vertical(id="pr-workspace"):
                yield Static(
                    "Select a pull request to start reviewing", id="pr-list-empty"
                )

    def on_mount(self) -> None:
        self.query_one(PRSidebar).focus_list()
