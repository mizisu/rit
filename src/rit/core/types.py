from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from textual.content import Content


class DiffSide(Enum):
    LEFT = "left"  # Old/deleted
    RIGHT = "right"  # New/added


class SegmentType(Enum):
    UNCHANGED = "unchanged"
    ADDED = "added"
    DELETED = "deleted"


@dataclass
class InlineSegment:
    text: str
    type: SegmentType

    @property
    def is_changed(self) -> bool:
        return self.type != SegmentType.UNCHANGED


@dataclass
class DiffLine:
    old_line_no: int | None  # Line number in old file
    new_line_no: int | None  # Line number in new file

    old_content: str = ""  # Content on left side
    new_content: str = ""  # Content on right side

    is_added: bool = False
    is_deleted: bool = False
    is_modified: bool = False  # Line exists on both sides but content differs

    old_segments: list[InlineSegment] = field(default_factory=list)
    new_segments: list[InlineSegment] = field(default_factory=list)

    line_index: int = 0  # Global index in diff view (0-based)

    highlighted_old_content: Content | None = None
    highlighted_new_content: Content | None = None

    is_selected: bool = False  # Visual mode selection
    is_current: bool = False  # Current cursor line

    @property
    def has_old_side(self) -> bool:
        return self.old_line_no is not None

    @property
    def has_new_side(self) -> bool:
        return self.new_line_no is not None

    @property
    def is_context(self) -> bool:
        return not self.is_added and not self.is_deleted and not self.is_modified

    @property
    def has_word_diff(self) -> bool:
        return bool(self.old_segments) or bool(self.new_segments)


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str = ""  # Optional function/class context

    lines: list[DiffLine] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(
            line.is_added or line.is_deleted or line.is_modified for line in self.lines
        )


@dataclass
class FileDiff:
    filename: str
    old_filename: str | None = None  # For renames
    hunks: list[DiffHunk] = field(default_factory=list)

    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False

    @property
    def total_additions(self) -> int:
        return sum(
            1
            for hunk in self.hunks
            for line in hunk.lines
            if line.is_added or line.is_modified
        )

    @property
    def total_deletions(self) -> int:
        return sum(
            1
            for hunk in self.hunks
            for line in hunk.lines
            if line.is_deleted or line.is_modified
        )
