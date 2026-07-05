from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from rit.core.types import FileDiff
from rit.ui.widgets.diff_types import RenderedRow

__all__ = (
    "line_index_for_location",
    "row_for_line_and_pane",
)


def line_index_for_location(
    diff: FileDiff,
    filename: str,
    line: int,
    side: Literal["LEFT", "RIGHT"],
    *,
    old_line_index: Mapping[int, int],
    new_line_index: Mapping[int, int],
    old_file_line_index: Mapping[tuple[str, int], int] | None = None,
    new_file_line_index: Mapping[tuple[str, int], int] | None = None,
) -> int | None:
    """Return the rendered diff line index for a file location."""
    file_index = old_file_line_index if side == "LEFT" else new_file_line_index
    if file_index is not None:
        cached = file_index.get((filename, line))
        if cached is not None:
            return cached

    if diff.filename == filename:
        cached = (old_line_index if side == "LEFT" else new_line_index).get(line)
        if cached is not None:
            return cached

    active_file = diff.filename
    target_attr = "old_line_no" if side == "LEFT" else "new_line_no"
    found_target_file = False

    for hunk in diff.hunks:
        if hunk.starts_file and hunk.file_path:
            if found_target_file and hunk.file_path != filename:
                break
            active_file = hunk.file_path
        if active_file != filename:
            continue
        found_target_file = True
        for diff_line in hunk.lines:
            if getattr(diff_line, target_attr) == line:
                return diff_line.line_index

    return None


def row_for_line_and_pane(
    rows: Sequence[RenderedRow],
    line_index: int,
    pane: Literal["old", "new"],
) -> RenderedRow | None:
    """Return the best rendered row for a diff line and pane."""
    fallback = None
    for row in rows:
        if row.line_index != line_index:
            continue
        if fallback is None:
            fallback = row
        if row.side == "auto" or row.side == pane:
            return row
    return fallback
