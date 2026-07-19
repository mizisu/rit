from __future__ import annotations

import re
from dataclasses import dataclass

from rich._emoji_codes import EMOJI as _RICH_EMOJI
from textual.binding import Binding
from textual.widgets import OptionList, TextArea
from textual.widgets.option_list import Option, OptionDoesNotExist

EMOJI_PICKER_BINDINGS = [
    Binding("down", "emoji_next", "Next emoji", show=False, priority=True),
    Binding("up", "emoji_previous", "Previous emoji", show=False, priority=True),
    Binding("enter,tab", "emoji_accept", "Select emoji", show=False, priority=True),
    Binding("escape", "emoji_hide", "Hide emoji picker", show=False, priority=True),
]

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


class EmojiPicker(OptionList):
    """Emoji shortcode suggestions associated with a text area."""

    DEFAULT_CSS = """
    EmojiPicker {
        height: 6;
        max-height: 8;
        margin-bottom: 1;
    }

    EmojiPicker.-hidden {
        display: none;
    }
    """

    def __init__(self, *, id: str) -> None:
        super().__init__(id=id, classes="-hidden", compact=True, markup=False)

    @property
    def is_open(self) -> bool:
        """Whether suggestions are visible."""
        return not self.has_class("-hidden")

    def refresh_for(self, body: TextArea) -> None:
        """Refresh suggestions for the shortcode at the text cursor."""
        token = _emoji_token_at_cursor(body)
        if token is None:
            self.hide_picker()
            return
        suggestions = _emoji_suggestions_for_token(
            token,
            limit=_EMOJI_PREVIEW_LIMIT,
        )
        if not suggestions:
            self.hide_picker()
            return

        previous_id = _highlighted_option_id(self)
        self.clear_options()
        self.add_options(
            [
                Option(_format_emoji_option(suggestion), id=suggestion.name)
                for suggestion in suggestions
            ]
        )
        _restore_highlighted_emoji(self, previous_id, suggestions)
        self.remove_class("-hidden")

    def accept_highlighted(self, body: TextArea) -> None:
        """Insert the highlighted suggestion into the text area."""
        highlighted = self.highlighted_option
        if highlighted is None or highlighted.disabled:
            return
        self.accept(body, highlighted.id)

    def accept(self, body: TextArea, name: object | None) -> None:
        """Insert a named suggestion into the text area."""
        if not isinstance(name, str):
            return
        emoji = _emoji_for_name(name)
        if emoji is None:
            return
        token = _emoji_token_at_cursor(body)
        if token is None:
            return
        row, _column = body.cursor_location
        body.replace(
            emoji,
            (row, token.start_column),
            (row, token.end_column),
            maintain_selection_offset=False,
        )
        self.hide_picker()
        body.focus()

    def hide_picker(self) -> None:
        """Clear and hide the suggestions."""
        self.clear_options()
        self.add_class("-hidden")


def _emoji_token_at_cursor(body: TextArea) -> _EmojiToken | None:
    start, end = body.selection
    if start != end:
        return None
    row, column = body.cursor_location
    lines = body.document.lines
    if row < 0 or row >= len(lines):
        return None
    return _emoji_token_for_line(lines[row], column)


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
