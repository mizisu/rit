"""Core utilities for rit."""

from rit.core.types import DiffLine, DiffHunk, DiffSide, InlineSegment, SegmentType
from rit.core.diff import compute_line_diff, compute_word_diff, parse_patch

__all__ = [
    "DiffLine",
    "DiffHunk",
    "DiffSide",
    "InlineSegment",
    "SegmentType",
    "compute_line_diff",
    "compute_word_diff",
    "parse_patch",
]
