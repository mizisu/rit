from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from rich._emoji_codes import EMOJI as _RICH_EMOJI
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option, OptionDoesNotExist

EditorKind = Literal["issue", "inline"]
SubmitMode = Literal["queue", "post"]

_EMOJI_CLOSED_RE = re.compile(r":([A-Za-z0-9_+\-]+):$")
_EMOJI_OPEN_RE = re.compile(r":([A-Za-z0-9_+\-]*)$")
_EMOJI_BOUNDARY_CHARS = frozenset(" \t([{<")
_EMOJI_NAMES = tuple(sorted(_RICH_EMOJI))
_EMOJI_PREVIEW_LIMIT = 6
_POPULAR_EMOJI_NAMES = ("rocket", "eyes", "white_check_mark", "tada", "heart", "+1")


@dataclass(frozen=True, slots=True)
class _EmojiToken:
    start_column: int
    end_column: int
    name: str
    closed: bool


@dataclass(frozen=True, slots=True)
class _EmojiSuggestion:
    name: str
    emoji: str


__all__ = (
    "EditorKind",
    "InlineCommentEditor",
    "SubmitMode",
)


class InlineCommentEditor(Vertical):
    """Inline comment editor shared by PR and file views."""

    DEFAULT_CSS = """
    InlineCommentEditor {
        height: auto;
        border: solid $primary;
        padding: 1;
        margin: 1 0;
        background: $surface;
    }

    InlineCommentEditor.-hidden {
        display: none;
    }

    InlineCommentEditor .comment-editor-title {
        text-style: bold;
        margin-bottom: 1;
    }

    InlineCommentEditor .comment-editor-body {
        height: 6;
        min-height: 4;
        max-height: 12;
        margin-bottom: 1;
    }

    InlineCommentEditor #comment-editor-emoji-options {
        height: 6;
        max-height: 8;
        margin-bottom: 1;
    }

    InlineCommentEditor #comment-editor-emoji-options.-hidden {
        display: none;
    }
    """

    BINDINGS = [
        Binding("down", "emoji_next", "Next emoji", show=False, priority=True),
        Binding("up", "emoji_previous", "Previous emoji", show=False, priority=True),
        Binding("enter,tab", "emoji_accept", "Select emoji", show=False, priority=True),
        Binding("escape", "emoji_hide", "Hide emoji picker", show=False, priority=True),
        Binding("ctrl+s,ctrl+enter", "submit('queue')", "Save draft", show=False),
        Binding(
            "ctrl+shift+s,ctrl+shift+enter",
            "submit('post')",
            "Post now",
            show=False,
        ),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    @dataclass
    class Submitted(Message):
        kind: EditorKind
        body: str
        mode: SubmitMode = "queue"

    @dataclass
    class Cancelled(Message):
        kind: EditorKind

    def __init__(
        self,
        *,
        kind: EditorKind,
        title: str,
        placeholder: str,
        initial_text: str = "",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="-hidden")
        self._kind: EditorKind = kind
        self._title = title
        self._placeholder = placeholder
        self._initial_text = initial_text
        self._pending_focus = False

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="comment-editor-title")
        yield TextArea(
            id="comment-editor-body",
            classes="comment-editor-body",
            soft_wrap=True,
            show_line_numbers=False,
            placeholder=self._placeholder,
        )
        yield OptionList(
            id="comment-editor-emoji-options",
            classes="-hidden",
            compact=True,
            markup=False,
        )
        if self._kind == "inline":
            hint = "Ctrl+S pending • Ctrl+Shift+S post now • Esc cancel"
        else:
            hint = "Ctrl+S submit • Esc cancel"
        yield Static(hint)

    def on_mount(self) -> None:
        if self._pending_focus:
            self._pending_focus = False
            self._focus_body()

    def _focus_body(self) -> None:
        body = self.query_one("#comment-editor-body", TextArea)
        body.text = self._initial_text
        self.remove_class("-hidden")
        body.focus()

    def open(self, initial_text: str | None = None) -> None:
        if initial_text is not None:
            self._initial_text = initial_text
        if not self.is_mounted:
            self._pending_focus = True
            self.remove_class("-hidden")
            return
        self._focus_body()

    def close(self) -> None:
        if self.is_mounted:
            body = self.query_one("#comment-editor-body", TextArea)
            body.text = ""
        self._initial_text = ""
        self.add_class("-hidden")
        self.screen.set_focus(None)

    @property
    def is_open(self) -> bool:
        return not self.has_class("-hidden")

    def action_submit(self, mode: SubmitMode = "queue") -> None:
        body = self.query_one("#comment-editor-body", TextArea).text.strip()
        if not body:
            self.notify("Comment cannot be empty", severity="warning")
            return
        self.post_message(self.Submitted(self._kind, body, mode))

    @on(TextArea.Changed, "#comment-editor-body")
    def _on_body_changed(self, event: TextArea.Changed) -> None:
        self._refresh_emoji_options(event.text_area)

    @on(TextArea.SelectionChanged, "#comment-editor-body")
    def _on_body_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._refresh_emoji_options(event.text_area)

    @on(OptionList.OptionSelected, "#comment-editor-emoji-options")
    def _on_emoji_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._accept_emoji_name(event.option_id)

    def action_emoji_next(self) -> None:
        options = self._emoji_options_widget()
        options.action_cursor_down()

    def action_emoji_previous(self) -> None:
        options = self._emoji_options_widget()
        options.action_cursor_up()

    def action_emoji_accept(self) -> None:
        highlighted = self._emoji_options_widget().highlighted_option
        if highlighted is None or highlighted.disabled:
            return
        self._accept_emoji_name(highlighted.id)

    def action_emoji_hide(self) -> None:
        self._hide_emoji_options()
        self.query_one("#comment-editor-body", TextArea).focus()

    def _refresh_emoji_options(self, body: TextArea) -> None:
        token = self._emoji_token_at_cursor(body)
        if token is None:
            self._hide_emoji_options()
            return
        suggestions = _emoji_suggestions_for_token(
            token,
            limit=_EMOJI_PREVIEW_LIMIT,
        )
        if not suggestions:
            self._hide_emoji_options()
            return

        options = self._emoji_options_widget()
        previous_id = _highlighted_option_id(options)
        options.clear_options()
        options.add_options(
            [
                Option(_format_emoji_option(suggestion), id=suggestion.name)
                for suggestion in suggestions
            ]
        )
        _restore_highlighted_emoji(options, previous_id, suggestions)
        options.remove_class("-hidden")

    def _accept_emoji_name(self, name: object | None) -> None:
        if not isinstance(name, str):
            return
        emoji = _emoji_for_name(name)
        if emoji is None:
            return
        body = self.query_one("#comment-editor-body", TextArea)
        token = self._emoji_token_at_cursor(body)
        if token is None:
            return
        row, _column = body.cursor_location
        body.replace(
            emoji,
            (row, token.start_column),
            (row, token.end_column),
            maintain_selection_offset=False,
        )
        self._hide_emoji_options()
        body.focus()

    def _hide_emoji_options(self) -> None:
        if not self.is_mounted:
            return
        options = self._emoji_options_widget()
        options.clear_options()
        options.add_class("-hidden")

    def _emoji_options_widget(self) -> OptionList:
        return self.query_one("#comment-editor-emoji-options", OptionList)

    def _emoji_options_visible(self) -> bool:
        if not self.is_mounted:
            return False
        return not self._emoji_options_widget().has_class("-hidden")

    def _emoji_token_at_cursor(self, body: TextArea) -> _EmojiToken | None:
        start, end = body.selection
        if start != end:
            return None
        row, column = body.cursor_location
        lines = body.document.lines
        if row < 0 or row >= len(lines):
            return None
        return _emoji_token_for_line(lines[row], column)

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled(self._kind))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("emoji_"):
            return self._emoji_options_visible()
        return super().check_action(action, parameters)


def _emoji_token_for_line(line: str, cursor_column: int) -> _EmojiToken | None:
    if cursor_column < 0 or cursor_column > len(line):
        return None
    prefix = line[:cursor_column]
    match = _EMOJI_CLOSED_RE.search(prefix)
    closed = match is not None
    if match is None:
        match = _EMOJI_OPEN_RE.search(prefix)
    if match is None:
        return None
    if match.start() > 0 and line[match.start() - 1] not in _EMOJI_BOUNDARY_CHARS:
        return None
    return _EmojiToken(
        start_column=match.start(),
        end_column=cursor_column,
        name=match.group(1),
        closed=closed,
    )


def _resolve_emoji_name(name: str) -> str | None:
    lowered = name.lower()
    if lowered in _RICH_EMOJI:
        return lowered
    normalized = lowered.replace("-", "_")
    if normalized in _RICH_EMOJI:
        return normalized
    return None


def _emoji_for_name(name: str) -> str | None:
    resolved = _resolve_emoji_name(name)
    if resolved is None:
        return None
    return _RICH_EMOJI[resolved]


def _emoji_suggestions_for_token(
    token: _EmojiToken,
    *,
    limit: int,
) -> list[_EmojiSuggestion]:
    if token.closed:
        resolved = _resolve_emoji_name(token.name)
        if resolved is None:
            return []
        return [_EmojiSuggestion(name=resolved, emoji=_RICH_EMOJI[resolved])]
    return _emoji_suggestions(token.name, limit=limit)


def _emoji_suggestions(query: str, *, limit: int) -> list[_EmojiSuggestion]:
    names = _popular_emoji_names() if not query else _matching_emoji_names(query)
    suggestions: list[_EmojiSuggestion] = []
    seen: set[str] = set()
    for name in names:
        resolved = _resolve_emoji_name(name)
        if resolved is None or resolved in seen:
            continue
        suggestions.append(_EmojiSuggestion(name=resolved, emoji=_RICH_EMOJI[resolved]))
        seen.add(resolved)
        if len(suggestions) >= limit:
            break
    return suggestions


def _popular_emoji_names() -> tuple[str, ...]:
    return _POPULAR_EMOJI_NAMES


def _matching_emoji_names(query: str) -> tuple[str, ...]:
    lowered = query.lower()
    normalized = lowered.replace("-", "_")
    exact = [name for name in _EMOJI_NAMES if name in {lowered, normalized}]
    prefix = [
        name
        for name in _EMOJI_NAMES
        if name not in exact and name.replace("-", "_").startswith(normalized)
    ]
    contains = [
        name
        for name in _EMOJI_NAMES
        if name not in exact
        and name not in prefix
        and normalized in name.replace("-", "_")
    ]
    return tuple(exact + prefix + contains)


def _highlighted_option_id(options: OptionList) -> str | None:
    highlighted = options.highlighted_option
    if highlighted is None or not isinstance(highlighted.id, str):
        return None
    return highlighted.id


def _restore_highlighted_emoji(
    options: OptionList,
    previous_id: str | None,
    suggestions: list[_EmojiSuggestion],
) -> None:
    option_ids = [previous_id, suggestions[0].name]
    for option_id in option_ids:
        if option_id is None:
            continue
        try:
            options.highlighted = options.get_option_index(option_id)
            return
        except OptionDoesNotExist:
            pass
    options.action_first()


def _format_emoji_option(suggestion: _EmojiSuggestion) -> str:
    return f"{suggestion.emoji} :{suggestion.name}:"
