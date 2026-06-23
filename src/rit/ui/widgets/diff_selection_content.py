"""Code content selection styling for visual mode."""

from __future__ import annotations

from textual.content import Content

__all__ = ("apply_selection_to_code_content",)


def apply_selection_to_code_content(
    content: Content,
    *,
    line_text: str,
    selection_start: int,
    selection_end: int,
    has_cursor: bool,
    cursor_col: int | None,
) -> Content:
    """Return code content with visual selection styling applied."""
    if not line_text:
        return content

    max_col = len(line_text) - 1
    selection_start = max(0, min(selection_start, max_col))
    selection_end = max(0, min(selection_end, max_col))

    if selection_start > selection_end:
        selection_start, selection_end = selection_end, selection_start

    result = content.stylize("reverse dim", selection_start, selection_end + 1)

    if has_cursor and cursor_col is not None and cursor_col < len(line_text):
        result = result.stylize("reverse bold", cursor_col, cursor_col + 1)

    return result
