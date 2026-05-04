from __future__ import annotations

import subprocess
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App, SeverityLevel
from textual.binding import Binding
from textual.reactive import var
from textual.signal import Signal

from rit.state.settings import Settings
from rit.ui.messages import Flash, SettingChanged

if TYPE_CHECKING:
    from rit.ui.screens.main import MainScreen


class RitApp(App):
    """GitHub PR Review TUI Application."""

    TITLE = "rit"
    CSS_PATH = Path(__file__).parent / "rit.tcss"
    PAUSE_GC_ON_SCROLL = True

    NAVIGATION_GROUP = Binding.Group("Navigation", compact=True)

    BINDINGS = [
        Binding("q", "quit", "Quit", show=False),
        Binding("f2", "settings", "Settings", show=False),
        Binding("o", "open_pr", "Open PR", tooltip="Open PR in web browser"),
    ]

    _settings_dict: var[dict] = var({})
    sidebar_visible: var[bool] = var(True)

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

    @cached_property
    def settings(self) -> Settings:
        return Settings(
            settings=self._settings_dict,
            on_change=self._on_setting_changed,
        )

    def _on_setting_changed(
        self, key: str, value: object, old_value: object | None
    ) -> None:
        if key == "ui.theme" and isinstance(value, str):
            self.theme = value

        self.settings_changed_signal.publish((key, value, old_value))
        self.post_message(SettingChanged(key=key, value=value, old_value=old_value))

    def on_mount(self) -> None:
        from rit.ui.screens.main import MainScreen

        self.theme = self.settings.theme

        main_screen = MainScreen(
            owner=self.owner,
            repo=self.repo,
            pr_number=self.pr_number,
        )
        self.push_screen(main_screen)

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

    def action_open_pr(self) -> None:
        try:
            if self.owner and self.repo:
                subprocess.run(
                    [
                        "gh",
                        "pr",
                        "view",
                        str(self.pr_number),
                        "--web",
                        "-R",
                        f"{self.owner}/{self.repo}",
                    ],
                    check=True,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["gh", "pr", "view", str(self.pr_number), "--web"],
                    check=True,
                    capture_output=True,
                )
        except subprocess.CalledProcessError:
            self.notify("Failed to open PR in browser", severity="error")

    def action_settings(self) -> None:
        # TODO: Implement settings screen
        self.notify("Settings coming soon!", title="Settings")

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
