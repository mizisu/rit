"""Header text construction for DiffView."""

from __future__ import annotations

from rich.cells import cell_len
from rich.markup import escape
from rich.text import Text

from rit.core.types import FileDiff
from rit.state.models import FileViewedState, PRFile

FILE_HEADER_CHROME_WIDTH = 8

__all__ = (
    "FILE_HEADER_CHROME_WIDTH",
    "aggregate_file_change_stats",
    "append_change_stats",
    "build_diff_header_text",
    "build_file_header_text",
    "change_stats_markup",
    "change_stats_plain",
    "file_header_min_width",
    "truncate_middle",
    "viewed_state_badge",
)


def build_diff_header_text(
    *,
    current_file: str | None,
    file: PRFile | None,
    showing_full_file: bool,
    preview_location: str,
) -> str:
    """Build the diff header text including viewed and preview status."""
    if not current_file:
        return "Select a file to view diff"

    path = escape(current_file)
    status_parts: list[str] = []
    if file is not None:
        state_badge, state_style = viewed_state_badge(file)
        status_parts.extend(
            [
                f"[{state_style}]{state_badge}[/]",
                change_stats_markup(file.additions, file.deletions),
            ]
        )

    if showing_full_file:
        status_parts.append("[dim italic]preview[/]")
        if preview_location:
            status_parts.append(f"[dim]{escape(preview_location)}[/]")

    if not status_parts:
        return f"[bold #cad3f5]{path}[/]"

    return f"[bold #cad3f5]{path}[/]  " + "  [dim]|[/]  ".join(status_parts)


def viewed_state_badge(file: PRFile | None) -> tuple[str, str]:
    """Return display text and style for a file viewed state."""
    state = file.viewer_viewed_state if file is not None else FileViewedState.UNVIEWED
    if state == FileViewedState.VIEWED:
        return "✓ Viewed", "bold #a6da95"
    if state == FileViewedState.DISMISSED:
        return "! Changed", "bold #eed49f"
    return "● Unviewed", "#6e738d"


def change_stats_markup(additions: int, deletions: int) -> str:
    """Return Rich markup for file addition/deletion counts."""
    parts: list[str] = []
    if deletions:
        parts.append(f"[bold #ed8796]-{deletions}[/]")
    if additions:
        parts.append(f"[bold #a6da95]+{additions}[/]")
    if not parts:
        return "[dim]no textual changes[/]"
    return " ".join(parts)


def change_stats_plain(additions: int, deletions: int) -> str:
    """Return plain text for file addition/deletion counts."""
    parts: list[str] = []
    if deletions:
        parts.append(f"-{deletions}")
    if additions:
        parts.append(f"+{additions}")
    if not parts:
        return "no textual changes"
    return " ".join(parts)


def append_change_stats(text: Text, additions: int, deletions: int) -> None:
    """Append styled file addition/deletion counts to Rich text."""
    if deletions:
        text.append(f"-{deletions}", style="bold #ed8796")
        if additions:
            text.append(" ")
    if additions:
        text.append(f"+{additions}", style="bold #a6da95")
    if not additions and not deletions:
        text.append("no textual changes", style="dim")


def build_file_header_text(
    *,
    path: str,
    old_path: str | None,
    additions: int,
    deletions: int,
    path_budget: int,
) -> Text:
    """Build the Rich text used by combined-file diff headers."""
    full_path = _file_header_display_path(path=path, old_path=old_path)
    display_path = truncate_middle(full_path, path_budget)

    text = Text()
    text.append("▾", style="#6e738d")
    text.append(" ")
    if old_path and old_path != path and display_path == full_path:
        text.append(old_path, style="dim")
        text.append(" -> ", style="dim")
        text.append(path, style="bold #cad3f5")
    else:
        text.append(display_path, style="bold #cad3f5")
    text.append("  ")
    append_change_stats(text, additions, deletions)
    return text


def file_header_min_width(*, path: str, old_path: str | None, stats_plain: str) -> int:
    """Return the minimum cell width needed before viewport sizing applies."""
    return (
        cell_len(_file_header_display_path(path=path, old_path=old_path))
        + cell_len(stats_plain)
        + FILE_HEADER_CHROME_WIDTH
    )


def aggregate_file_change_stats(diff: FileDiff | None, path: str) -> tuple[int, int]:
    """Return added/deleted line counts for one file path in a diff."""
    if diff is None:
        return 0, 0

    additions = 0
    deletions = 0
    active_path = diff.filename
    for hunk in diff.hunks:
        hunk_path = hunk.file_path or active_path
        for line in hunk.lines:
            line_path = line.file_path or hunk_path
            if line_path != path:
                continue
            if line.is_added or line.is_modified:
                additions += 1
            if line.is_deleted or line.is_modified:
                deletions += 1
    return additions, deletions


def truncate_middle(value: str, max_width: int) -> str:
    """Truncate text in the middle without exceeding a display-cell width."""
    if cell_len(value) <= max_width:
        return value
    if max_width <= 0:
        return ""
    if max_width <= 3:
        return _take_cell_prefix(value, max_width)

    ellipsis = "..."
    text_budget = max_width - cell_len(ellipsis)
    head_budget = max(1, text_budget // 2)
    tail_budget = max(1, text_budget - head_budget)

    head = _take_cell_prefix(value, head_budget)
    tail = _take_cell_suffix(value, tail_budget)
    result = f"{head}{ellipsis}{tail}"

    while cell_len(result) > max_width and tail:
        tail = _take_cell_suffix(tail[:-1], cell_len(tail) - 1)
        result = f"{head}{ellipsis}{tail}"
    while cell_len(result) > max_width and head:
        head = _take_cell_prefix(head[:-1], cell_len(head) - 1)
        result = f"{head}{ellipsis}{tail}"
    return result


def _file_header_display_path(*, path: str, old_path: str | None) -> str:
    if old_path and old_path != path:
        return f"{old_path} -> {path}"
    return path


def _take_cell_prefix(value: str, max_width: int) -> str:
    if max_width <= 0:
        return ""

    result = ""
    for char in value:
        next_value = f"{result}{char}"
        if cell_len(next_value) > max_width:
            break
        result = next_value
    return result


def _take_cell_suffix(value: str, max_width: int) -> str:
    if max_width <= 0:
        return ""

    result = ""
    for char in reversed(value):
        next_value = f"{char}{result}"
        if cell_len(next_value) > max_width:
            break
        result = next_value
    return result
