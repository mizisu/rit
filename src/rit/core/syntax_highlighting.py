"""Stable syntax-highlighting interface backed by Lumis."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache

from textual.content import Content, Span

from rit import _lumis
from rit.core.highlight_theme import styles_for_mode

__all__ = ("highlight_code", "prewarm_highlighter")

_MAX_HIGHLIGHT_BYTES = 4 * 1024 * 1024
_MAX_HIGHLIGHT_LINE_BYTES = 256 * 1024
_MAX_HIGHLIGHT_SPANS = 250_000

def _style_for_scope(scope: str, styles: Mapping[str, str]) -> str | None:
    candidate = scope
    while candidate:
        if style := styles.get(candidate):
            return style
        candidate, _, _ = candidate.rpartition(".")
    return None


def _normalized_scope(source: bytes, end: int, scope: str) -> str:
    if scope != "property":
        return scope

    cursor = end
    while cursor < len(source) and source[cursor] in b" \t":
        cursor += 1
    if source[cursor : cursor + 1] == b"(":
        return "function.method"
    return scope


def _has_oversized_line(source: bytes) -> bool:
    if len(source) <= _MAX_HIGHLIGHT_LINE_BYTES:
        return False

    line_start = 0
    while True:
        line_end = source.find(b"\n", line_start)
        if line_end < 0:
            return len(source) - line_start > _MAX_HIGHLIGHT_LINE_BYTES
        if line_end - line_start > _MAX_HIGHLIGHT_LINE_BYTES:
            return True
        line_start = line_end + 1


def _normalize_source(source_code: str) -> str:
    if "\r" in source_code:
        return "\n".join(source_code.splitlines())
    if source_code.endswith("\n"):
        return source_code[:-1]
    return source_code


def _character_offsets(
    source: bytes,
    spans: list[tuple[int, int, str]],
) -> dict[int, int]:
    boundaries = sorted(
        {offset for start, end, _scope in spans for offset in (start, end)}
    )
    offsets: dict[int, int] = {}
    previous_byte = 0
    previous_character = 0
    for boundary in boundaries:
        previous_character += len(source[previous_byte:boundary].decode("utf-8"))
        offsets[boundary] = previous_character
        previous_byte = boundary
    return offsets


def highlight_code(
    source_code: str,
    *,
    path: str,
    dark_mode: bool,
) -> Content:
    """Highlight source through the active Lumis binding."""
    source_code = _normalize_source(source_code)
    plain_content = Content(source_code).stylize_before("$text")
    if not source_code:
        return plain_content

    try:
        source = source_code.encode("utf-8")
        if len(source) > _MAX_HIGHLIGHT_BYTES or _has_oversized_line(source):
            return plain_content

        semantic_spans = _lumis.highlight_spans(source_code, path)
        if len(semantic_spans) > _MAX_HIGHLIGHT_SPANS:
            return plain_content

        styles = styles_for_mode(dark_mode)
        styled_spans: list[tuple[int, int, str]] = []
        previous_end = 0
        for start, end, scope in semantic_spans:
            if not 0 <= previous_end <= start < end <= len(source):
                return plain_content
            previous_end = end
            normalized_scope = _normalized_scope(source, end, scope)
            if style := _style_for_scope(normalized_scope, styles):
                styled_spans.append((start, end, style))
        if source_code.isascii():
            spans = [Span(start, end, style) for start, end, style in styled_spans]
        else:
            offsets = _character_offsets(source, styled_spans)
            spans = [
                Span(offsets[start], offsets[end], style)
                for start, end, style in styled_spans
            ]
    except Exception:
        return plain_content

    return plain_content.add_spans(spans)


@cache
def prewarm_highlighter() -> None:
    """Initialize Lumis language and query data before the first diff render."""
    highlight_code(
        "def warmup():\n    return 1",
        path="warmup.py",
        dark_mode=True,
    )
