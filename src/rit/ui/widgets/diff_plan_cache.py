"""Input-checked source plans and bounded row placements for fold projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.ui.widgets import diff_plan as _plan
from rit.ui.widgets.diff_layout import MIN_LINE_NUMBER_WIDTH
from rit.ui.widgets.diff_types import RenderedRow


@dataclass
class HunkPlan:
    lines: list[DiffLine]
    plan: _plan.DiffPlan
    signature: tuple[tuple[object, ...], ...]
    placements: dict[bool, tuple[tuple[int, int, int], _plan.RenderedRowsPlan]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class PlanProjection:
    plan: _plan.DiffPlan
    hunks: tuple[HunkPlan, ...]

    def build_rows(self, *, split: bool) -> _plan.RenderedRowsPlan:
        rows: list[RenderedRow] = []
        unified_lookup: dict[tuple[int, Literal["old", "new", "auto"]], int] = {}
        split_lookup: dict[int, int] = {}
        for hunk_index, local in enumerate(self.hunks):
            start = self.plan.hunk_start_line_indices[hunk_index]
            position = (start, hunk_index, len(rows))
            cached = local.placements.get(split)
            if cached is not None and cached[0] == position:
                placed = cached[1]
            else:
                placed = _plan.build_rendered_rows_from_lines(
                    local.lines,
                    [hunk_index] * len(local.lines),
                    split=split,
                    line_offset=start,
                    row_offset=len(rows),
                )
                local.placements[split] = position, placed
            rows.extend(placed.rows_split if split else placed.rows_unified)
            unified_lookup.update(placed.row_lookup_unified)
            split_lookup.update(placed.row_lookup_split)
        return _plan.RenderedRowsPlan(
            [] if split else rows, rows if split else [], unified_lookup, split_lookup
        )


class DiffPlanCache:
    """Own checked hunk plans and bounded row placements for one source."""

    def __init__(self, source: FileDiff) -> None:
        self.source = source
        self.revision = 0
        self.has_source_changes = True
        self._signature: tuple[object, ...] = ()
        self._hunks: dict[tuple[object, ...], HunkPlan] = {}

    def _get_hunk(self, hunk: DiffHunk, filename: str) -> HunkPlan:
        placeholder = len(hunk.lines) == 1 and hunk.lines[0].is_folded_file_placeholder
        key = ("fold", filename) if placeholder else ("hunk", filename, id(hunk))
        signature = tuple(
            (
                0 if placeholder else id(line),
                line.old_line_no,
                line.new_line_no,
                line.old_content,
                line.new_content,
                line.is_added,
                line.is_deleted,
                line.is_modified,
                line.file_path or filename,
                line.is_folded_file_placeholder,
                line.syntax_highlighting_disabled,
                line.preview_change,
                line.preview_deleted_before,
                tuple((part.text, part.type) for part in line.old_segments)
                if line.old_segments
                else (),
                tuple((part.text, part.type) for part in line.new_segments)
                if line.new_segments
                else (),
            )
            for line in hunk.lines
        )
        cached = self._hunks.get(key)
        if cached is None or cached.signature != signature:
            if not placeholder:
                self.revision += 1
                self.has_source_changes = True
            plan = _plan.build_diff_plan(
                FileDiff(filename=filename, hunks=[hunk]),
                include_rendered_rows=False,
                assign_line_metadata=False,
            )
            cached = HunkPlan(hunk.lines, plan, signature)
            self._hunks[key] = cached
        return cached

    def _source_signature(self) -> tuple[object, ...]:
        source = self.source
        return (
            source.planning_revision,
            source.filename,
            source.old_filename,
            source.is_new,
            source.is_deleted,
            source.is_binary,
            source.is_fully_refined,
            source.show_hunk_headers,
            tuple(
                (
                    id(hunk),
                    id(hunk.lines),
                    len(hunk.lines),
                    hunk.file_path,
                    hunk.starts_file,
                    hunk.file_old_path,
                    hunk.file_status,
                    hunk.header,
                    hunk.old_start,
                    hunk.old_count,
                    hunk.new_start,
                    hunk.new_count,
                )
                for hunk in source.hunks
            ),
        )

    def source_structure_matches(self) -> bool:
        """Recheck source refinement and structural inputs before publication."""
        return self._source_signature() == self._signature

    def prepare(self, diff: FileDiff) -> PlanProjection:
        """Prepare visible indexes without writing live line metadata."""
        signature = self._source_signature()
        if signature != self._signature:
            self.revision += 1
            self.has_source_changes = True
            self._hunks.clear()
            self._signature = signature
        parts: list[HunkPlan] = []
        active_file = diff.filename
        for hunk in diff.hunks:
            if hunk.starts_file and hunk.file_path:
                active_file = hunk.file_path
            parts.append(self._get_hunk(hunk, active_file))
        if len(parts) == 1 and active_file == diff.filename:
            local = parts[0].plan
            # Fold placeholders are recreated; the installed plan must reference
            # the current projection, not the cached placeholder object.
            if parts[0].lines is diff.hunks[0].lines:
                return PlanProjection(local, tuple(parts))

        all_lines: list[DiffLine] = []
        file_paths: set[str] = {diff.filename}
        stats: dict[str, list[int]] = {diff.filename: [0, 0]}
        new_numbers: dict[int, int] = {}
        old_numbers: dict[int, int] = {}
        file_new: dict[tuple[str, int], int] = {}
        file_old: dict[tuple[str, int], int] = {}
        hunk_indices: list[int] = []
        ranges: list[tuple[int, int, int]] = []
        starts: list[int] = []
        ends: list[int] = []
        modified_count = 0
        widths = (1, 1, 1)
        old_width = new_width = MIN_LINE_NUMBER_WIDTH
        bounds: tuple[int, int] | None = None
        for hunk_index, (hunk, cached) in enumerate(zip(diff.hunks, parts)):
            plan = cached.plan
            start = len(all_lines)
            count = len(hunk.lines)
            all_lines.extend(hunk.lines)
            file_paths.update(plan.file_paths)
            for path, (added, deleted) in plan.file_change_stats.items():
                counts = stats.setdefault(path, [0, 0])
                counts[0] += added
                counts[1] += deleted
            for number, offset in plan.line_index_by_new_number.items():
                new_numbers.setdefault(number, start + offset)
            for number, offset in plan.line_index_by_old_number.items():
                old_numbers.setdefault(number, start + offset)
            for key, offset in plan.line_index_by_file_new_number.items():
                file_new.setdefault(key, start + offset)
            for key, offset in plan.line_index_by_file_old_number.items():
                file_old.setdefault(key, start + offset)
            hunk_indices.extend([hunk_index] * count)
            ranges.append((hunk_index, start, start + count - 1))
            starts.append(start)
            ends.append(start + count - 1)
            modified_count += plan.modified_line_count
            widths = (
                max(widths[0], plan.code_widths[0]),
                max(widths[1], plan.code_widths[1]),
                max(widths[2], plan.code_widths[2]),
            )
            old_width = max(old_width, plan.old_line_number_width)
            new_width = max(new_width, plan.new_line_number_width)
            if plan.new_line_number_bounds is not None:
                lo, hi = plan.new_line_number_bounds
                bounds = (
                    (min(bounds[0], lo), max(bounds[1], hi)) if bounds else (lo, hi)
                )
        return PlanProjection(
            _plan.DiffPlan(
                all_lines=all_lines,
                file_paths=frozenset(file_paths),
                file_change_stats={
                    path: (counts[0], counts[1]) for path, counts in stats.items()
                },
                line_index_by_new_number=new_numbers,
                line_index_by_old_number=old_numbers,
                new_line_number_bounds=bounds,
                line_index_by_file_new_number=file_new,
                line_index_by_file_old_number=file_old,
                hunk_index_by_line=hunk_indices,
                hunk_line_ranges=ranges,
                hunk_start_line_indices=starts,
                hunk_end_line_indices=ends,
                modified_line_count=modified_count,
                code_widths=widths,
                old_line_number_width=old_width,
                new_line_number_width=new_width,
                rendered_rows=_plan.RenderedRowsPlan([], [], {}, {}),
            ),
            tuple(parts),
        )


def publish_line_metadata(diff: FileDiff) -> None:
    """Install dense view indices only within the validated UI commit."""
    index = 0
    active_file = diff.filename
    for hunk in diff.hunks:
        if hunk.starts_file and hunk.file_path:
            active_file = hunk.file_path
        for line in hunk.lines:
            line.line_index = index
            if line.file_path is None:
                line.file_path = active_file
            index += 1
