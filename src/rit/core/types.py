from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from textual.content import Content

__all__ = (
    "DiffHunk",
    "DiffLine",
    "DiffSide",
    "FileDiff",
    "InlineSegment",
    "SegmentType",
)


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
    file_path: str | None = None
    preview_change: Literal["added", "modified"] | None = None
    preview_deleted_before: bool = False

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
    starts_file: bool = False
    file_path: str | None = None
    file_old_path: str | None = None
    file_status: str = "modified"
    file_additions: int = 0
    file_deletions: int = 0

    @property
    def has_changes(self) -> bool:
        if not self.lines:
            return False
        if len(self.lines) == 1:
            line = self.lines[0]
            return line.is_added or line.is_deleted or line.is_modified
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
    is_fully_refined: bool = True
    show_hunk_headers: bool = True

    @property
    def change_counts(self) -> tuple[int, int]:
        additions = 0
        deletions = 0
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.is_added or line.is_modified:
                    additions += 1
                if line.is_deleted or line.is_modified:
                    deletions += 1
        return additions, deletions

    @property
    def total_additions(self) -> int:
        if not self.hunks:
            return 0
        if len(self.hunks) == 1:
            lines = self.hunks[0].lines
            if len(lines) == 1:
                line = lines[0]
                return 1 if line.is_added or line.is_modified else 0
            additions = 0
            for line in lines:
                if line.is_added or line.is_modified:
                    additions += 1
            return additions
        return sum(
            1
            for hunk in self.hunks
            for line in hunk.lines
            if line.is_added or line.is_modified
        )

    @property
    def total_deletions(self) -> int:
        if not self.hunks:
            return 0
        if len(self.hunks) == 1:
            lines = self.hunks[0].lines
            if len(lines) == 1:
                line = lines[0]
                return 1 if line.is_deleted or line.is_modified else 0
            deletions = 0
            for line in lines:
                if line.is_deleted or line.is_modified:
                    deletions += 1
            return deletions
        return sum(
            1
            for hunk in self.hunks
            for line in hunk.lines
            if line.is_deleted or line.is_modified
        )
