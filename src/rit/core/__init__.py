"""Core utilities for rit."""

from rit.core.diff import compute_line_diff, compute_word_diff, parse_patch
from rit.core.types import (
    DiffHunk,
    DiffLine,
    FileDiff,
    InlineSegment,
    SegmentType,
)

__all__ = [
    "DiffHunk",
    "DiffLine",
    "FileDiff",
    "InlineSegment",
    "SegmentType",
    "compute_line_diff",
    "compute_word_diff",
    "parse_patch",
]
