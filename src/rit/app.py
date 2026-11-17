from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import replace
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import pyperclip
from textual import events, on
from textual.app import App, SystemCommand
from textual.binding import Binding
from textual.notifications import SeverityLevel
from textual.reactive import var
from textual.screen import Screen
from textual.signal import Signal
from textual.widgets import Input, TextArea

from rit.services.gh_cli import GhCliError, run_gh
from rit.services.tmux_navigation import TmuxNavigation
from rit.state.pr_list import PRListTab
from rit.state.settings import Settings
from rit.state.store import PRListStore
from rit.ui.messages import Flash, SettingChanged
from rit.ui.terminal_graphics import configure_terminal_graphics
from rit.ui.widgets.comment_editor import InlineCommentEditor

if TYPE_CHECKING:
    from rit.ui.screens.main import MainScreen
    from rit.ui.widgets.pr_sidebar import PRSidebar


__all__ = ("RitApp",)


class RitApp(App):
    """GitHub PR Review TUI Application."""

    TITLE = "rit"
    CSS_PATH = Path(__file__).parent / "rit.tcss"
    PAUSE_GC_ON_SCROLL = True

    NAVIGATION_GROUP = Binding.Group("Navigation", compact=True)

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+e,ctrl+shift+p", "toggle_pr_list", "PRs", priority=True),
        Binding("q", "quit", "Quit", show=False),
        Binding("f2", "settings", "Settings", show=False),
        Binding("o", "open_pr", "Open PR", tooltip="Open PR in web browser"),
    ]

    _settings_dict: var[dict] = var({})
    sidebar_visible: var[bool] = var(True)
    pr_list_visible: var[bool] = var(False)

    def __init__(
        self,
        owner: str | None = None,
        repo: str | None = None,
        pr_number: int = 0,
    ) -> None:
        self.settings_changed_signal: Signal[tuple[str, object, object]] = Signal(
            self, "settings-changed"
        )

        super().__init__()

        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self.pr_list_store = PRListStore(owner=owner, repo=repo)
        self.pr_list_store.set_message_sink(self.post_message)
        self.pr_list_tab: PRListTab = "review"
        self.pr_list_done_expanded: set[PRListTab] = set()
        self._pr_screens: dict[int, Screen[None]] = {}
        self._pr_list_refresh_pending = False
        self.pr_list_visible = not pr_number
        self.tmux_navigation = TmuxNavigation()

    @cached_property
    def settings(self) -> Settings:
        return Settings(
            settings=self._settings_dict,
            on_change=self._apply_setting_change,
        )

    def _apply_setting_change(
        self, key: str, value: object, old_value: object | None
    ) -> None:
        if key == "ui.theme" and isinstance(value, str):
            self.theme = value

        self.settings_changed_signal.publish((key, value, old_value))
        self.post_message(SettingChanged(key=key, value=value, old_value=old_value))

    def copy_to_clipboard(self, text: str) -> None:
        """Copy text through Textual and the native system clipboard."""
        super().copy_to_clipboard(text)
        try:
            pyperclip.copy(text)
        except pyperclip.PyperclipException:
            pass

    def on_mount(self) -> None:
        self.app_suspend_signal.subscribe(
            self, lambda _: self.tmux_navigation.pause(), immediate=True
        )
        self.app_resume_signal.subscribe(
            self, lambda _: self.tmux_navigation.resume(), immediate=True
        )

        self.run_worker(
            asyncio.to_thread(configure_terminal_graphics),
            name="terminal-graphics-setup",
        )
        self.theme = self.settings.theme

        self.push_screen(self._review_screen(self.pr_number))
        if not self.pr_number:
            self.refresh_pr_list()

    def _review_screen(self, number: int) -> Screen[None]:
        from rit.ui.screens.main import MainScreen
        from rit.ui.screens.pr_list import PRListScreen

        if number not in self._pr_screens:
            screen = (
                MainScreen(self.owner, self.repo, number) if number else PRListScreen()
            )
            self._pr_screens[number] = screen
            self.install_screen(screen, f"pr-{number}")
        return self._pr_screens[number]

    async def on_prsidebar_selected(self, event: PRSidebar.Selected) -> None:
        event.stop()
        await self.open_pr_number(event.number)

    async def open_pr_number(self, number: int) -> None:
        """Switch installed screens without destroying PR-local drafts or widgets."""
        if self.screen.is_modal or number <= 0:
            return
        self.pr_number = number
        if self.size.width < 140:
            self.pr_list_visible = False
        await self.switch_screen(self._review_screen(number))
        self._sync_pr_sidebars()
        if screen := self._get_main_screen():
            screen.focus_review()
        self.refresh_bindings()

    def _sync_pr_sidebars(self) -> None:
        from rit.ui.widgets.pr_sidebar import PRSidebar

        for screen in self._pr_screens.values():
            if screen.is_mounted and screen is self.screen:
                for sidebar in screen.query(PRSidebar):
                    sidebar.sync_visibility()
                    sidebar.refresh_list()

    def watch_pr_list_visible(self) -> None:
        self._sync_pr_sidebars()

    def on_resize(self, _event: events.Resize) -> None:
        self.call_after_refresh(self._sync_pr_sidebars)

    def action_toggle_pr_list(self) -> None:
        from rit.ui.widgets.pr_sidebar import PRSidebar

        self.pr_list_visible = not self.pr_list_visible
        if self.pr_list_visible:
            if not self.pr_list_store.attempted:
                self.refresh_pr_list()
            self.call_after_refresh(self.screen.query_one(PRSidebar).focus_list)
        elif screen := self._get_main_screen():
            screen.focus_review()

    def refresh_pr_list(self) -> None:
        """Coalesce refreshes, including mutations made during an in-flight load."""
        self._pr_list_refresh_pending = True
        if self.pr_list_store.loading:
            return
        self.pr_list_store.loading = True
        self._sync_pr_sidebars()
        self.run_worker(self._load_pr_list(), name="pr-list", group="pr-list")

    async def _load_pr_list(self) -> None:
        while self._pr_list_refresh_pending:
            self._pr_list_refresh_pending = False
            await self.pr_list_store.load()
            self._on_pr_list_updated()

    @on(PRListStore.Updated)
    def _on_pr_list_updated(self) -> None:
        if snapshot := self.pr_list_store.snapshot:
            self.owner, self.repo = snapshot.owner, snapshot.repo
        self._sync_pr_sidebars()

    def review_submitted(self, number: int) -> None:
        """Reflect a submitted review before GitHub's search index catches up."""
        if snapshot := self.pr_list_store.snapshot:
            self.pr_list_store.snapshot = replace(
                snapshot,
                items=tuple(
                    item.model_copy(
                        update={
                            "requested": False,
                            "review_count": max(1, item.review_count),
                        }
                    )
                    if item.number == number
                    else item
                    for item in snapshot.items
                ),
            )
            self._sync_pr_sidebars()
        if self.pr_list_store.loading:
            self._pr_list_refresh_pending = True

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        if not screen.is_modal:
            yield SystemCommand(
                "PR list",
                "Show or hide the repository's pull requests",
                self.action_toggle_pr_list,
            )

    def _get_main_screen(self) -> MainScreen | None:
        from rit.ui.screens.main import MainScreen

        if isinstance(self.screen, MainScreen):
            return self.screen
        return None

    def action_next_tab(self) -> None:
        if screen := self._get_main_screen():
            screen.next_tab()

    def action_prev_tab(self) -> None:
        if screen := self._get_main_screen():
            screen.prev_tab()

    async def action_open_pr(self) -> None:
        if not self.pr_number:
            return
        command = ["pr", "view", str(self.pr_number), "--web"]
        if self.owner and self.repo:
            command.extend(("-R", f"{self.owner}/{self.repo}"))
        try:
            await run_gh(command)
        except GhCliError:
            self.notify("Failed to open PR in browser", severity="error")

    def action_settings(self) -> None:
        from rit.ui.screens.settings import SettingsScreen

        self.push_screen(SettingsScreen(self.settings))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "open_pr" and not self.pr_number:
            return False
        if action == "toggle_pr_list" and self.screen.is_modal:
            return False
        focused = self.focused
        if (
            action in {"quit", "open_pr", "settings", "toggle_pr_list"}
            and focused is not None
            and (
                isinstance(focused, (Input, TextArea, InlineCommentEditor))
                or any(
                    isinstance(ancestor, InlineCommentEditor)
                    for ancestor in focused.ancestors
                )
            )
        ):
            return False
        return super().check_action(action, parameters)

    def on_flash(self, message: Flash) -> None:
        severity_map: dict[str, SeverityLevel] = {
            "default": "information",
            "success": "information",
            "warning": "warning",
            "error": "error",
        }
        severity = severity_map.get(message.style, "information")

        from textual.content import Content

        if isinstance(message.content, Content):
            content = message.content.plain
        else:
            content = str(message.content)

        self.notify(
            content,
            severity=severity,
            timeout=message.duration or 3.0,
            markup=False,
        )
