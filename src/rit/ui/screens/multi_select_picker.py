from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option, OptionDoesNotExist

__all__ = (
    "MultiSelectItem",
    "MultiSelectPickerScreen",
    "MultiSelectResult",
)


@dataclass(frozen=True)
class MultiSelectItem:
    key: str
    label: str
    search_text: str = ""


@dataclass(frozen=True)
class MultiSelectResult:
    selected_keys: tuple[str, ...]


class MultiSelectPickerScreen(ModalScreen[MultiSelectResult | None]):
    """Searchable multi-select modal for small PR management pickers."""

    DEFAULT_CSS = """
    MultiSelectPickerScreen {
        align: center middle;
    }

    #multi-select-dialog {
        width: 76;
        max-width: 92%;
        max-height: 88%;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    #multi-select-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #multi-select-search {
        margin-bottom: 1;
    }

    #multi-select-options {
        height: 14;
        min-height: 8;
        max-height: 18;
        margin-bottom: 1;
    }

    #multi-select-help {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Next", show=False),
        Binding("down", "cursor_down", "Next", show=False),
        Binding("k", "cursor_up", "Prev", show=False),
        Binding("up", "cursor_up", "Prev", show=False),
        Binding("space", "toggle_selected", "Toggle", show=False),
        Binding("tab", "focus_next", "Next Field", show=False),
        Binding("shift+tab", "focus_prev", "Prev Field", show=False),
        Binding("ctrl+s", "submit", "Apply", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        *,
        title: str,
        items: list[MultiSelectItem],
        selected_keys: set[str],
        placeholder: str = "Filter...",
        empty_label: str = "No matches",
    ) -> None:
        super().__init__()
        self._title = title
        self._items = items
        self._selected_keys = set(selected_keys)
        self._placeholder = placeholder
        self._empty_label = empty_label
        self._query = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="multi-select-dialog"):
            yield Static(self._title, id="multi-select-title")
            yield Input(placeholder=self._placeholder, id="multi-select-search")
            yield OptionList(id="multi-select-options")
            yield Static(
                "Type to filter • Tab to list • Space/Enter to toggle • Ctrl+S apply • Esc cancel",
                id="multi-select-help",
            )

    def on_mount(self) -> None:
        self._refresh_options()
        self.query_one("#multi-select-search", Input).focus()

    def _refresh_options(self) -> None:
        options = self.query_one("#multi-select-options", OptionList)
        previous_id = self._highlighted_option_id(options)
        items = self._filtered_items()
        options.clear_options()

        if not items:
            options.add_option(Option(self._empty_label, id="empty", disabled=True))
            return

        options.add_options(
            [
                Option(self._option_prompt(item), id=item.key)
                for item in items
                if item.key
            ]
        )
        self._restore_highlight(options, previous_id)

    def _filtered_items(self) -> list[MultiSelectItem]:
        query = self._query.strip().casefold()
        if not query:
            return self._items
        return [
            item
            for item in self._items
            if query in item.key.casefold()
            or query in item.label.casefold()
            or query in item.search_text.casefold()
        ]

    def _option_prompt(self, item: MultiSelectItem) -> str:
        marker = "[x]" if item.key in self._selected_keys else "[ ]"
        return f"{marker} {item.label}"

    def _highlighted_option_id(self, options: OptionList) -> str | None:
        highlighted = options.highlighted_option
        return highlighted.id if highlighted is not None else None

    def _restore_highlight(self, options: OptionList, option_id: str | None) -> None:
        if option_id is not None:
            try:
                options.highlighted = options.get_option_index(option_id)
                return
            except OptionDoesNotExist:
                pass
        options.action_first()

    @on(Input.Changed, "#multi-select-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._query = event.value
        self._refresh_options()

    @on(Input.Submitted, "#multi-select-search")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one("#multi-select-options", OptionList).focus()

    def action_cursor_down(self) -> None:
        options = self.query_one("#multi-select-options", OptionList)
        options.focus()
        options.action_cursor_down()

    def action_cursor_up(self) -> None:
        options = self.query_one("#multi-select-options", OptionList)
        options.focus()
        options.action_cursor_up()

    def action_toggle_selected(self) -> None:
        options = self.query_one("#multi-select-options", OptionList)
        highlighted = options.highlighted_option
        if highlighted is None or highlighted.disabled or highlighted.id is None:
            return
        self._toggle_key(highlighted.id)

    def _toggle_key(self, key: str) -> None:
        if key == "empty":
            return
        if key in self._selected_keys:
            self._selected_keys.remove(key)
        else:
            self._selected_keys.add(key)
        self._refresh_options()

    def action_focus_next(self) -> None:
        search = self.query_one("#multi-select-search", Input)
        options = self.query_one("#multi-select-options", OptionList)
        if search.has_focus:
            options.focus()
        else:
            search.focus()

    def action_focus_prev(self) -> None:
        self.action_focus_next()

    def action_submit(self) -> None:
        self.dismiss(
            MultiSelectResult(selected_keys=tuple(sorted(self._selected_keys)))
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#multi-select-options")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id is not None:
            self._toggle_key(event.option_id)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"cursor_down", "cursor_up", "toggle_selected"} and isinstance(
            self.focused, Input
        ):
            return False
        return super().check_action(action, parameters)
