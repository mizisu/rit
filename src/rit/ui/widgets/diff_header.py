"""File header text construction for DiffView."""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text

from rit.core.types import FileDiff
from rit.state.models import FileViewedState

FILE_HEADER_DIFFSTAT_BLOCKS = 5
_FILE_HEADER_PREFIX_WIDTH = cell_len("▾ ")
_FILE_HEADER_STATS_GAP_WIDTH = cell_len("  ")
_FILE_HEADER_DIFFSTAT_GAP_WIDTH = cell_len("  ")
_FILE_HEADER_VIEWED_GAP_WIDTH = cell_len("  ")
_FILE_HEADER_VIEWED_LABEL_WIDTH = cell_len("Unviewed")
_FILE_HEADER_EXTRA_WIDTH = 4
FILE_HEADER_CHROME_WIDTH = (
    _FILE_HEADER_PREFIX_WIDTH
    + _FILE_HEADER_STATS_GAP_WIDTH
    + _FILE_HEADER_DIFFSTAT_GAP_WIDTH
    + FILE_HEADER_DIFFSTAT_BLOCKS
    + _FILE_HEADER_VIEWED_GAP_WIDTH
    + _FILE_HEADER_VIEWED_LABEL_WIDTH
    + _FILE_HEADER_EXTRA_WIDTH
)

__all__ = (
    "FILE_HEADER_CHROME_WIDTH",
    "FILE_HEADER_DIFFSTAT_BLOCKS",
    "aggregate_file_change_stats",
    "append_change_stats",
    "build_file_header_text",
    "change_stats_plain",
    "file_header_min_width",
    "file_header_path_budget",
    "truncate_middle",
)


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


def _append_diffstat_bar(text: Text, additions: int, deletions: int) -> None:
    total = additions + deletions
    if total <= 0:
        text.append("□" * FILE_HEADER_DIFFSTAT_BLOCKS, style="#6e738d")
        return

    added_blocks = round(FILE_HEADER_DIFFSTAT_BLOCKS * additions / total)
    if additions and not added_blocks:
        added_blocks = 1
    if deletions and added_blocks == FILE_HEADER_DIFFSTAT_BLOCKS:
        added_blocks -= 1

    deleted_blocks = FILE_HEADER_DIFFSTAT_BLOCKS - added_blocks
    if added_blocks:
        text.append("■" * added_blocks, style="bold #a6da95")
    if deleted_blocks:
        text.append("■" * deleted_blocks, style="bold #ed8796")


def _append_viewed_label(text: Text, state: FileViewedState) -> None:
    if state == FileViewedState.VIEWED:
        text.append("Viewed", style="bold #a6da95")
    elif state == FileViewedState.DISMISSED:
        text.append("Changed", style="bold #eed49f")
    else:
        text.append("Unviewed", style="#6e738d")


def build_file_header_text(
    *,
    path: str,
    old_path: str | None,
    additions: int,
    deletions: int,
    path_budget: int,
    viewed_state: FileViewedState = FileViewedState.UNVIEWED,
    collapsed: bool = False,
) -> Text:
    """Build the Rich text used by file diff headers."""
    full_path = _file_header_display_path(path=path, old_path=old_path)
    display_path = truncate_middle(full_path, path_budget)

    text = Text()
    text.append("▸" if collapsed else "▾", style="#6e738d")
    text.append(" ")
    if old_path and old_path != path and display_path == full_path:
        text.append(old_path, style="dim")
        text.append(" -> ", style="dim")
        text.append(path, style="bold #cad3f5")
    else:
        text.append(display_path, style="bold #cad3f5")
    text.append("  ")
    append_change_stats(text, additions, deletions)
    text.append("  ")
    _append_diffstat_bar(text, additions, deletions)
    text.append("  ")
    _append_viewed_label(text, viewed_state)
    return text


def file_header_min_width(*, path: str, old_path: str | None, stats_plain: str) -> int:
    """Return the minimum cell width needed before viewport sizing applies."""
    return (
        cell_len(_file_header_display_path(path=path, old_path=old_path))
        + cell_len(stats_plain)
        + FILE_HEADER_CHROME_WIDTH
    )


def file_header_path_budget(width: int, *, stats_plain: str) -> int:
    """Return the path cell budget for a rendered file header width."""
    non_path_width = (
        _FILE_HEADER_PREFIX_WIDTH
        + _FILE_HEADER_STATS_GAP_WIDTH
        + cell_len(stats_plain)
        + _FILE_HEADER_DIFFSTAT_GAP_WIDTH
        + FILE_HEADER_DIFFSTAT_BLOCKS
        + _FILE_HEADER_VIEWED_GAP_WIDTH
        + _FILE_HEADER_VIEWED_LABEL_WIDTH
    )
    return max(4, width - non_path_width)


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
