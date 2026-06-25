from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from rit.core.types import DiffHunk, DiffLine, FileDiff

__all__ = (
    "FullFilePreviewAction",
    "FullFileRestorePosition",
    "build_full_file_diff",
    "choose_full_file_preview_action",
    "full_file_anchor_line_index",
    "full_file_preview_target",
    "full_file_restore_line_index",
    "nearest_full_file_anchor_for_deleted_line",
    "selected_full_file_anchor",
)


@dataclass(frozen=True)
class FullFilePreviewAction:
    """Action selected for a full-file preview toggle."""

    kind: Literal["ignore", "restore", "request_file", "load_current"]
    filename: str | None = None


@dataclass(frozen=True)
class FullFileRestorePosition:
    """Cursor position saved before entering a full-file preview."""

    line: int
    column: int
    cursor_pane: Literal["old", "new"]
    active_pane: Literal["old", "new"]
    viewport_offset: int | None


def choose_full_file_preview_action(
    *,
    current_file: str | None,
    selected_file: str | None,
    showing_full_file: bool,
    has_store: bool,
) -> FullFilePreviewAction:
    """Return the action needed for a full-file preview toggle."""
    if current_file is None or not has_store:
        return FullFilePreviewAction(kind="ignore")
    if showing_full_file:
        return FullFilePreviewAction(kind="restore")
    if selected_file is None:
        return FullFilePreviewAction(kind="ignore")
    if selected_file != current_file:
        return FullFilePreviewAction(kind="request_file", filename=selected_file)
    return FullFilePreviewAction(kind="load_current", filename=current_file)


def full_file_restore_line_index(
    restore_position: FullFileRestorePosition | None,
    *,
    line_count: int,
) -> int | None:
    """Return the restored line index bounded to the current diff lines."""
    if restore_position is None or line_count <= 0:
        return None
    return max(0, min(restore_position.line, line_count - 1))


def full_file_preview_target(
    current_file: str | None,
    selected_line: DiffLine | None,
) -> str | None:
    """Return the file whose full preview should open for the selected line."""
    if selected_line is not None and selected_line.file_path:
        return selected_line.file_path
    return current_file


def full_file_anchor_line_index(
    line_no: int | None,
    line_index_by_new_number: Mapping[int, int],
    *,
    available_line_bounds: tuple[int, int] | None = None,
) -> int | None:
    """Return the full-file preview line index for an anchor line number."""
    if line_no is None or not line_index_by_new_number:
        return None

    first_line, last_line = (
        available_line_bounds
        if available_line_bounds is not None
        else (min(line_index_by_new_number), max(line_index_by_new_number))
    )
    target_line_no = min(max(line_no, first_line), last_line)
    return line_index_by_new_number.get(target_line_no)


def selected_full_file_anchor(
    filename: str,
    selected_line: DiffLine | None,
    source_diff: FileDiff | None,
) -> int | None:
    """Return the full-file preview line number for the selected diff line."""
    if selected_line is None:
        return None
    if selected_line.file_path and selected_line.file_path != filename:
        return None
    if selected_line.new_line_no is not None:
        return selected_line.new_line_no
    if selected_line.old_line_no is None:
        return None
    return nearest_full_file_anchor_for_deleted_line(
        selected_line.old_line_no,
        source_diff,
    )


def nearest_full_file_anchor_for_deleted_line(
    old_line_no: int,
    source_diff: FileDiff | None,
) -> int | None:
    """Return the nearest current-file line for a deleted source line."""
    if source_diff is None:
        return None

    for hunk in source_diff.hunks:
        for index, line in enumerate(hunk.lines):
            if line.old_line_no != old_line_no or not line.is_deleted:
                continue

            for next_index in range(index + 1, len(hunk.lines)):
                next_line = hunk.lines[next_index]
                if next_line.new_line_no is not None:
                    return next_line.new_line_no

            for previous_index in range(index - 1, -1, -1):
                previous_line = hunk.lines[previous_index]
                if previous_line.new_line_no is not None:
                    return previous_line.new_line_no + 1

            return hunk.new_start

    return None


def build_full_file_diff(
    filename: str,
    content: str,
    *,
    source_diff: FileDiff | None = None,
) -> FileDiff:
    """Build a full-file preview diff from file content."""
    raw_lines = content.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()

    diff_lines = [
        DiffLine(
            old_line_no=i + 1,
            new_line_no=i + 1,
            old_content=line,
            new_content=line,
        )
        for i, line in enumerate(raw_lines)
    ]

    if source_diff is not None:
        _apply_full_preview_change_markers(diff_lines, source_diff)

    hunks = _build_full_file_preview_hunks(
        filename,
        diff_lines,
        source_diff=source_diff,
    )

    return FileDiff(filename=filename, hunks=hunks, show_hunk_headers=False)


def _apply_full_preview_change_markers(
    diff_lines: list[DiffLine],
    source_diff: FileDiff,
) -> None:
    if not diff_lines:
        return

    lines_by_new_number = {
        line.new_line_no: line for line in diff_lines if line.new_line_no is not None
    }
    total_lines = len(diff_lines)

    for hunk in source_diff.hunks:
        pending_deletion = False
        last_new_line_no: int | None = None

        for source_line in hunk.lines:
            if source_line.is_deleted:
                pending_deletion = True
                continue

            new_line_no = source_line.new_line_no
            if new_line_no is None:
                continue

            if pending_deletion:
                _mark_full_preview_deleted_before(lines_by_new_number, new_line_no)
                pending_deletion = False

            target_line = lines_by_new_number.get(new_line_no)
            if target_line is not None:
                if source_line.is_modified:
                    target_line.preview_change = "modified"
                elif source_line.is_added:
                    target_line.preview_change = "added"

            last_new_line_no = new_line_no

        if pending_deletion:
            anchor_line_no = _full_preview_deleted_anchor_line(
                hunk,
                total_lines,
                last_new_line_no,
            )
            if anchor_line_no is not None:
                _mark_full_preview_deleted_before(
                    lines_by_new_number,
                    anchor_line_no,
                )


def _mark_full_preview_deleted_before(
    lines_by_new_number: dict[int, DiffLine],
    line_no: int,
) -> None:
    line = lines_by_new_number.get(line_no)
    if line is not None:
        line.preview_deleted_before = True


def _full_preview_deleted_anchor_line(
    hunk: DiffHunk,
    total_lines: int,
    last_new_line_no: int | None,
) -> int | None:
    if total_lines <= 0:
        return None
    if last_new_line_no is not None:
        return min(total_lines, max(1, last_new_line_no + 1))
    return min(total_lines, max(1, hunk.new_start))


def _build_full_file_preview_hunks(
    filename: str,
    diff_lines: list[DiffLine],
    *,
    source_diff: FileDiff | None,
) -> list[DiffHunk]:
    file_change_counts = source_diff.change_counts if source_diff else (0, 0)
    if not diff_lines:
        return [
            DiffHunk(
                old_start=0,
                old_count=0,
                new_start=0,
                new_count=0,
                header="empty file",
                lines=[],
                starts_file=True,
                file_path=filename,
            )
        ]

    if source_diff is None or not source_diff.hunks:
        return [
            DiffHunk(
                old_start=1,
                old_count=len(diff_lines),
                new_start=1,
                new_count=len(diff_lines),
                header="full file",
                lines=diff_lines,
                starts_file=True,
                file_path=filename,
                file_additions=file_change_counts[0],
                file_deletions=file_change_counts[1],
            )
        ]

    change_ranges = _full_preview_change_ranges(source_diff, len(diff_lines))
    if not change_ranges:
        return [
            DiffHunk(
                old_start=1,
                old_count=len(diff_lines),
                new_start=1,
                new_count=len(diff_lines),
                header="full file",
                lines=diff_lines,
                starts_file=True,
                file_path=filename,
                file_additions=file_change_counts[0],
                file_deletions=file_change_counts[1],
            )
        ]

    hunks: list[DiffHunk] = []
    cursor = 1
    total_changes = len(change_ranges)

    for range_index, (start, end, source_hunk) in enumerate(change_ranges, start=1):
        if cursor < start:
            hunks.append(
                _make_full_preview_hunk(
                    filename,
                    diff_lines,
                    start=cursor,
                    end=start - 1,
                    header=_full_preview_context_label(range_index),
                    starts_file=not hunks,
                    file_change_counts=file_change_counts,
                )
            )

        hunks.append(
            _make_full_preview_hunk(
                filename,
                diff_lines,
                start=start,
                end=end,
                header=_full_preview_change_label(
                    range_index,
                    total_changes,
                    source_hunk.header,
                ),
                starts_file=not hunks,
                file_change_counts=file_change_counts,
                old_start=source_hunk.old_start,
                old_count=source_hunk.old_count,
                new_start=source_hunk.new_start,
                new_count=source_hunk.new_count,
            )
        )
        cursor = end + 1

    if cursor <= len(diff_lines):
        hunks.append(
            _make_full_preview_hunk(
                filename,
                diff_lines,
                start=cursor,
                end=len(diff_lines),
                header=f"context after hunk {total_changes}",
                starts_file=not hunks,
                file_change_counts=file_change_counts,
            )
        )

    return hunks


def _full_preview_change_ranges(
    source_diff: FileDiff,
    total_lines: int,
) -> list[tuple[int, int, DiffHunk]]:
    ranges: list[tuple[int, int, DiffHunk]] = []
    next_start = 1

    for hunk in source_diff.hunks:
        if hunk.new_count <= 0:
            start = max(1, min(hunk.new_start, total_lines))
            end = start
        else:
            start = max(1, min(hunk.new_start, total_lines))
            end = max(start, min(hunk.new_start + hunk.new_count - 1, total_lines))

        if start < next_start:
            start = next_start
        if start > total_lines:
            break
        if end < start:
            end = start

        ranges.append((start, end, hunk))
        next_start = end + 1

    return ranges


def _full_preview_context_label(range_index: int) -> str:
    if range_index == 1:
        return "context before hunk 1"
    return f"context between hunks {range_index - 1}-{range_index}"


def _full_preview_change_label(
    range_index: int,
    total_changes: int,
    header: str,
) -> str:
    label = f"change hunk {range_index}/{total_changes}"
    header = _clean_hunk_header(header)
    if header:
        label += f"  {header}"
    return label


def _clean_hunk_header(header: str) -> str:
    if not header:
        return ""
    if header[0].isspace() or header[-1].isspace():
        return header.strip()
    return header


def _make_full_preview_hunk(
    filename: str,
    diff_lines: list[DiffLine],
    *,
    start: int,
    end: int,
    header: str,
    starts_file: bool,
    file_change_counts: tuple[int, int],
    old_start: int | None = None,
    old_count: int | None = None,
    new_start: int | None = None,
    new_count: int | None = None,
) -> DiffHunk:
    count = max(0, end - start + 1)
    hunk_lines = (
        diff_lines
        if start == 1 and end == len(diff_lines)
        else diff_lines[start - 1 : end]
    )
    return DiffHunk(
        old_start=old_start if old_start is not None else start,
        old_count=old_count if old_count is not None else count,
        new_start=new_start if new_start is not None else start,
        new_count=new_count if new_count is not None else count,
        header=header,
        lines=hunk_lines,
        starts_file=starts_file,
        file_path=filename if starts_file else None,
        file_additions=file_change_counts[0],
        file_deletions=file_change_counts[1],
    )
