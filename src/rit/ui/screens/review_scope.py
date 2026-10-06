"""An anchored review-scope popover that leaves the diff visible."""

from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.geometry import Region
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rit.state.models import LoadingState
from rit.state.review_scope import PRCommit, ReviewHistory, ReviewScope
from rit.state.store import PRStore


class ReviewScopePicker(ModalScreen[ReviewScope | None]):
    """Choose one scope or a contiguous first-parent commit span."""

    DEFAULT_CSS = """
    ReviewScopePicker { background: transparent; }
    #review-scope-dialog {
        width: 76; height: auto;
        background: $surface; border: round $primary 60%; padding: 0 1;
        border-title-color: $text; border-title-style: bold;
    }
    #review-scope-search {
        height: 2; min-height: 2; margin: 0; padding: 0;
        border: none; border-bottom: solid $primary 20%;
        background: transparent;
    }
    #review-scope-search:focus { border-bottom: solid $primary; }
    #review-scope-options {
        height: 10; min-height: 1; border: none; padding: 0;
        background: transparent; background-tint: transparent;
        scrollbar-size-vertical: 1;
    }
    #review-scope-options > .option-list--option-disabled { color: $text-muted; }
    #review-scope-options > .option-list--option-highlighted {
        background: $primary 20%; color: $text; text-style: bold;
    }
    #review-scope-options > .option-list--option-hover { background: $primary 10%; }
    #review-scope-preview {
        height: 3; border-top: solid $primary 20%; color: $text-muted;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
    #review-scope-help, #review-scope-error {
        height: 1; color: $text-muted;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
    #review-scope-error { display: none; color: $warning; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("enter", "submit", "Select", show=False),
        Binding("j", "move(1)", "Next", show=False),
        Binding("k", "move(-1)", "Previous", show=False),
        Binding("down", "move(1)", "Next", show=False, priority=True),
        Binding("up", "move(-1)", "Previous", show=False, priority=True),
        Binding("shift+down", "extend(1)", "Extend", show=False),
        Binding("shift+up", "extend(-1)", "Extend", show=False),
        Binding("/", "search", "Search", show=False),
        Binding("ctrl+r", "refresh", "Refresh", show=False),
        Binding("tab", "focus_next", "Next field", show=False, priority=True),
        Binding(
            "shift+tab", "focus_previous", "Previous field", show=False, priority=True
        ),
    ]

    def __init__(self, store: PRStore, anchor: Region) -> None:
        super().__init__()
        self.store = store
        self._anchor_region = anchor
        self.history: ReviewHistory | None = None
        self._range_anchor: str | None = None
        self._range_end: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="review-scope-dialog") as dialog:
            dialog.border_title = "Review scope"
            yield Input(
                placeholder="Search commits by title or SHA…  /",
                id="review-scope-search",
            )
            yield OptionList(id="review-scope-options", compact=True)
            yield Static("", id="review-scope-preview", markup=False)
            yield Static("", id="review-scope-error", markup=False)
            yield Static(
                "↑↓ Move  Enter Apply  Shift+↑↓ Range  Esc Close",
                id="review-scope-help",
                markup=False,
            )

    def on_mount(self) -> None:
        self._refresh_options()
        self.query_one(OptionList).focus()
        self.run_worker(self._load_history(), group="scope-history", exclusive=True)

    def on_resize(self) -> None:
        self._position_dialog()

    def _position_dialog(self) -> None:
        dialog = self.query_one("#review-scope-dialog")
        options = self.query_one(OptionList)
        width = min(76, max(1, self.size.width - 2))
        overhead = 8 + int(self.query_one("#review-scope-error").display)
        desired = min(10, max(1, options.option_count)) + overhead
        below = max(0, self.size.height - self._anchor_region.bottom - 1)
        above = max(0, self._anchor_region.y - 1)
        minimum = min(desired, overhead + 4)
        if below >= minimum:
            available, y = below, self._anchor_region.bottom
        elif above >= minimum:
            available, y = above, self._anchor_region.y - min(desired, above)
        else:
            available = max(1, self.size.height - 2)
            y = max(
                1,
                min(
                    self._anchor_region.bottom,
                    self.size.height - min(desired, available) - 1,
                ),
            )
        height = min(desired, available)
        options.styles.height = max(1, height - overhead)
        dialog.styles.width = width
        dialog.styles.max_height = max(1, self.size.height - 2)
        dialog.styles.offset = (
            max(0, min(self._anchor_region.x, self.size.width - width - 1)),
            y,
        )

    async def _load_history(self) -> None:
        await self.store.load_review_history(refresh=True)
        if not self.is_mounted:
            return
        resource = self.store.state.review_history
        self.history = resource.loaded_value
        self._set_error(resource.error or "")
        self._refresh_options()

    def action_refresh(self) -> None:
        self._range_anchor = self._range_end = None
        self.history = None
        self._set_error("")
        self._refresh_options()
        self.run_worker(self._load_history(), group="scope-history", exclusive=True)

    def _since_unavailable(self) -> str:
        if self.history is not None:
            try:
                self.history.since_review()
            except ValueError as error:
                return str(error)
            return ""
        if self.store.state.review_history.loading == LoadingState.ERROR:
            return "Review history unavailable · Ctrl+R to retry"
        return "Loading review history…"

    @staticmethod
    def _scope_row(label: str, selected: bool, note: str) -> Table:
        row = Table.grid(expand=True, padding=0)
        row.add_column(width=2, no_wrap=True)
        row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        row.add_column(justify="right", no_wrap=True, overflow="ellipsis")
        row.add_row("✓ " if selected else "  ", Text(label), Text(note, style="dim"))
        return row

    def _commit_row(self, commit: PRCommit, selected: bool) -> Table:
        row = Table.grid(expand=True, padding=0)
        row.add_column(width=2, no_wrap=True)
        row.add_column(width=9, no_wrap=True)
        row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        row.add_column(width=20, justify="right", no_wrap=True)
        badges: list[str] = []
        if self.history is not None:
            if commit.sha == self.history.head_sha:
                badges.append("HEAD")
            review = self.history.last_review
            if review is not None and commit.sha == review.commit_sha:
                badges.append("LAST REVIEW")
        row.add_row(
            "✓ " if selected else "  ",
            Text(commit.sha[:7], style="dim"),
            Text(commit.title, no_wrap=True, overflow="ellipsis"),
            Text(" · ".join(badges), style="bold"),
        )
        return row

    def _refresh_options(self) -> None:
        options = self.query_one(OptionList)
        highlighted = options.highlighted_option
        previous = highlighted.id if highlighted is not None else None
        active = self.store.state.scope
        query = self.query_one(Input).value.strip().casefold()
        reason = self._since_unavailable()
        note = "Read-only"
        if self.history is None:
            note = (
                "Unavailable" if self.store.state.review_history.error else "Loading…"
            )
        elif reason:
            note = (
                "No review yet" if self.history.last_review is None else "No checkpoint"
            )
        elif (
            self.history.last_review
            and self.history.last_review.commit_sha == self.history.head_sha
        ):
            note = "Up to date"
        rows = [
            Option(
                self._scope_row(
                    "All changes", active.kind == "all" and not self._range_anchor, "Full PR"
                ),
                id="all",
            ),
            Option(
                self._scope_row(
                    "Since last review", active.kind == "since" and not self._range_anchor, note
                ),
                id="since",
                disabled=bool(reason),
            ),
            Option(
                Text("Commits · newest first", no_wrap=True, overflow="ellipsis"),
                id="commits",
                disabled=True,
            ),
        ]
        selected = (
            set(active.commit_shas) if active.kind in {"commit", "range"} else set()
        )
        if self._range_anchor and self._range_end and self.history:
            try:
                selected = set(
                    self.history.select_commits(
                        self._range_anchor, self._range_end
                    ).commit_shas
                )
            except ValueError:
                selected = set()
        count = 0
        if self.history:
            for commit in reversed(self.history.commits):
                if query and query not in f"{commit.sha} {commit.title}".casefold():
                    continue
                rows.append(
                    Option(
                        self._commit_row(commit, commit.sha in selected), id=commit.sha
                    )
                )
                count += 1
        if not count:
            label = "No matching commits" if self.history else "Loading commits…"
            if self.store.state.review_history.error and self.history is None:
                label = "Commits unavailable · Ctrl+R to retry"
            rows.append(
                Option(Text(label, no_wrap=True, overflow="ellipsis"), disabled=True)
            )
        options.set_options(rows)
        ids = {option.id for option in rows if not option.disabled}
        target = (
            previous
            if previous in ids
            else active.head_sha
            if active.kind == "commit"
            else active.kind
        )
        options.highlighted = options.get_option_index(
            target if target is not None and target in ids else "all"
        )
        self._refresh_preview()
        self._position_dialog()

    def _refresh_preview(self) -> None:
        option = self.query_one(OptionList).highlighted_option
        key = option.id if option is not None else None
        detail = "All PR changes against the base branch.\nComments and Viewed are available."
        if self.history and self._range_anchor and self._range_end:
            try:
                scope = self.history.select_commits(self._range_anchor, self._range_end)
                detail = f"{scope.label} · {scope.title}\n{scope.base_sha[:7]} → {scope.head_sha[:7]} · Read-only"
            except ValueError as error:
                detail = str(error)
        elif key == "since":
            detail = (
                self._since_unavailable()
                or "From your last submitted review to HEAD.\nRead-only · Comments and Viewed stay in All changes."
            )
            if (
                self.history
                and self.history.last_review
                and self.history.last_review.commit_sha == self.history.head_sha
            ):
                detail = "No new changes since your last review.\nYour last review already covers HEAD."
        elif self.history:
            commit = next(
                (commit for commit in self.history.commits if commit.sha == key), None
            )
            if commit:
                note = " · Merge, first parent" if commit.parent_count > 1 else ""
                detail = f"{commit.title}\n{commit.parent_sha[:7]} → {commit.sha[:7]} · Read-only{note}"
        preview = self.query_one("#review-scope-preview", Static)
        preview.update(detail)
        preview.tooltip = detail

    def _set_error(self, error: str) -> None:
        widget = self.query_one("#review-scope-error", Static)
        widget.update(error)
        widget.tooltip = error
        widget.display = bool(error)
        self._position_dialog()

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self._refresh_preview()

    @on(Input.Changed)
    def _filter_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._range_anchor = self._range_end = None
        self._refresh_options()

    @on(Input.Submitted)
    def _search_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one(OptionList).focus()

    def action_search(self) -> None:
        self.query_one(Input).focus()

    def action_move(self, direction: int) -> None:
        self._range_anchor = self._range_end = None
        options = self.query_one(OptionList)
        if direction > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()
        self._refresh_options()

    def action_extend(self, direction: int) -> None:
        if self.query_one(Input).value.strip():
            self._set_error("Clear search to select a contiguous range")
            return
        options = self.query_one(OptionList)
        current = options.highlighted_option
        if (
            self.history is None
            or current is None
            or current.id is None
            or current.id not in {c.sha for c in self.history.commits}
        ):
            return
        self._range_anchor = self._range_anchor or current.id
        if direction > 0:
            options.action_cursor_down()
        else:
            options.action_cursor_up()
        target = options.highlighted_option
        if target and target.id in {c.sha for c in self.history.commits}:
            self._range_end = target.id
        else:
            options.highlighted = options.get_option_index(current.id)
        self._refresh_options()

    def action_submit(self) -> None:
        option = self.query_one(OptionList).highlighted_option
        if option is not None and not option.disabled and option.id is not None:
            self._select(option.id)

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id:
            self._select(event.option_id)

    def _select(self, key: str) -> None:
        try:
            if key == "all":
                scope = ReviewScope()
            elif self.history is None:
                return
            elif key == "since":
                scope = self.history.since_review()
            else:
                scope = self.history.select_commits(self._range_anchor or key, key)
        except ValueError as error:
            self._set_error(str(error))
            return
        self.dismiss(scope)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:
            self.dismiss(None)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.focused, Input) and action in {
            "move",
            "extend",
            "search",
            "submit",
        }:
            return False
        return super().check_action(action, parameters)
