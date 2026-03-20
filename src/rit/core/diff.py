"""Diff parsing and word-level diff computation."""

import re
from difflib import SequenceMatcher

from rit.core.types import (
    DiffLine,
    DiffHunk,
    FileDiff,
    InlineSegment,
    SegmentType,
)

WORD_DIFF_THRESHOLD = 0.2
WORD_DIFF_MAX_LINE_LENGTH = 1000
TAB_SIZE = 4


def parse_patch(patch: str, filename: str) -> FileDiff:
    if not patch:
        return FileDiff(filename=filename)

    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None

    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

    old_line_no = 0
    new_line_no = 0

    for line in patch.splitlines():
        match = hunk_pattern.match(line)
        if match:
            if current_hunk:
                hunks.append(current_hunk)

            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) else 1
            header = match.group(5).strip()

            current_hunk = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=header,
            )
            old_line_no = old_start
            new_line_no = new_start
            continue

        if current_hunk is None:
            continue

        if line.startswith("+"):
            content = line[1:].expandtabs(TAB_SIZE)
            current_hunk.lines.append(
                DiffLine(
                    old_line_no=None,
                    new_line_no=new_line_no,
                    old_content="",
                    new_content=content,
                    is_added=True,
                )
            )
            new_line_no += 1
        elif line.startswith("-"):
            content = line[1:].expandtabs(TAB_SIZE)
            current_hunk.lines.append(
                DiffLine(
                    old_line_no=old_line_no,
                    new_line_no=None,
                    old_content=content,
                    new_content="",
                    is_deleted=True,
                )
            )
            old_line_no += 1
        elif line.startswith(" ") or line == "":
            content = line[1:].expandtabs(TAB_SIZE) if line.startswith(" ") else ""
            current_hunk.lines.append(
                DiffLine(
                    old_line_no=old_line_no,
                    new_line_no=new_line_no,
                    old_content=content,
                    new_content=content,
                )
            )
            old_line_no += 1
            new_line_no += 1
        elif line.startswith("\\"):
            continue

    if current_hunk:
        hunks.append(current_hunk)

    for hunk in hunks:
        _identify_modified_lines(hunk)

    return FileDiff(filename=filename, hunks=hunks)


def _identify_modified_lines(hunk: DiffHunk) -> None:
    """Identify modified lines (delete immediately followed by add).

    This converts adjacent delete+add pairs into "modified" lines
    that can show side-by-side with word-level diff.
    """
    i = 0
    new_lines: list[DiffLine] = []

    while i < len(hunk.lines):
        line = hunk.lines[i]

        if line.is_deleted and i + 1 < len(hunk.lines):
            next_line = hunk.lines[i + 1]
            if next_line.is_added:
                max_line_length = max(
                    len(line.old_content),
                    len(next_line.new_content),
                )

                if max_line_length > WORD_DIFF_MAX_LINE_LENGTH:
                    new_lines.append(
                        DiffLine(
                            old_line_no=line.old_line_no,
                            new_line_no=next_line.new_line_no,
                            old_content=line.old_content,
                            new_content=next_line.new_content,
                            is_modified=True,
                        )
                    )
                    i += 2
                    continue

                similarity = _compute_similarity(
                    line.old_content, next_line.new_content
                )

                if similarity >= WORD_DIFF_THRESHOLD:
                    old_segments, new_segments = compute_word_diff(
                        line.old_content, next_line.new_content
                    )
                    new_lines.append(
                        DiffLine(
                            old_line_no=line.old_line_no,
                            new_line_no=next_line.new_line_no,
                            old_content=line.old_content,
                            new_content=next_line.new_content,
                            is_modified=True,
                            old_segments=old_segments,
                            new_segments=new_segments,
                        )
                    )
                    i += 2
                    continue

        new_lines.append(line)
        i += 1

    hunk.lines = new_lines


def _compute_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\S+|\s+", text)


def _merge_segments(segments: list[InlineSegment]) -> list[InlineSegment]:
    """Merge small unchanged gaps between changed segments.

    Whitespace-only UNCHANGED segments between same-type changed segments
    are absorbed into the changed region, producing one continuous highlight
    instead of a fragmented patchwork.
    """
    if len(segments) <= 2:
        return segments

    # First pass: convert whitespace-only UNCHANGED between changed segments
    converted = list(segments)
    for i in range(1, len(converted) - 1):
        seg = converted[i]
        if (
            seg.type == SegmentType.UNCHANGED
            and seg.text.strip() == ""
            and converted[i - 1].type != SegmentType.UNCHANGED
            and converted[i + 1].type != SegmentType.UNCHANGED
        ):
            converted[i] = InlineSegment(text=seg.text, type=converted[i - 1].type)

    # Second pass: merge consecutive same-type segments
    result: list[InlineSegment] = [converted[0]]
    for seg in converted[1:]:
        if result[-1].type == seg.type:
            prev = result[-1]
            result[-1] = InlineSegment(text=prev.text + seg.text, type=prev.type)
        else:
            result.append(seg)

    return result


def compute_word_diff(
    old_text: str, new_text: str
) -> tuple[list[InlineSegment], list[InlineSegment]]:
    """Compute word-level diff between two strings.

    Uses word-level tokenization to produce clean, readable highlights
    instead of character-level fragmentation.

    Args:
        old_text: Old version of the text
        new_text: New version of the text

    Returns:
        Tuple of (old_segments, new_segments) for highlighting
    """
    if old_text == new_text:
        return (
            [InlineSegment(text=old_text, type=SegmentType.UNCHANGED)],
            [InlineSegment(text=new_text, type=SegmentType.UNCHANGED)],
        )

    old_words = _tokenize(old_text)
    new_words = _tokenize(new_text)

    matcher = SequenceMatcher(None, old_words, new_words)
    old_segments: list[InlineSegment] = []
    new_segments: list[InlineSegment] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            text = "".join(old_words[i1:i2])
            old_segments.append(InlineSegment(text=text, type=SegmentType.UNCHANGED))
            new_segments.append(InlineSegment(text=text, type=SegmentType.UNCHANGED))
        elif tag == "replace":
            old_segments.append(
                InlineSegment(text="".join(old_words[i1:i2]), type=SegmentType.DELETED)
            )
            new_segments.append(
                InlineSegment(text="".join(new_words[j1:j2]), type=SegmentType.ADDED)
            )
        elif tag == "delete":
            old_segments.append(
                InlineSegment(text="".join(old_words[i1:i2]), type=SegmentType.DELETED)
            )
        elif tag == "insert":
            new_segments.append(
                InlineSegment(text="".join(new_words[j1:j2]), type=SegmentType.ADDED)
            )

    return _merge_segments(old_segments), _merge_segments(new_segments)


def compute_line_diff(old_lines: list[str], new_lines: list[str]) -> list[DiffLine]:
    matcher = SequenceMatcher(None, old_lines, new_lines)
    result: list[DiffLine] = []

    old_line_no = 1
    new_line_no = 1

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for idx in range(i2 - i1):
                result.append(
                    DiffLine(
                        old_line_no=old_line_no,
                        new_line_no=new_line_no,
                        old_content=old_lines[i1 + idx],
                        new_content=new_lines[j1 + idx],
                    )
                )
                old_line_no += 1
                new_line_no += 1

        elif tag == "replace":
            old_chunk = old_lines[i1:i2]
            new_chunk = new_lines[j1:j2]
            max_len = max(len(old_chunk), len(new_chunk))
            for idx in range(max_len):
                old_text = old_chunk[idx] if idx < len(old_chunk) else ""
                new_text = new_chunk[idx] if idx < len(new_chunk) else ""

                if old_text and new_text:
                    max_line_length = max(len(old_text), len(new_text))
                    if max_line_length > WORD_DIFF_MAX_LINE_LENGTH:
                        result.append(
                            DiffLine(
                                old_line_no=old_line_no,
                                new_line_no=new_line_no,
                                old_content=old_text,
                                new_content=new_text,
                                is_modified=True,
                            )
                        )
                    else:
                        similarity = _compute_similarity(old_text, new_text)
                        if similarity >= WORD_DIFF_THRESHOLD:
                            old_segments, new_segments = compute_word_diff(
                                old_text, new_text
                            )
                            result.append(
                                DiffLine(
                                    old_line_no=old_line_no,
                                    new_line_no=new_line_no,
                                    old_content=old_text,
                                    new_content=new_text,
                                    is_modified=True,
                                    old_segments=old_segments,
                                    new_segments=new_segments,
                                )
                            )
                        else:
                            result.append(
                                DiffLine(
                                    old_line_no=old_line_no,
                                    new_line_no=None,
                                    old_content=old_text,
                                    is_deleted=True,
                                )
                            )
                            result.append(
                                DiffLine(
                                    old_line_no=None,
                                    new_line_no=new_line_no,
                                    new_content=new_text,
                                    is_added=True,
                                )
                            )
                    old_line_no += 1
                    new_line_no += 1
                elif old_text:
                    result.append(
                        DiffLine(
                            old_line_no=old_line_no,
                            new_line_no=None,
                            old_content=old_text,
                            is_deleted=True,
                        )
                    )
                    old_line_no += 1
                else:
                    result.append(
                        DiffLine(
                            old_line_no=None,
                            new_line_no=new_line_no,
                            new_content=new_text,
                            is_added=True,
                        )
                    )
                    new_line_no += 1

        elif tag == "delete":
            for idx in range(i2 - i1):
                result.append(
                    DiffLine(
                        old_line_no=old_line_no,
                        new_line_no=None,
                        old_content=old_lines[i1 + idx],
                        is_deleted=True,
                    )
                )
                old_line_no += 1

        elif tag == "insert":
            for idx in range(j2 - j1):
                result.append(
                    DiffLine(
                        old_line_no=None,
                        new_line_no=new_line_no,
                        new_content=new_lines[j1 + idx],
                        is_added=True,
                    )
                )
                new_line_no += 1

    return result
