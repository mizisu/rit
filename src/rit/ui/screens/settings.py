from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from rit.state.settings import Settings


__all__ = ("SettingsScreen",)


@dataclass(frozen=True)
class _SettingRow:
    key: str
    title: str
    value: str


class SettingsScreen(ModalScreen[None]):
    """Read-only settings overview."""

    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
    }

    #settings-dialog {
        width: 72;
        max-width: 92%;
        max-height: 88%;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    #settings-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #settings-list {
        height: auto;
        max-height: 20;
        margin-bottom: 1;
    }

    .settings-section {
        text-style: bold;
        margin-top: 1;
    }

    .settings-value {
        color: $text;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-dialog"):
            yield Static("Settings", id="settings-title")
            with VerticalScroll(id="settings-list"):
                for section in self._settings.schema:
                    title = section.get("title", section.get("key", ""))
                    yield Static(str(title), classes="settings-section")
                    for row in self._setting_rows(section):
                        yield Static(
                            f"{row.title}: {row.value}",
                            id=f"setting-{_setting_row_id(row.key)}",
                            classes="settings-value",
                        )
            yield Button("Close", id="settings-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#settings-close", Button).focus()

    def _setting_rows(
        self,
        section: dict,
        *,
        prefix: str = "",
    ) -> Iterable[_SettingRow]:
        section_key = section.get("key")
        key = f"{prefix}.{section_key}" if prefix else str(section_key)
        if section.get("type") == "object":
            for field in section.get("fields", []):
                yield from self._setting_rows(field, prefix=key)
            return

        title = str(section.get("title", key))
        yield _SettingRow(
            key=key,
            title=title,
            value=_format_setting_value(
                section,
                self._settings.get_or(key, section.get("default")),
            ),
        )

    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#settings-close")
    def on_close_pressed(self, _event: Button.Pressed) -> None:
        self.action_close()


def _setting_row_id(key: str) -> str:
    return key.replace(".", "-").replace("_", "-")


def _format_setting_value(field: dict, value: object) -> str:
    if field.get("type") == "boolean":
        return "On" if value is True else "Off"

    if field.get("type") == "choices":
        choices = field.get("choices", [])
        if isinstance(choices, list):
            for choice in choices:
                if (
                    isinstance(choice, tuple)
                    and len(choice) == 2
                    and choice[1] == value
                ):
                    return str(choice[0])

    return str(value)
