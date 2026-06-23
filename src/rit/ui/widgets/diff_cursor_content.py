"""Code content cursor styling for diff rendering."""

from __future__ import annotations

from textual.content import Content

__all__ = ("apply_cursor_to_code_content",)


def apply_cursor_to_code_content(
    content: Content,
    *,
    line_text: str,
    has_cursor: bool,
    cursor_col: int | None,
) -> Content:
    """Return code content with the cursor cell styled."""
    if not has_cursor or cursor_col is None:
        return content
    if not line_text:
        return Content(" ").stylize("reverse", 0, 1)
    if cursor_col >= len(line_text):
        return content
    return content.stylize("reverse", cursor_col, cursor_col + 1)
