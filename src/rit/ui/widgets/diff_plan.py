"""DiffView planning helpers for line indexes and rendered rows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rich.cells import cell_len

from rit.core.types import DiffLine, FileDiff
from rit.ui.widgets.diff_types import RenderedRow

__all__ = (
    "DiffPlan",
    "RenderedRowsPlan",
    "build_diff_plan",
    "build_rendered_rows",
    "build_rendered_rows_from_lines",
)


@dataclass(frozen=True)
class RenderedRowsPlan:
    rows_unified: list[RenderedRow]
    rows_split: list[RenderedRow]
    row_lookup_unified: dict[tuple[int, Literal["old", "new", "auto"]], int]
    row_lookup_split: dict[int, int]


@dataclass(frozen=True)
class DiffPlan:
    all_lines: list[DiffLine]
    file_paths: frozenset[str]
    file_change_stats: dict[str, tuple[int, int]]
    line_index_by_new_number: dict[int, int]
    line_index_by_old_number: dict[int, int]
    new_line_number_bounds: tuple[int, int] | None
    line_index_by_file_new_number: dict[tuple[str, int], int]
    line_index_by_file_old_number: dict[tuple[str, int], int]
    hunk_index_by_line: list[int]
    hunk_line_ranges: list[tuple[int, int, int]]
    hunk_start_line_indices: list[int]
    hunk_end_line_indices: list[int]
    modified_line_count: int
    code_widths: tuple[int, int, int]
    old_line_number_width: int
    new_line_number_width: int
    rendered_rows: RenderedRowsPlan


def _row_kind_for_line(
    line: DiffLine,
    *,
    modified_side: Literal["old", "new"] | None = None,
) -> Literal[
    "context",
    "added",
    "deleted",
    "modified-old",
    "modified-new",
]:
    if line.is_modified:
        return "modified-old" if modified_side == "old" else "modified-new"
    if line.is_added:
        return "added"
    if line.is_deleted:
        return "deleted"
    return "context"


def build_rendered_rows(
    diff: FileDiff | None,
    *,
    split: bool | None = None,
) -> RenderedRowsPlan:
    rows_unified: list[RenderedRow] = []
    rows_split: list[RenderedRow] = []
    row_lookup_unified: dict[tuple[int, Literal["old", "new", "auto"]], int] = {}
    row_lookup_split: dict[int, int] = {}

    if diff is None:
        return RenderedRowsPlan(
            rows_unified=rows_unified,
            rows_split=rows_split,
            row_lookup_unified=row_lookup_unified,
            row_lookup_split=row_lookup_split,
        )

    for hunk_index, hunk in enumerate(diff.hunks):
        for line in hunk.lines:
            _append_rendered_rows_for_line(
                line=line,
                hunk_index=hunk_index,
                rows_unified=rows_unified,
                rows_split=rows_split,
                row_lookup_unified=row_lookup_unified,
                row_lookup_split=row_lookup_split,
                split=split,
            )

    return RenderedRowsPlan(
        rows_unified=rows_unified,
        rows_split=rows_split,
        row_lookup_unified=row_lookup_unified,
        row_lookup_split=row_lookup_split,
    )


def build_rendered_rows_from_lines(
    lines: Sequence[DiffLine],
    hunk_index_by_line: Sequence[int],
    *,
    split: bool | None = None,
) -> RenderedRowsPlan:
    rows_unified: list[RenderedRow] = []
    rows_split: list[RenderedRow] = []
    row_lookup_unified: dict[tuple[int, Literal["old", "new", "auto"]], int] = {}
    row_lookup_split: dict[int, int] = {}

    for offset, line in enumerate(lines):
        hunk_index = (
            hunk_index_by_line[offset] if offset < len(hunk_index_by_line) else 0
        )
        _append_rendered_rows_for_line(
            line=line,
            hunk_index=hunk_index,
            rows_unified=rows_unified,
            rows_split=rows_split,
            row_lookup_unified=row_lookup_unified,
            row_lookup_split=row_lookup_split,
            split=split,
        )

    return RenderedRowsPlan(
        rows_unified=rows_unified,
        rows_split=rows_split,
        row_lookup_unified=row_lookup_unified,
        row_lookup_split=row_lookup_split,
    )


def _append_rendered_rows_for_line(
    *,
    line: DiffLine,
    hunk_index: int,
    rows_unified: list[RenderedRow],
    rows_split: list[RenderedRow],
    row_lookup_unified: dict[tuple[int, Literal["old", "new", "auto"]], int],
    row_lookup_split: dict[int, int],
    split: bool | None,
) -> None:
    shared_kind = (
        _row_kind_for_line(line)
        if not line.is_modified or split is not False
        else None
    )
    if split is not True and line.is_modified:
        old_row = RenderedRow(
            mode="unified",
            row_index=len(rows_unified),
            line_index=line.line_index,
            hunk_index=hunk_index,
            kind=_row_kind_for_line(line, modified_side="old"),
            side="old",
            anchor_id=f"line-{line.line_index}-old",
            old_line_no=line.old_line_no,
            new_line_no=line.new_line_no,
        )
        rows_unified.append(old_row)
        row_lookup_unified[(line.line_index, "old")] = old_row.row_index

        new_row = RenderedRow(
            mode="unified",
            row_index=len(rows_unified),
            line_index=line.line_index,
            hunk_index=hunk_index,
            kind=_row_kind_for_line(line, modified_side="new"),
            side="new",
            anchor_id=f"line-{line.line_index}-new",
            old_line_no=line.old_line_no,
            new_line_no=line.new_line_no,
        )
        rows_unified.append(new_row)
        row_lookup_unified[(line.line_index, "new")] = new_row.row_index
    elif split is not True:
        side: Literal["old", "new", "auto"]
        if line.is_deleted:
            side = "old"
        elif line.is_added:
            side = "new"
        else:
            side = "auto"

        row = RenderedRow(
            mode="unified",
            row_index=len(rows_unified),
            line_index=line.line_index,
            hunk_index=hunk_index,
            kind=shared_kind if shared_kind is not None else _row_kind_for_line(line),
            side=side,
            anchor_id=f"line-{line.line_index}",
            old_line_no=line.old_line_no,
            new_line_no=line.new_line_no,
        )
        rows_unified.append(row)
        row_lookup_unified[(line.line_index, side)] = row.row_index

    if split is not False:
        split_row = RenderedRow(
            mode="split",
            row_index=len(rows_split),
            line_index=line.line_index,
            hunk_index=hunk_index,
            kind=shared_kind if shared_kind is not None else _row_kind_for_line(line),
            side="auto",
            anchor_id=f"line-{line.line_index}",
            old_line_no=line.old_line_no,
            new_line_no=line.new_line_no,
        )
        rows_split.append(split_row)
        row_lookup_split[line.line_index] = split_row.row_index


def build_diff_plan(
    diff: FileDiff,
    *,
    include_rendered_rows: bool = True,
) -> DiffPlan:
    all_lines: list[DiffLine] = []
    file_paths: set[str] = {diff.filename}
    file_change_counts: dict[str, list[int]] = {diff.filename: [0, 0]}
    line_index_by_new_number: dict[int, int] = {}
    line_index_by_old_number: dict[int, int] = {}
    line_index_by_file_new_number: dict[tuple[str, int], int] = {}
    line_index_by_file_old_number: dict[tuple[str, int], int] = {}
    hunk_index_by_line: list[int] = []
    hunk_line_ranges: list[tuple[int, int, int]] = []
    hunk_start_line_indices: list[int] = []
    hunk_end_line_indices: list[int] = []
    rows_unified: list[RenderedRow] = []
    rows_split: list[RenderedRow] = []
    row_lookup_unified: dict[tuple[int, Literal["old", "new", "auto"]], int] = {}
    row_lookup_split: dict[int, int] = {}
    modified_line_count = 0
    old_code_width = 1
    new_code_width = 1
    max_old_line_no: int | None = None
    min_new_line_no: int | None = None
    max_new_line_no: int | None = None
    active_file = diff.filename

    line_index = 0
    for hunk_index, hunk in enumerate(diff.hunks):
        if hunk.starts_file and hunk.file_path:
            active_file = hunk.file_path
        if hunk.file_path:
            file_paths.add(hunk.file_path)
            file_change_counts.setdefault(hunk.file_path, [0, 0])
        hunk_start = line_index
        for line in hunk.lines:
            if line.file_path is None:
                line.file_path = active_file
            line_path = line.file_path or active_file
            file_paths.add(line_path)
            line.line_index = line_index
            if include_rendered_rows:
                _append_rendered_rows_for_line(
                    line=line,
                    hunk_index=hunk_index,
                    rows_unified=rows_unified,
                    rows_split=rows_split,
                    row_lookup_unified=row_lookup_unified,
                    row_lookup_split=row_lookup_split,
                    split=None,
                )
            all_lines.append(line)
            hunk_index_by_line.append(hunk_index)

            if line.is_modified:
                modified_line_count += 1
            change_counts = file_change_counts.setdefault(line_path, [0, 0])
            if line.is_added or line.is_modified:
                change_counts[0] += 1
            if line.is_deleted or line.is_modified:
                change_counts[1] += 1

            old_code_width = max(old_code_width, _line_code_width(line.old_content))
            new_code_width = max(new_code_width, _line_code_width(line.new_content))

            if line.new_line_no is not None:
                line_index_by_new_number.setdefault(line.new_line_no, line_index)
                min_new_line_no = (
                    line.new_line_no
                    if min_new_line_no is None
                    else min(min_new_line_no, line.new_line_no)
                )
                max_new_line_no = (
                    line.new_line_no
                    if max_new_line_no is None
                    else max(max_new_line_no, line.new_line_no)
                )
                line_index_by_file_new_number.setdefault(
                    (line_path, line.new_line_no),
                    line_index,
                )
            if line.old_line_no is not None:
                line_index_by_old_number.setdefault(line.old_line_no, line_index)
                max_old_line_no = (
                    line.old_line_no
                    if max_old_line_no is None
                    else max(max_old_line_no, line.old_line_no)
                )
                line_index_by_file_old_number.setdefault(
                    (line_path, line.old_line_no),
                    line_index,
                )

            line_index += 1

        hunk_end = line_index - 1
        hunk_line_ranges.append((hunk_index, hunk_start, hunk_end))
        hunk_start_line_indices.append(hunk_start)
        hunk_end_line_indices.append(hunk_end)

    return DiffPlan(
        all_lines=all_lines,
        file_paths=frozenset(file_paths),
        file_change_stats={
            path: (counts[0], counts[1])
            for path, counts in file_change_counts.items()
        },
        line_index_by_new_number=line_index_by_new_number,
        line_index_by_old_number=line_index_by_old_number,
        new_line_number_bounds=(min_new_line_no, max_new_line_no)
        if min_new_line_no is not None and max_new_line_no is not None
        else None,
        line_index_by_file_new_number=line_index_by_file_new_number,
        line_index_by_file_old_number=line_index_by_file_old_number,
        hunk_index_by_line=hunk_index_by_line,
        hunk_line_ranges=hunk_line_ranges,
        hunk_start_line_indices=hunk_start_line_indices,
        hunk_end_line_indices=hunk_end_line_indices,
        modified_line_count=modified_line_count,
        code_widths=(
            max(old_code_width, new_code_width),
            old_code_width,
            new_code_width,
        ),
        old_line_number_width=_line_number_width(max_old_line_no),
        new_line_number_width=_line_number_width(max_new_line_no),
        rendered_rows=RenderedRowsPlan(
            rows_unified,
            rows_split,
            row_lookup_unified,
            row_lookup_split,
        )
        if include_rendered_rows
        else RenderedRowsPlan([], [], {}, {}),
    )


def _line_code_width(text: str) -> int:
    return max(1, cell_len(text)) if text else 1


def _line_number_width(max_line_no: int | None) -> int:
    return max(1, len(str(max_line_no))) if max_line_no is not None else 1
