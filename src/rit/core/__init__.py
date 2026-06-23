"""Core utilities for rit."""

from rit.core.types import (
    DiffHunk,
    DiffLine,
    DiffSide,
    FileDiff,
    InlineSegment,
    SegmentType,
)
from rit.core.diff import compute_line_diff, compute_word_diff, parse_patch

__all__ = [
    "DiffHunk",
    "DiffLine",
    "DiffSide",
    "FileDiff",
    "InlineSegment",
    "SegmentType",
    "compute_line_diff",
    "compute_word_diff",
    "parse_patch",
]
