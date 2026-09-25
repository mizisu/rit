from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from rit.core.diff import ParsedFilePatchSummary, parse_file_patch_summary, parse_patch
from rit.core.types import FileDiff, InlineSegment, SegmentType, WordDiffRanges
from rit.state.models import PRFile

__all__ = (
    "diff_from_file_patch",
    "file_from_diff",
    "file_from_summary",
    "parse_file_patch_summaries",
)


def parse_file_patch_summaries(
    sections: Iterable[str],
) -> list[ParsedFilePatchSummary]:
    """Return lightweight file summaries from raw diff sections."""
    if isinstance(sections, Sequence) and not sections:
        return []

    summaries: list[ParsedFilePatchSummary] = []
    for section in sections:
        summary = parse_file_patch_summary(section)
        if summary is not None:
            summaries.append(summary)
    return summaries


def file_from_diff(diff: FileDiff) -> PRFile:
    """Return a PR file projection from a parsed diff."""
    additions, deletions = diff.change_counts
    old_filename = diff.old_filename
    return PRFile(
        filename=diff.filename,
        status=_file_status(
            is_new=diff.is_new,
            is_deleted=diff.is_deleted,
            old_filename=old_filename,
        ),
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
        previous_filename=old_filename,
    )


def file_from_summary(summary: ParsedFilePatchSummary) -> PRFile:
    """Return a PR file projection from lightweight diff metadata."""
    old_filename = summary.old_filename
    additions = summary.additions
    deletions = summary.deletions
    return PRFile(
        filename=summary.filename,
        status=_file_status(
            is_new=summary.is_new,
            is_deleted=summary.is_deleted,
            old_filename=old_filename,
        ),
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
        patch=summary.patch,
        previous_filename=old_filename,
    )


def diff_from_file_patch(file: PRFile) -> FileDiff:
    """Parse a PR patch, compact word spans, and restore file metadata."""
    diff = parse_patch(file.patch, file.filename)
    # ponytail: large single-file parse peaks remain; pack during refinement if needed.
    for hunk in diff.hunks:
        for line in hunk.lines:
            if line.old_segments:
                line.old_segments = _word_diff_ranges(line.old_segments)
            if line.new_segments:
                line.new_segments = _word_diff_ranges(line.new_segments)
    status = file.status
    diff.old_filename = file.previous_filename
    diff.is_new = status == "added"
    diff.is_deleted = status == "removed"
    return diff


def _word_diff_ranges(segments: list[InlineSegment] | WordDiffRanges) -> WordDiffRanges:
    if isinstance(segments, tuple):
        return segments
    ranges: list[tuple[int, int, Literal["added", "deleted"]]] = []
    offset = 0
    for segment in segments:
        end = offset + len(segment.text)
        if segment.type == SegmentType.ADDED:
            ranges.append((offset, end, "added"))
        elif segment.type == SegmentType.DELETED:
            ranges.append((offset, end, "deleted"))
        offset = end
    return tuple(ranges)


def _file_status(
    *,
    is_new: bool,
    is_deleted: bool,
    old_filename: str | None,
) -> str:
    if is_new:
        return "added"
    if is_deleted:
        return "removed"
    if old_filename:
        return "renamed"
    return "modified"
