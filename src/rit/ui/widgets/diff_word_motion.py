"""Word boundary policy for diff cursor movement."""

from __future__ import annotations

__all__ = (
    "first_word_start",
    "is_word_char",
    "last_word_start",
    "next_word_end",
    "next_word_start",
    "previous_word_start",
)


def is_word_char(char: str) -> bool:
    """Return whether a character belongs to a word token."""
    return char.isalnum() or char == "_"


def first_word_start(text: str) -> int:
    """Return the first non-whitespace token start, or len(text)."""
    pos = 0
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def next_word_start(text: str, pos: int) -> int | None:
    """Return the next token start after pos."""
    if pos >= len(text) - 1:
        return None
    current_pos = pos
    if is_word_char(text[current_pos]):
        while current_pos < len(text) and is_word_char(text[current_pos]):
            current_pos += 1
    elif not text[current_pos].isspace():
        while (
            current_pos < len(text)
            and not text[current_pos].isspace()
            and not is_word_char(text[current_pos])
        ):
            current_pos += 1
    while current_pos < len(text) and text[current_pos].isspace():
        current_pos += 1
    return current_pos if current_pos < len(text) else None


def previous_word_start(text: str, pos: int) -> int | None:
    """Return the previous token start before pos."""
    if pos <= 0:
        return None
    current_pos = pos - 1
    while current_pos > 0 and text[current_pos].isspace():
        current_pos -= 1
    if is_word_char(text[current_pos]):
        while current_pos > 0 and is_word_char(text[current_pos - 1]):
            current_pos -= 1
    else:
        while (
            current_pos > 0
            and not text[current_pos - 1].isspace()
            and not is_word_char(text[current_pos - 1])
        ):
            current_pos -= 1
    return current_pos


def next_word_end(text: str, pos: int) -> int | None:
    """Return the current or next token end after pos."""
    if pos >= len(text) - 1:
        return None
    current_pos = pos + 1
    while current_pos < len(text) and text[current_pos].isspace():
        current_pos += 1
    if current_pos >= len(text):
        return None
    if is_word_char(text[current_pos]):
        while current_pos < len(text) - 1 and is_word_char(text[current_pos + 1]):
            current_pos += 1
    else:
        while (
            current_pos < len(text) - 1
            and not text[current_pos + 1].isspace()
            and not is_word_char(text[current_pos + 1])
        ):
            current_pos += 1
    return current_pos


def last_word_start(text: str) -> int | None:
    """Return the final token start in text."""
    current_pos = len(text) - 1
    while current_pos >= 0 and text[current_pos].isspace():
        current_pos -= 1
    if current_pos < 0:
        return None
    if is_word_char(text[current_pos]):
        while current_pos > 0 and is_word_char(text[current_pos - 1]):
            current_pos -= 1
    else:
        while (
            current_pos > 0
            and not text[current_pos - 1].isspace()
            and not is_word_char(text[current_pos - 1])
        ):
            current_pos -= 1
    return current_pos
