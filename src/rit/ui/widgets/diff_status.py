"""Status-line text construction for DiffView."""

from __future__ import annotations

from rich.markup import escape

__all__ = ("build_status_line",)


def build_status_line(
    header_text: str,
    *,
    search_query: str,
    search_match_count: int,
    search_match_index: int,
) -> str:
    """Return header text with an optional search status suffix."""
    query = search_query.strip()
    if not query:
        return header_text

    escaped_query = escape(query)
    active_match = (
        search_match_index + 1
        if 0 <= search_match_index < search_match_count
        else 0
    )
    if search_match_count == 0:
        suffix = f'[$warning]search "{escaped_query}" no matches[/]'
    else:
        suffix = f'[dim]search "{escaped_query}" {active_match}/{search_match_count}[/]'
    return f"{header_text}  {suffix}"
