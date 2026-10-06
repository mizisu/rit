"""Compact merge status, checks, and changed-file summaries."""

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.css.styles import RulesMap
from textual.message import Message
from textual.strip import Strip
from textual.style import Style
from textual.visual import RenderOptions, Visual
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from rit.state.models import LoadingState, PRFile
from rit.state.pr_overview import (
    CheckOutcome,
    FileChangeGroup,
    PRCheck,
    PRChecksSnapshot,
)
from rit.state.store import PRStore


class SummaryOptionList(OptionList):
    """Render logical rows instead of mounting a widget per file or check."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("g", "first", show=False),
        Binding("G", "last", show=False),
        Binding("ctrl+d", "page_down", show=False),
        Binding("ctrl+u", "page_up", show=False),
        Binding("space", "select", show=False),
    ]

    @dataclass
    class MoveFocus(Message):
        direction: int

    def action_cursor_down(self) -> None:
        if self.highlighted == self.option_count - 1:
            self.post_message(self.MoveFocus(1))
        else:
            super().action_cursor_down()
            if self.highlighted is None:
                self.post_message(self.MoveFocus(1))

    def action_cursor_up(self) -> None:
        if self.highlighted == 0:
            self.post_message(self.MoveFocus(-1))
        else:
            super().action_cursor_up()
            if self.highlighted is None:
                self.post_message(self.MoveFocus(-1))


def _line(text: str) -> Content:
    return Content(text.replace("\n", "↵").replace("\r", "␍").replace("\t", "⇥"))


class FileSummaryLabel(Visual):
    """Fit one file row to the viewport, reserving space for change counts."""

    def __init__(self, file: PRFile, display_path: str) -> None:
        color = {
            "added": "#a6da95",
            "removed": "#ed8796",
            "renamed": "#8aadf4",
            "copied": "#8aadf4",
        }.get(file.status, "#cad3f5")
        self._prefix = Content.assemble("  ", (file.status_icon, color), " ")
        self._path = _line(display_path)
        self._changes = Content.assemble(
            (f"+{file.additions}", "#a6da95"), " ", (f"-{file.deletions}", "#ed8796")
        )

    def get_optimal_width(self, rules: RulesMap, container_width: int) -> int:
        return (
            self._prefix.cell_length
            + self._path.cell_length
            + self._changes.cell_length
            + 1
        )

    def get_height(self, rules: RulesMap, width: int) -> int:
        return 1

    def render_strips(
        self, width: int, height: int | None, style: Style, options: RenderOptions
    ) -> list[Strip]:
        prefix = self._prefix.truncate(max(0, width))
        available = max(0, width - prefix.cell_length)
        show_changes = available >= self._changes.cell_length + 7
        path_width = (
            available - self._changes.cell_length - 1 if show_changes else available
        )
        path = self._path.truncate(path_width, ellipsis=True, pad=show_changes)
        row = prefix + path
        if show_changes:
            row += Content(" ") + self._changes
        strips = row.render_strips(width, 1, style, options)
        return strips or [Strip.blank(max(0, width), style.rich_style)]


@dataclass(frozen=True)
class CheckAppearance:
    """Share check icons and colors between the rollup and individual rows."""

    icon: str
    color: str
    label: str


_CHECK_APPEARANCES: dict[CheckOutcome, CheckAppearance] = {
    "success": CheckAppearance("✓", "#a6da95", "All passed"),
    "pending": CheckAppearance("●", "#eed49f", "Checks running"),
    "failure": CheckAppearance("✗", "#ed8796", "Checks failed"),
    "cancelled": CheckAppearance("!", "#eed49f", "Checks cancelled"),
    "neutral": CheckAppearance("—", "#939ab7", "Checks completed"),
    "unknown": CheckAppearance("?", "#eed49f", "Unknown check results"),
}


class PRMerge(Vertical):
    """GitHub's merge verdict with short reasons always visible."""

    def __init__(self, store: PRStore) -> None:
        super().__init__(id="pr-merge-section", classes="sidebar-section")
        self.store = store

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-section-heading"):
            yield Static("Merge", classes="sidebar-section-title")
            yield Button(
                "↻",
                id="refresh-merge",
                tooltip="Refresh merge status",
                compact=True,
                flat=True,
            )
        yield Static("Checking...", id="pr-merge-status", markup=False)

    def refresh_data(self) -> None:
        pr = self.store.state.pr
        resource = self.store.state.merge_status
        snapshot = resource.loaded_value
        if snapshot is not None and (
            pr is None
            or (snapshot.base_sha, snapshot.head_sha) != (pr.base_sha, pr.head_sha)
        ):
            snapshot = None
        self.display = not (
            (pr is not None and pr.state in {"MERGED", "CLOSED"})
            or (snapshot is not None and snapshot.state in {"MERGED", "CLOSED"})
        )
        self.query_one(Button).disabled = resource.loading == LoadingState.LOADING
        status = self.query_one("#pr-merge-status", Static)
        status.tooltip = _line(resource.error) if resource.error else None
        if not self.display:
            return
        if snapshot is None:
            label, reasons = (
                ("Unknown", ("Reason unavailable",))
                if resource.loading == LoadingState.ERROR
                else ("Checking", ())
            )
        else:
            label, reasons = snapshot.summary
        icon, color = {
            "Ready": ("✓", "#a6da95"),
            "Blocked": ("✗", "#ed8796"),
            "Queued": ("●", "#8aadf4"),
            "Checking": ("◌", "#939ab7"),
            "Warning": ("!", "#eed49f"),
            "Unknown": ("?", "#eed49f"),
        }[label]
        content = Content.assemble((f"{icon} {label}", color))
        for reason in reasons:
            content += Content("\n  ") + _line(reason)
        status.update(content)


class PRChecks(Vertical):
    """Check rollup with an on-demand list of individual results."""

    @dataclass
    class OpenURLRequested(Message):
        url: str

    def __init__(self, store: PRStore) -> None:
        super().__init__(id="pr-checks-section", classes="sidebar-section")
        self.store = store
        self._signature: tuple[object, ...] | None = None
        self._expanded = False
        self._snapshot: PRChecksSnapshot | None = None
        self._checks_by_id: dict[str, PRCheck] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-section-heading"):
            yield Static("Checks", classes="sidebar-section-title")
            yield Button(
                "↻",
                id="refresh-checks",
                tooltip="Refresh checks",
                compact=True,
                flat=True,
            )
        yield SummaryOptionList(
            Option("Loading...", id="summary", disabled=True),
            id="pr-checks",
            compact=True,
            disabled=True,
            markup=False,
        )

    def refresh_data(self) -> None:
        resource = self.store.state.checks
        signature = (id(resource.value), resource.loading, resource.error)
        if signature == self._signature:
            return
        self._signature = signature
        self.query_one("#refresh-checks", Button).disabled = (
            resource.loading == LoadingState.LOADING
        )
        self._snapshot = resource.loaded_value
        if self._snapshot is not None:
            self.query_one(SummaryOptionList).tooltip = None
            self._render_checks()
            return
        failed = resource.loading == LoadingState.ERROR
        self._set_summary("Unable to load checks" if failed else "Loading...")
        self.query_one(SummaryOptionList).tooltip = (
            _line(resource.error or "Unable to load checks") if failed else None
        )

    def _set_summary(self, text: str) -> None:
        self._checks_by_id.clear()
        options = self.query_one(SummaryOptionList)
        options.disabled = True
        options.clear_options().add_option(
            Option(_line(text), id="summary", disabled=True)
        )

    @staticmethod
    def summary(snapshot: PRChecksSnapshot) -> Content:
        outcome = snapshot.outcome
        if outcome is None:
            return _line("No checks reported")
        appearance = _CHECK_APPEARANCES[outcome]
        label = appearance.label
        if outcome == "success" and any(
            check.outcome == "neutral" for check in snapshot.checks
        ):
            label = "Checks passed"
        return Content.assemble(
            (f"{appearance.icon} {label}", appearance.color),
            f" · {len(snapshot.checks)}",
        )

    def _render_checks(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        options = self.query_one(SummaryOptionList)
        selected = (
            options.get_option_at_index(options.highlighted).id
            if options.highlighted is not None
            else "summary"
        )
        options.clear_options()
        options.disabled = not snapshot.checks
        summary = self.summary(snapshot)
        if snapshot.checks:
            summary = Content("▾ " if self._expanded else "▸ ") + summary
        options.add_option(Option(summary, id="summary", disabled=not snapshot.checks))
        self._checks_by_id = {
            f"check:{check.node_id}": check for check in snapshot.checks
        }
        if self._expanded:
            options.add_options(self._check_option(check) for check in snapshot.checks)
        if selected == "summary" or (self._expanded and selected in self._checks_by_id):
            options.highlighted = options.get_option_index(selected or "summary")

    @staticmethod
    def _check_option(check: PRCheck) -> Option:
        appearance = _CHECK_APPEARANCES[check.outcome]
        return Option(
            Content.assemble(
                (appearance.icon, appearance.color),
                _line(f" {check.name}"),
                (f" · {check.detail.lower()}", appearance.color),
            ),
            id=f"check:{check.node_id}",
        )

    @on(OptionList.OptionSelected)
    def _select(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if self._snapshot is None:
            return
        if event.option.id == "summary":
            self._expanded = not self._expanded
            self._render_checks()
        else:
            check = self._checks_by_id.get(event.option.id or "")
            if check is not None and check.url:
                self.post_message(self.OpenURLRequested(check.url))

    @on(OptionList.OptionHighlighted)
    def _highlight(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        check = self._checks_by_id.get(event.option.id or "")
        event.option_list.tooltip = (
            _line(f"{check.name} · {check.detail.lower()}") if check else None
        )


class FileGroup(Vertical):
    """One category, with only revealed files in the native option list."""

    @dataclass
    class OpenFileRequested(Message):
        filename: str

    def __init__(self, name: str) -> None:
        super().__init__(
            id=f"pr-file-group-{name.lower()}", classes="file-summary-group"
        )
        self.category_name = name
        self.group: FileChangeGroup | None = None
        self._files_by_id: dict[str, PRFile] = {}
        self._expanded = name in {"Implementation", "Tests"}
        self._user_toggled = False
        self._shown = 5
        self.display = False

    def compose(self) -> ComposeResult:
        with Horizontal(classes="file-group-heading"):
            yield Button(
                self.category_name, classes="file-group-toggle", compact=True, flat=True
            )
            yield Static("", classes="file-group-count")
            yield Static("", classes="file-group-totals")
        yield SummaryOptionList(
            id=f"summary-files-{self.category_name.lower()}",
            compact=True,
            markup=False,
        )

    def show_group(self, group: FileChangeGroup | None, *, expand: bool) -> None:
        if group is self.group:
            return
        self.group = group
        self.display = group is not None
        options = self.query_one(SummaryOptionList)
        self._files_by_id.clear()
        selected = options.highlighted
        options.clear_options()
        if group is None:
            return
        if not self._user_toggled:
            self._expanded = expand
        self._shown = min(max(self._shown, 5), len(group.files))
        self._append_files(0, self._shown)
        if selected is not None and options.option_count:
            options.highlighted = min(selected, options.option_count - 1)
        self.query_one(".file-group-count", Static).update(str(len(group.files)))
        self.query_one(".file-group-totals", Static).update(
            Content.assemble(
                (f"+{group.additions}", "#a6da95"),
                " ",
                (f"-{group.deletions}", "#ed8796"),
            )
        )
        self._update_heading()

    def _update_heading(self) -> None:
        if self.group is None:
            return
        button = self.query_one(Button)
        button.label = _line(f"{'▾' if self._expanded else '▸'} {self.category_name}")
        button.tooltip = (
            f"{self.category_name} · {len(self.group.files)} files · "
            f"+{self.group.additions} -{self.group.deletions}"
        )
        if self.category_name == "Generated":
            button.tooltip += " · identified by filename conventions"
        self.query_one(SummaryOptionList).display = self._expanded

    def _append_files(self, start: int, end: int) -> None:
        group = self.group
        if group is None:
            return
        options = self.query_one(SummaryOptionList)
        for file in group.files[start:end]:
            self._files_by_id[f"file:{file.filename}"] = file
        options.add_options(
            Option(FileSummaryLabel(file, path), id=f"file:{file.filename}")
            for file, path in zip(
                group.files[start:end], group.display_paths[start:end]
            )
        )
        if end < len(group.files):
            options.add_option(
                Option(_line(f"  … {len(group.files) - end} more"), id="more")
            )

    @on(Button.Pressed)
    def _toggle(self, event: Button.Pressed) -> None:
        event.stop()
        self._user_toggled = True
        self._expanded = not self._expanded
        self._update_heading()

    @on(OptionList.OptionSelected)
    def _select(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        group = self.group
        if group is None:
            return
        if event.option.id == "more":
            event.option_list.remove_option("more")
            end = min(self._shown + 20, len(group.files))
            self._append_files(self._shown, end)
            self._shown = end
        else:
            file = self._files_by_id.get(event.option.id or "")
            if file is not None:
                self.post_message(self.OpenFileRequested(file.filename))

    @on(OptionList.OptionHighlighted)
    def _highlight(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        file = self._files_by_id.get(event.option.id or "")
        if file is not None:
            event.option_list.tooltip = _line(
                f"{file.status.capitalize()} · {file.display_name} · "
                f"+{file.additions} -{file.deletions}"
            )
            return
        remaining = len(self.group.files) - self._shown if self.group else 0
        event.option_list.tooltip = (
            f"Show {min(20, remaining)} files · {remaining} remaining"
            if event.option.id == "more"
            else None
        )


class PRFilesSummary(Vertical):
    """Metadata-only file categories with no diff-loading side effects."""

    def __init__(self, store: PRStore) -> None:
        super().__init__(id="pr-files-summary", classes="sidebar-section")
        self.store = store
        self._signature: tuple[object, ...] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-section-heading"):
            yield Static(
                "Files changed",
                id="pr-files-summary-count",
                classes="sidebar-section-title",
            )
            yield Button(
                "↻",
                id="refresh-file-summary",
                tooltip="Refresh changed files",
                compact=True,
                flat=True,
            )
        yield Static("Loading...", id="pr-files-summary-status", classes="placeholder")
        for name in FileChangeGroup.CATEGORIES:
            yield FileGroup(name)

    def refresh_data(self) -> None:
        resource = self.store.state.file_summary
        signature = (id(resource.value), resource.loading, resource.error)
        if signature == self._signature:
            return
        self._signature = signature
        self.query_one("#refresh-file-summary", Button).disabled = (
            resource.loading == LoadingState.LOADING
        )
        snapshot = resource.loaded_value
        count = len(snapshot.metadata.files) if snapshot else 0
        status = self.query_one("#pr-files-summary-status", Static)
        status.display = snapshot is None or not count
        placeholders = {
            LoadingState.ERROR: "Unable to load files",
            LoadingState.LOADED: "No changed files",
        }
        status.update(placeholders.get(resource.loading, "Loading..."))
        status.tooltip = _line(resource.error) if resource.error else None
        groups = {group.name: group for group in snapshot.groups} if snapshot else {}
        for widget in self.query(FileGroup):
            expand = (
                count <= 20
                or len(groups) == 1
                or widget.category_name in {"Implementation", "Tests"}
            )
            widget.show_group(groups.get(widget.category_name), expand=expand)
        self.query_one("#pr-files-summary-count", Static).update(
            f"{count} files changed" if snapshot else "Files changed"
        )
