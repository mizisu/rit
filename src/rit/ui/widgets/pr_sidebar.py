"""Keyboard-first PR inbox sidebar."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

from rich.text import Text
from textual import getters, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static, Tab, Tabs
from textual.widgets.option_list import Option, OptionDoesNotExist

from rit.app import RitApp
from rit.state.pr_list import PRListItem
from rit.ui.pr_status import PR_STATUS_LABELS


class PRSidebar(Vertical, can_focus_children=False):
    """Single-line PR rows and permanently expanded branch stacks."""

    app = getters.app(RitApp)

    DEFAULT_CSS = """
    PRSidebar {
        display: none;
        width: 64;
        max-width: 42%;
        height: 1fr;
        border-right: solid $panel;
        padding: 0 1;
        background: $background;
    }
    PRSidebar #pr-list-title { height: 3; padding-top: 1; }
    PRSidebar Underline { display: none; }
    PRSidebar OptionList, PRSidebar OptionList:focus {
        height: 1fr;
        border: none;
        padding: 0;
        background: $background;
        background-tint: transparent;
    }
    PRSidebar .option-list--option { padding: 0; }
    PRSidebar OptionList > .option-list--option-highlighted {
        background: $panel;
        color: $text;
        text-style: none;
    }
    PRSidebar OptionList:focus > .option-list--option-highlighted {
        background: $primary 15%;
        color: $text;
        text-style: bold;
    }
    PRSidebar #pr-list-preview {
        height: auto;
        max-height: 5;
        border-top: solid $panel;
        padding: 0 1;
        margin-top: 1;
    }
    PRSidebar #pr-list-status { height: auto; max-height: 5; color: $warning; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "next_pr", "Next PR", show=False),
        Binding("k", "previous_pr", "Previous PR", show=False),
        Binding("h,l", "switch_list", "Switch list", show=False),
        Binding("ctrl+r", "refresh_list", "Refresh PRs", show=False),
        Binding("escape", "close_list", "Back to PR", show=False),
        Binding("ctrl+l", "focus_review", "Review", show=False),
    ]

    @dataclass
    class Selected(Message):
        number: int

    def compose(self) -> ComposeResult:
        yield Static("Reviews", id="pr-list-title")
        yield Tabs(
            Tab("Created", id="created"),
            Tab("To review", id="review"),
            active=self.app.pr_list_tab,
            id="pr-list-tabs",
        )
        yield OptionList(id="pr-list-options")
        yield Static("", id="pr-list-preview", markup=False)
        yield Static("", id="pr-list-status", markup=False)

    def on_mount(self) -> None:
        self.sync_visibility()
        self.refresh_list()
        self.app.settings_changed_signal.subscribe(self, self._settings_changed)

    def _settings_changed(self, change: tuple[str, object, object]) -> None:
        if change[0] == "ui.theme":
            self.call_after_refresh(self.refresh_list)

    def on_resize(self) -> None:
        self.call_after_refresh(self.refresh_list)

    def sync_visibility(self) -> None:
        self.display = self.app.pr_list_visible
        self.can_focus_children = self.display
        if not self.display and self.has_focus_within:
            self.screen.set_focus(None)
        self.screen.set_class(
            self.display and self.app.size.width < 140, "pr-list-overlay"
        )
        if self.is_mounted:
            self.query_one(Tabs).active = self.app.pr_list_tab

    def focus_list(self) -> None:
        self.query_one(OptionList).focus()

    def refresh_list(self) -> None:
        if not self.is_mounted:
            return
        options = self.query_one(OptionList)
        selected = options.highlighted_option
        previous = selected.id if selected else str(self.app.pr_number)
        width = max(12, options.scrollable_content_region.width)
        store = self.app.pr_list_store
        snapshot = store.snapshot
        rows: list[Option] = []
        targets: list[str] = []
        title = Text("Reviews", style="bold")
        if snapshot:
            title.append(f"  {snapshot.owner}/{snapshot.repo}", style="dim")
            now = datetime.now(UTC)
            stacks = snapshot.stacks(self.app.pr_list_tab, now=now)
            groups = (
                ("Needs review", "Done")
                if self.app.pr_list_tab == "review"
                else ("Draft", "Open", "Done")
            )
            for group in groups:
                members = [stack for stack in stacks if stack.group == group]
                count = sum(len(stack.targets) for stack in members)
                done = group == "Done"
                expanded = self.app.pr_list_tab in self.app.pr_list_done_expanded
                marker = ("▾ " if expanded else "▸ ") if done else ""
                heading = Text(
                    f" {marker}{group}  {count}",
                    style=f"bold on {self.app.theme_variables['panel']}",
                )
                heading.truncate(width, pad=True)
                rows.append(
                    Option(
                        heading,
                        id="group-done" if done else None,
                        disabled=not done or not count,
                    )
                )
                if done and not expanded:
                    continue
                for stack in members:
                    stacked = len(stack.members) > 1
                    if stacked:
                        stack_title = " ".join(stack.members[0].title.split())
                        stack_heading = Text(f"┬ {stack_title}", no_wrap=True)
                        count_label = Text(f"  {len(stack.targets)}", style="dim")
                        stack_heading.truncate(
                            max(1, width - count_label.cell_len), overflow="ellipsis"
                        )
                        rows.append(Option(stack_heading + count_label, disabled=True))
                    for index, item in enumerate(stack.members):
                        prefix = (
                            ("└─ " if index == len(stack.members) - 1 else "├─ ")
                            if stacked
                            else ""
                        )
                        item_group = item.group(
                            self.app.pr_list_tab, snapshot.viewer, now
                        )
                        if item_group is not None and item_group != stack.group:
                            prefix += f"[{item_group}] "
                        if item.number in stack.targets:
                            targets.append(str(item.number))
                        rows.append(
                            Option(
                                self.row(
                                    item,
                                    width,
                                    prefix=prefix,
                                    context=item.number not in stack.targets,
                                ),
                                id=str(item.number),
                            )
                        )
                rows.append(Option("", disabled=True))
            if not stacks:
                empty = (
                    "No PRs authored by you"
                    if self.app.pr_list_tab == "created"
                    else "No review requests or submitted reviews"
                )
                rows = [Option(empty, disabled=True)]
        else:
            rows = [
                Option(
                    "Loading PRs and stacks…"
                    if store.loading or not store.attempted
                    else "Unable to load PRs",
                    disabled=True,
                )
            ]
        self.query_one("#pr-list-title", Static).update(title)
        options.clear_options().add_options(rows)
        for preferred in (previous, str(self.app.pr_number), *targets[:1]):
            if preferred:
                try:
                    options.highlighted = options.get_option_index(preferred)
                    break
                except OptionDoesNotExist:
                    pass
        else:
            options.action_first()
        self.refresh_preview()
        warnings = list(snapshot.warnings) if snapshot else []
        if store.error:
            warnings.insert(0, f"Refresh failed: {store.error}")
        if store.loading and snapshot:
            warnings.insert(
                0,
                "Refreshing PRs and stacks…"
                if snapshot.context_complete
                else "Loading stack context…",
            )
        self.query_one("#pr-list-status", Static).update("\n".join(warnings))

    @on(OptionList.OptionHighlighted, "#pr-list-options")
    def highlight_changed(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self.refresh_preview()

    def refresh_preview(self) -> None:
        """Preview titles and metadata without widening every row."""
        preview = self.query_one("#pr-list-preview", Static)
        option = self.query_one(OptionList).highlighted_option
        snapshot = self.app.pr_list_store.snapshot
        if snapshot is None or option is None:
            preview.display = False
            preview.update("")
            return
        item = next(
            (item for item in snapshot.items if str(item.number) == option.id),
            None,
        )
        preview.display = item is not None
        if item is None:
            preview.update("")
            return
        title = Text(" ".join(item.title.split()), style="bold")
        group = item.group(self.app.pr_list_tab, snapshot.viewer, datetime.now(UTC))
        state = "Draft" if item.draft and item.state == "OPEN" else item.state.title()
        metadata = f"#{item.number}  @{item.author}  {state}"
        metadata += f"  {group or 'Stack context'}"
        if item.number == self.app.pr_number:
            metadata += "  Viewing"
        title.append(f"\n{metadata}", style="dim")
        preview.update(title)

    @staticmethod
    def row(
        item: PRListItem, width: int, *, prefix: str = "", context: bool = False
    ) -> Text:
        """Keep CI and age aligned even with wide Unicode titles."""
        seconds = max(0, int((datetime.now(UTC) - item.updated_at).total_seconds()))
        age = "now"
        for unit, duration in (
            ("y", 31536000),
            ("mo", 2592000),
            ("w", 604800),
            ("d", 86400),
            ("h", 3600),
            ("m", 60),
        ):
            if seconds >= duration:
                age = f"{seconds // duration}{unit}"
                break
        check, color = {
            "SUCCESS": ("✓", "green"),
            "FAILURE": ("×", "red"),
            "ERROR": ("×", "red"),
            "PENDING": ("◌", "yellow"),
            "EXPECTED": ("◌", "yellow"),
        }.get(item.checks or "", ("-", "dim"))
        suffix = Text(f" {item.author[:8]:>8} " if width >= 72 else " ", style="dim")
        suffix.append(check, style=color)
        suffix.append(f" {age:>4}", style="dim")
        state = "Draft" if item.draft and item.state == "OPEN" else item.state.title()
        icon = Text.from_markup(PR_STATUS_LABELS[state]).split(" ")[0]
        title = Text()
        title.append(prefix, style="dim")
        title.append_text(icon)
        title.append(f" #{item.number} ", style="dim")
        if context:
            title.append("[ctx] ", style="dim")
        title.append(
            ("Draft " if state == "Draft" else "") + " ".join(item.title.split())
        )
        if context:
            title.stylize("dim")
        title.truncate(max(1, width - suffix.cell_len), overflow="ellipsis", pad=True)
        return title + suffix

    @on(Tabs.TabActivated)
    def tab_changed(self, event: Tabs.TabActivated) -> None:
        event.stop()
        if (
            self.screen is not self.app.screen
            or event.tab.id != self.query_one(Tabs).active
        ):
            return
        self.app.pr_list_tab = "created" if event.tab.id == "created" else "review"
        self.refresh_list()

    @on(OptionList.OptionSelected)
    def pr_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id == "group-done":
            tab = self.app.pr_list_tab
            if tab in self.app.pr_list_done_expanded:
                self.app.pr_list_done_expanded.remove(tab)
            else:
                self.app.pr_list_done_expanded.add(tab)
            self.refresh_list()
        elif event.option_id:
            self.post_message(self.Selected(int(event.option_id)))

    def action_next_pr(self) -> None:
        self.focus_list()
        self.query_one(OptionList).action_cursor_down()

    def action_previous_pr(self) -> None:
        self.focus_list()
        self.query_one(OptionList).action_cursor_up()

    def action_switch_list(self) -> None:
        self.app.pr_list_tab = (
            "created" if self.app.pr_list_tab == "review" else "review"
        )
        self.query_one(Tabs).active = self.app.pr_list_tab
        self.refresh_list()

    def action_refresh_list(self) -> None:
        self.app.refresh_pr_list()

    def action_close_list(self) -> None:
        if self.app.pr_number:
            self.app.pr_list_visible = False
            self.action_focus_review()

    def action_focus_review(self) -> None:
        if screen := self.app._get_main_screen():
            if self.app.size.width < 140:
                self.app.pr_list_visible = False
            screen.focus_review()
