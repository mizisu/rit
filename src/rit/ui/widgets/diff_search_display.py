"""DiffView adapters for refreshing search match rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rit.ui.widgets.diff_search_policy import search_refresh_update

__all__ = ("refresh_search_display",)

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


def refresh_search_display(view: DiffView) -> None:
    """Refresh rendered lines affected by search match changes."""
    update = search_refresh_update(
        view._search_matches,
        previous_match_lines=view._prev_search_match_lines,
    )
    match_lines = set(update.dirty_lines)
    view._prev_search_match_lines = set(update.previous_match_lines)

    if not match_lines:
        return

    view._invalidate_base_code_content_cache(match_lines)

    from rit.ui.widgets import diff_blocks as _blocks

    if _blocks._refresh_grouped_blocks_for_lines(view, match_lines):
        return

    for line_idx in match_lines:
        view._update_line_cursor(line_idx)
