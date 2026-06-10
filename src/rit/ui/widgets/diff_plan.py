"""DiffView planning helpers for line indexes and rendered rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rit.core.types import DiffLine, FileDiff
from rit.ui.widgets.diff_types import RenderedRow


@dataclass(frozen=True)
class RenderedRowsPlan:
    rows_unified: list[RenderedRow]
    rows_split: list[RenderedRow]
    row_lookup_unified: dict[tuple[int, Literal["old", "new", "auto"]], int]
    row_lookup_split: dict[int, int]


@dataclass(frozen=True)
class DiffPlan:
    all_lines: list[DiffLine]
    line_index_by_new_number: dict[int, int]
    line_index_by_old_number: dict[int, int]
    line_index_by_file_new_number: dict[tuple[str, int], int]
    line_index_by_file_old_number: dict[tuple[str, int], int]
    hunk_index_by_line: list[int]
    hunk_line_ranges: list[tuple[int, int, int]]
    modified_line_count: int
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


def build_rendered_rows(diff: FileDiff | None) -> RenderedRowsPlan:
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
            if line.is_modified:
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
            else:
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
                    kind=_row_kind_for_line(line),
                    side=side,
                    anchor_id=f"line-{line.line_index}",
                    old_line_no=line.old_line_no,
                    new_line_no=line.new_line_no,
                )
                rows_unified.append(row)
                row_lookup_unified[(line.line_index, side)] = row.row_index

            split_row = RenderedRow(
                mode="split",
                row_index=len(rows_split),
                line_index=line.line_index,
                hunk_index=hunk_index,
                kind=_row_kind_for_line(line),
                side="auto",
                anchor_id=f"line-{line.line_index}",
                old_line_no=line.old_line_no,
                new_line_no=line.new_line_no,
            )
            rows_split.append(split_row)
            row_lookup_split[line.line_index] = split_row.row_index

    return RenderedRowsPlan(
        rows_unified=rows_unified,
        rows_split=rows_split,
        row_lookup_unified=row_lookup_unified,
        row_lookup_split=row_lookup_split,
    )


def build_diff_plan(diff: FileDiff) -> DiffPlan:
    all_lines: list[DiffLine] = []
    line_index_by_new_number: dict[int, int] = {}
    line_index_by_old_number: dict[int, int] = {}
    line_index_by_file_new_number: dict[tuple[str, int], int] = {}
    line_index_by_file_old_number: dict[tuple[str, int], int] = {}
    hunk_index_by_line: list[int] = []
    hunk_line_ranges: list[tuple[int, int, int]] = []
    modified_line_count = 0
    active_file = diff.filename

    line_index = 0
    for hunk_index, hunk in enumerate(diff.hunks):
        if hunk.starts_file and hunk.file_path:
            active_file = hunk.file_path
        hunk_start = line_index
        for line in hunk.lines:
            if line.file_path is None:
                line.file_path = active_file
            line.line_index = line_index
            all_lines.append(line)
            hunk_index_by_line.append(hunk_index)

            if line.is_modified:
                modified_line_count += 1

            if line.new_line_no is not None:
                line_index_by_new_number.setdefault(line.new_line_no, line_index)
                line_index_by_file_new_number.setdefault(
                    (line.file_path or active_file, line.new_line_no),
                    line_index,
                )
            if line.old_line_no is not None:
                line_index_by_old_number.setdefault(line.old_line_no, line_index)
                line_index_by_file_old_number.setdefault(
                    (line.file_path or active_file, line.old_line_no),
                    line_index,
                )

            line_index += 1

        hunk_end = line_index - 1
        hunk_line_ranges.append((hunk_index, hunk_start, hunk_end))

    return DiffPlan(
        all_lines=all_lines,
        line_index_by_new_number=line_index_by_new_number,
        line_index_by_old_number=line_index_by_old_number,
        line_index_by_file_new_number=line_index_by_file_new_number,
        line_index_by_file_old_number=line_index_by_file_old_number,
        hunk_index_by_line=hunk_index_by_line,
        hunk_line_ranges=hunk_line_ranges,
        modified_line_count=modified_line_count,
        rendered_rows=build_rendered_rows(diff),
    )
