from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rit.state.models import PRFile

MAX_VISIBLE_MATCHES = 200
MATCH_STYLE = "bold cyan"


@dataclass(frozen=True)
class FilePickerMatch:
    """Ranked file match for the go-to-file picker."""

    file: PRFile
    index: int
    score: float
    positions: frozenset[int]


def rank_file_matches(files: list[PRFile], query: str) -> list[FilePickerMatch]:
    """Return files ranked for fuzzy go-to-file matching."""
    terms = [term for term in query.casefold().split() if term]
    if not terms:
        return [
            FilePickerMatch(
                file=file,
                index=index,
                score=0,
                positions=frozenset(),
            )
            for index, file in enumerate(files)
        ]

    matches: list[FilePickerMatch] = []
    for index, file in enumerate(files):
        result = _score_filename(file.filename, terms)
        if result is None:
            continue
        score, positions = result
        matches.append(
            FilePickerMatch(
                file=file,
                index=index,
                score=score,
                positions=frozenset(positions),
            )
        )

    return sorted(matches, key=lambda match: (-match.score, match.index))


def _score_filename(
    filename: str,
    terms: list[str],
) -> tuple[float, set[int]] | None:
    haystack = filename.casefold()
    basename_start = filename.rfind("/") + 1
    basename = filename[basename_start:].casefold()
    basename_stem = basename.rsplit(".", 1)[0]

    matched_positions: set[int] = set()
    score = 0.0
    for term in terms:
        positions = _match_term(haystack, term)
        if positions is None:
            return None
        matched_positions.update(positions)
        score += _term_score(
            filename=filename,
            haystack=haystack,
            basename=basename,
            basename_stem=basename_stem,
            basename_start=basename_start,
            term=term,
            positions=positions,
        )

    score += _path_coverage_bonus(filename, terms)

    spread = max(matched_positions) - min(matched_positions) if matched_positions else 0
    score -= spread * 0.25
    score -= len(filename) * 0.03
    return score, matched_positions


def _match_term(haystack: str, term: str) -> list[int] | None:
    start = haystack.find(term)
    if start >= 0:
        return list(range(start, start + len(term)))

    positions: list[int] = []
    cursor = 0
    for char in term:
        found = haystack.find(char, cursor)
        if found < 0:
            return None
        positions.append(found)
        cursor = found + 1
    return positions


def _term_score(
    *,
    filename: str,
    haystack: str,
    basename: str,
    basename_stem: str,
    basename_start: int,
    term: str,
    positions: list[int],
) -> float:
    score = len(term) * 10.0
    if term in haystack:
        score += 120
    if term in basename:
        score += 240
    if basename.startswith(term):
        score += 420
    if basename_stem == term:
        score += 1300

    score += _segment_bonus(filename, term)

    previous = None
    for position in positions:
        if position >= basename_start:
            score += 22
        if position == 0 or filename[position - 1] in "/_.-":
            score += 36
        if previous is not None and position == previous + 1:
            score += 18
        previous = position

    return score


def _segment_bonus(filename: str, term: str) -> float:
    score = 0.0
    segments = filename.casefold().split("/")
    for index, segment in enumerate(segments):
        is_basename = index == len(segments) - 1
        pieces = [segment]
        if "." in segment:
            pieces.append(segment.rsplit(".", 1)[0])
        for piece in pieces:
            if piece == term:
                score += 1300 if is_basename else 900
            elif piece.startswith(term):
                score += 540 if is_basename else 460
            elif term in piece:
                score += 260 if is_basename else 220
    return score


def _path_coverage_bonus(filename: str, terms: list[str]) -> float:
    if len(terms) < 2:
        return 0.0

    term_segments = [_matching_segment_indexes(filename, term) for term in terms]
    if any(not indexes for indexes in term_segments):
        return 0.0

    covered_segments = set().union(*term_segments)
    if len(covered_segments) < 2:
        return 0.0

    return 1000.0 * (len(covered_segments) - 1)


def _matching_segment_indexes(filename: str, term: str) -> set[int]:
    indexes: set[int] = set()
    for index, segment in enumerate(filename.casefold().split("/")):
        pieces = [segment]
        if "." in segment:
            pieces.append(segment.rsplit(".", 1)[0])
        if any(term in piece for piece in pieces):
            indexes.add(index)
    return indexes


class FilePickerScreen(ModalScreen[str | None]):
    """Fuzzy file picker for fast diff navigation."""

    DEFAULT_CSS = """
    FilePickerScreen {
        align: center middle;
    }

    #file-picker-dialog {
        width: 96;
        max-width: 94%;
        height: auto;
        max-height: 88%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    #file-picker-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #file-picker-search {
        margin-bottom: 1;
    }

    #file-picker-options {
        height: 16;
        min-height: 8;
        max-height: 22;
        margin-bottom: 1;
    }

    #file-picker-count {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Next", show=False),
        Binding("down", "cursor_down", "Next", show=False),
        Binding("k", "cursor_up", "Prev", show=False),
        Binding("up", "cursor_up", "Prev", show=False),
        Binding("enter", "submit", "Open", show=False),
        Binding("tab", "focus_next", "Next Field", show=False),
        Binding("shift+tab", "focus_prev", "Prev Field", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        *,
        files: list[PRFile],
        selected_file: str | None,
        total_count: int | None = None,
    ) -> None:
        super().__init__()
        self._files = list(files)
        self._selected_file = selected_file
        self._total_count = total_count or len(files)
        self._query = ""
        self._matches: list[FilePickerMatch] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="file-picker-dialog"):
            yield Static("Go to file", id="file-picker-title")
            yield Input(placeholder="Search files", id="file-picker-search")
            yield OptionList(id="file-picker-options")
            yield Static("", id="file-picker-count")

    def on_mount(self) -> None:
        self._refresh_options()
        self.query_one("#file-picker-search", Input).focus()

    @staticmethod
    def option_prompt(match: FilePickerMatch, *, total_count: int) -> Text:
        text = Text()
        text.append(f"{match.index + 1}/{total_count} ", style="dim")
        _append_highlighted_filename(text, match.file.filename, match.positions)
        text.append("  ")
        text.append(f"+{match.file.additions}", style="green")
        text.append(" ")
        text.append(f"-{match.file.deletions}", style="red")
        return text

    def _refresh_options(self) -> None:
        options = self.query_one("#file-picker-options", OptionList)
        previous_id = self._highlighted_option_id(options)
        self._matches = rank_file_matches(self._files, self._query)

        visible_matches = self._visible_matches()
        options.clear_options()
        if not visible_matches:
            options.add_option(Option("No matching files", id="empty", disabled=True))
            self._update_count()
            return

        options.add_options(
            [
                Option(
                    self.option_prompt(match, total_count=self._total_count),
                    id=match.file.filename,
                )
                for match in visible_matches
            ]
        )
        self._restore_highlight(options, previous_id)
        self._update_count()

    def _visible_matches(self) -> list[FilePickerMatch]:
        matches = self._matches[:MAX_VISIBLE_MATCHES]
        if self._query or not self._selected_file:
            return matches

        selected = next(
            (
                match
                for match in self._matches
                if match.file.filename == self._selected_file
            ),
            None,
        )
        if selected is None or selected in matches:
            return matches
        return [selected, *matches[: MAX_VISIBLE_MATCHES - 1]]

    def _highlighted_option_id(self, options: OptionList) -> str | None:
        highlighted = options.highlighted_option
        return highlighted.id if highlighted is not None else None

    def _restore_highlight(
        self,
        options: OptionList,
        previous_id: str | None,
    ) -> None:
        for option_id in (previous_id, self._selected_file):
            if option_id is None:
                continue
            try:
                options.highlighted = options.get_option_index(option_id)
                return
            except Exception:
                pass
        options.action_first()

    def _update_count(self) -> None:
        shown = min(len(self._matches), MAX_VISIBLE_MATCHES)
        total = len(self._matches)
        if total > shown:
            label = f"{shown}/{total} matches"
        else:
            label = f"{total} match" if total == 1 else f"{total} matches"
        self.query_one("#file-picker-count", Static).update(label)

    @on(Input.Changed, "#file-picker-search")
    def on_search_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._query = event.value
        self._refresh_options()

    @on(Input.Submitted, "#file-picker-search")
    def on_search_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    @on(OptionList.OptionSelected, "#file-picker-options")
    def on_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id and event.option_id != "empty":
            self.dismiss(event.option_id)

    def action_cursor_down(self) -> None:
        options = self.query_one("#file-picker-options", OptionList)
        options.focus()
        options.action_cursor_down()

    def action_cursor_up(self) -> None:
        options = self.query_one("#file-picker-options", OptionList)
        options.focus()
        options.action_cursor_up()

    def action_focus_next(self) -> None:
        search = self.query_one("#file-picker-search", Input)
        options = self.query_one("#file-picker-options", OptionList)
        if search.has_focus:
            options.focus()
        else:
            search.focus()

    def action_focus_prev(self) -> None:
        self.action_focus_next()

    def action_submit(self) -> None:
        options = self.query_one("#file-picker-options", OptionList)
        highlighted = options.highlighted_option
        if highlighted is None or highlighted.disabled or highlighted.id is None:
            return
        if highlighted.id == "empty":
            return
        self.dismiss(highlighted.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _append_highlighted_filename(
    text: Text,
    filename: str,
    positions: frozenset[int],
) -> None:
    if not positions:
        text.append(filename)
        return

    for index, char in enumerate(filename):
        style = MATCH_STYLE if index in positions else ""
        text.append(char, style=style)
