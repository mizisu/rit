"""Diff parsing and word-level diff computation."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from rit.core.types import (
    DiffHunk,
    DiffLine,
    FileDiff,
    InlineSegment,
    SegmentType,
)

WORD_DIFF_THRESHOLD = 0.2
WORD_DIFF_MAX_LINE_LENGTH = 1000
TOKEN_REFINEMENT_MAX_LENGTH = 200
PARSE_REFINEMENT_CELL_BUDGET = 25
TAB_SIZE = 4
HUNK_PATTERN = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
DIFF_GIT_PREFIX = "diff --git "


@dataclass(frozen=True)
class ParsedFilePatch:
    """A parsed file section from a multi-file unified diff."""

    diff: FileDiff
    patch: str


@dataclass(frozen=True)
class ParsedFilePatchSummary:
    """Lightweight metadata for a file section from a multi-file unified diff."""

    filename: str
    patch: str
    old_filename: str | None = None
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False
    additions: int = 0
    deletions: int = 0


def parse_multi_file_patch(
    patch: str,
    *,
    refine: Literal["auto", "eager", "never"] = "auto",
    refinement_cell_budget: int | None = PARSE_REFINEMENT_CELL_BUDGET,
) -> list[ParsedFilePatch]:
    """Parse a multi-file unified diff into per-file diffs."""
    sections = _split_multi_file_patch(patch)
    parsed: list[ParsedFilePatch] = []
    for section in sections:
        filename, old_filename, is_new, is_deleted, is_binary = _parse_file_metadata(
            section
        )
        if not filename:
            continue

        diff = parse_patch(
            section,
            filename,
            refine=refine,
            refinement_cell_budget=refinement_cell_budget,
        )
        diff.old_filename = old_filename
        diff.is_new = is_new
        diff.is_deleted = is_deleted
        diff.is_binary = is_binary
        parsed.append(ParsedFilePatch(diff=diff, patch=section))
    return parsed


def parse_file_patch_summary(section: str) -> ParsedFilePatchSummary | None:
    """Parse file metadata and line counts without building diff line objects."""
    filename, old_filename, is_new, is_deleted, is_binary = _parse_file_metadata(
        section
    )
    if not filename:
        return None

    additions, deletions = _count_patch_changes(section)
    return ParsedFilePatchSummary(
        filename=filename,
        patch=section,
        old_filename=old_filename,
        is_new=is_new,
        is_deleted=is_deleted,
        is_binary=is_binary,
        additions=additions,
        deletions=deletions,
    )


def _split_multi_file_patch(patch: str) -> list[str]:
    if not patch:
        return []

    starts = [match.start() for match in re.finditer(r"(?m)^diff --git ", patch)]
    if not starts:
        return [patch]

    sections: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(patch)
        sections.append(patch[start:end].rstrip("\n"))
    return sections


def _parse_file_metadata(section: str) -> tuple[str, str | None, bool, bool, bool]:
    filename = ""
    old_filename: str | None = None
    is_new = False
    is_deleted = False
    is_binary = False

    for line in section.splitlines():
        if line.startswith(DIFF_GIT_PREFIX):
            old_path, new_path = _parse_diff_git_paths(line)
            filename = new_path or old_path
            old_filename = old_path if old_path != new_path else None
        elif line.startswith("new file mode"):
            is_new = True
            old_filename = None
        elif line.startswith("deleted file mode"):
            is_deleted = True
        elif line.startswith("rename from "):
            old_filename = line.removeprefix("rename from ")
        elif line.startswith("rename to "):
            filename = line.removeprefix("rename to ")
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            is_binary = True
        elif line.startswith("--- "):
            old_path = _normalize_patch_path(line.removeprefix("--- "))
            if old_path is None:
                is_new = True
                old_filename = None
            elif old_filename is None:
                old_filename = old_path
        elif line.startswith("+++ "):
            new_path = _normalize_patch_path(line.removeprefix("+++ "))
            if new_path is None:
                is_deleted = True
            else:
                filename = new_path
        elif line.startswith("@@ "):
            break

    if old_filename == filename:
        old_filename = None
    return filename, old_filename, is_new, is_deleted, is_binary


def _count_patch_changes(section: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in section.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _parse_diff_git_paths(line: str) -> tuple[str, str]:
    rest = line.removeprefix(DIFF_GIT_PREFIX)
    if rest.startswith("a/"):
        separator = rest.find(" b/")
        if separator >= 0:
            return rest[2:separator], rest[separator + 3 :]
    parts = rest.split(" ", 1)
    if len(parts) == 2:
        return _strip_diff_prefix(parts[0]), _strip_diff_prefix(parts[1])
    return "", _strip_diff_prefix(rest)


def _normalize_patch_path(path: str) -> str | None:
    path = path.split("\t", 1)[0]
    if path == "/dev/null":
        return None
    return _strip_diff_prefix(path)


def _strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def parse_patch(
    patch: str,
    filename: str,
    *,
    refine: Literal["auto", "eager", "never"] = "auto",
    refinement_cell_budget: int | None = PARSE_REFINEMENT_CELL_BUDGET,
) -> FileDiff:
    """Parse a unified patch with adaptive modified-line refinement."""
    if not patch:
        return FileDiff(filename=filename)

    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None

    old_line_no = 0
    new_line_no = 0

    for line in patch.splitlines():
        match = HUNK_PATTERN.match(line) if line.startswith("@@ ") else None
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

    refinement_cost = _estimate_refinement_cost(hunks)
    block_budget = _refinement_block_budget(
        refine,
        refinement_cost=refinement_cost,
        refinement_cell_budget=refinement_cell_budget,
    )
    is_fully_refined = refinement_cost == 0
    if block_budget is not False:
        is_fully_refined = True
        for hunk in hunks:
            is_fully_refined &= _identify_modified_lines(
                hunk,
                block_cell_budget=block_budget,
            )

    return FileDiff(
        filename=filename,
        hunks=hunks,
        is_fully_refined=is_fully_refined,
    )


def _refinement_block_budget(
    refine: Literal["auto", "eager", "never"],
    *,
    refinement_cost: int,
    refinement_cell_budget: int | None,
) -> int | None | Literal[False]:
    if refinement_cost == 0 or refine == "never":
        return False
    if refine == "eager":
        return None
    if refine != "auto":
        raise ValueError(f"Unsupported refinement mode: {refine}")
    if refinement_cell_budget is None or refinement_cost <= refinement_cell_budget:
        return None
    return 1


def _estimate_refinement_cost(hunks: list[DiffHunk]) -> int:
    cost = 0
    for hunk in hunks:
        cost += _estimate_hunk_refinement_cost(hunk)
    return cost


def _estimate_hunk_refinement_cost(hunk: DiffHunk) -> int:
    cost = 0
    i = 0
    lines = hunk.lines
    while i < len(lines):
        if not lines[i].is_deleted:
            i += 1
            continue

        deleted_count = 0
        while i < len(lines) and lines[i].is_deleted:
            deleted_count += 1
            i += 1

        added_count = 0
        while i < len(lines) and lines[i].is_added:
            added_count += 1
            i += 1

        cost += deleted_count * added_count
    return cost


def _realign_replace_block(
    deleted_lines: list[DiffLine],
    added_lines: list[DiffLine],
) -> list[DiffLine]:
    realigned_lines = compute_line_diff(
        [line.old_content for line in deleted_lines],
        [line.new_content for line in added_lines],
    )

    old_index = 0
    new_index = 0

    for line in realigned_lines:
        if line.old_line_no is not None:
            line.old_line_no = deleted_lines[old_index].old_line_no
            old_index += 1
        if line.new_line_no is not None:
            line.new_line_no = added_lines[new_index].new_line_no
            new_index += 1

    return realigned_lines


def _identify_modified_lines(
    hunk: DiffHunk,
    *,
    block_cell_budget: int | None = None,
) -> bool:
    """Identify modified lines within contiguous delete/add replace blocks."""
    i = 0
    new_lines: list[DiffLine] = []
    fully_refined = True

    while i < len(hunk.lines):
        line = hunk.lines[i]
        if not line.is_deleted:
            new_lines.append(line)
            i += 1
            continue

        deleted_start = i
        while i < len(hunk.lines) and hunk.lines[i].is_deleted:
            i += 1

        added_start = i
        while i < len(hunk.lines) and hunk.lines[i].is_added:
            i += 1

        deleted_lines = hunk.lines[deleted_start:added_start]
        added_lines = hunk.lines[added_start:i]

        if added_lines:
            block_cost = len(deleted_lines) * len(added_lines)
            if block_cell_budget is None or block_cost <= block_cell_budget:
                new_lines.extend(_realign_replace_block(deleted_lines, added_lines))
            else:
                new_lines.extend(deleted_lines)
                new_lines.extend(added_lines)
                fully_refined = False
        else:
            new_lines.extend(deleted_lines)

    hunk.lines = new_lines
    return fully_refined


def _replace_pair_cost(old_text: str, new_text: str) -> float:
    if old_text == new_text:
        return 0.0

    similarity = _compute_similarity(old_text, new_text)
    if similarity < WORD_DIFF_THRESHOLD:
        return 2.1

    return 1.0 - similarity


def _align_replace_lines(
    old_lines: list[str],
    new_lines: list[str],
) -> list[tuple[str | None, str | None]]:
    old_count = len(old_lines)
    new_count = len(new_lines)

    if old_count == 0:
        return [(None, line) for line in new_lines]
    if new_count == 0:
        return [(line, None) for line in old_lines]

    costs = [[0.0] * (new_count + 1) for _ in range(old_count + 1)]
    choices = [[""] * (new_count + 1) for _ in range(old_count + 1)]

    for old_index in range(1, old_count + 1):
        costs[old_index][0] = costs[old_index - 1][0] + 1.0
        choices[old_index][0] = "delete"

    for new_index in range(1, new_count + 1):
        costs[0][new_index] = costs[0][new_index - 1] + 1.0
        choices[0][new_index] = "insert"

    for old_index in range(1, old_count + 1):
        old_text = old_lines[old_index - 1]
        for new_index in range(1, new_count + 1):
            new_text = new_lines[new_index - 1]
            pair_cost = costs[old_index - 1][new_index - 1] + _replace_pair_cost(
                old_text, new_text
            )
            delete_cost = costs[old_index - 1][new_index] + 1.0
            insert_cost = costs[old_index][new_index - 1] + 1.0

            if (
                old_text == new_text
                and pair_cost <= delete_cost
                and pair_cost <= insert_cost
            ):
                costs[old_index][new_index] = pair_cost
                choices[old_index][new_index] = "pair"
            elif pair_cost < delete_cost and pair_cost < insert_cost:
                costs[old_index][new_index] = pair_cost
                choices[old_index][new_index] = "pair"
            elif insert_cost <= delete_cost:
                costs[old_index][new_index] = insert_cost
                choices[old_index][new_index] = "insert"
            else:
                costs[old_index][new_index] = delete_cost
                choices[old_index][new_index] = "delete"

    aligned_lines: list[tuple[str | None, str | None]] = []
    old_index = old_count
    new_index = new_count

    while old_index > 0 or new_index > 0:
        choice = choices[old_index][new_index]
        if choice == "pair":
            aligned_lines.append((old_lines[old_index - 1], new_lines[new_index - 1]))
            old_index -= 1
            new_index -= 1
        elif choice == "delete":
            aligned_lines.append((old_lines[old_index - 1], None))
            old_index -= 1
        else:
            aligned_lines.append((None, new_lines[new_index - 1]))
            new_index -= 1

    aligned_lines.reverse()
    return aligned_lines


def _compute_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    max_possible_ratio = (2 * min(len(a), len(b))) / (len(a) + len(b))
    if max_possible_ratio < WORD_DIFF_THRESHOLD:
        return 0.0

    matcher = SequenceMatcher(None, a, b)
    if matcher.real_quick_ratio() < WORD_DIFF_THRESHOLD:
        return 0.0
    if matcher.quick_ratio() < WORD_DIFF_THRESHOLD:
        return 0.0
    return matcher.ratio()


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


def _can_refine_token_replace(old_words: list[str], new_words: list[str]) -> bool:
    if len(old_words) != 1 or len(new_words) != 1:
        return False

    old_text = old_words[0]
    new_text = new_words[0]
    if old_text.isspace() or new_text.isspace():
        return False

    return max(len(old_text), len(new_text)) <= TOKEN_REFINEMENT_MAX_LENGTH


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _trim_weak_prefix_match(prefix_len: int, text: str) -> int:
    if prefix_len == 0:
        return 0

    fragment_len = 0
    index = prefix_len - 1
    while index >= 0 and _is_word_char(text[index]):
        fragment_len += 1
        index -= 1

    if fragment_len == 1 and index >= 0 and not _is_word_char(text[index]):
        return prefix_len - 1

    return prefix_len


def _trim_weak_suffix_match(suffix_len: int, text: str) -> int:
    if suffix_len == 0:
        return 0

    start = len(text) - suffix_len
    fragment_len = 0
    index = start
    while index < len(text) and _is_word_char(text[index]):
        fragment_len += 1
        index += 1

    if fragment_len == 1 and index < len(text) and not _is_word_char(text[index]):
        return suffix_len - 1

    return suffix_len


def _compute_char_diff_segments(
    old_text: str,
    new_text: str,
) -> tuple[list[InlineSegment], list[InlineSegment]]:
    prefix_len = 0
    max_prefix = min(len(old_text), len(new_text))
    while prefix_len < max_prefix and old_text[prefix_len] == new_text[prefix_len]:
        prefix_len += 1

    prefix_len = _trim_weak_prefix_match(prefix_len, old_text)

    old_remaining = len(old_text) - prefix_len
    new_remaining = len(new_text) - prefix_len
    suffix_len = 0
    max_suffix = min(old_remaining, new_remaining)
    while (
        suffix_len < max_suffix
        and old_text[len(old_text) - suffix_len - 1]
        == new_text[len(new_text) - suffix_len - 1]
    ):
        suffix_len += 1

    suffix_len = _trim_weak_suffix_match(suffix_len, old_text)

    old_middle_end = len(old_text) - suffix_len
    new_middle_end = len(new_text) - suffix_len

    old_segments: list[InlineSegment] = []
    new_segments: list[InlineSegment] = []

    prefix = old_text[:prefix_len]
    if prefix:
        old_segments.append(InlineSegment(text=prefix, type=SegmentType.UNCHANGED))
        new_segments.append(InlineSegment(text=prefix, type=SegmentType.UNCHANGED))

    old_middle = old_text[prefix_len:old_middle_end]
    new_middle = new_text[prefix_len:new_middle_end]
    if old_middle:
        old_segments.append(InlineSegment(text=old_middle, type=SegmentType.DELETED))
    if new_middle:
        new_segments.append(InlineSegment(text=new_middle, type=SegmentType.ADDED))

    suffix = old_text[old_middle_end:]
    if suffix:
        old_segments.append(InlineSegment(text=suffix, type=SegmentType.UNCHANGED))
        new_segments.append(InlineSegment(text=suffix, type=SegmentType.UNCHANGED))

    return _merge_segments(old_segments), _merge_segments(new_segments)


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
            replaced_old_words = old_words[i1:i2]
            replaced_new_words = new_words[j1:j2]
            if _can_refine_token_replace(replaced_old_words, replaced_new_words):
                refined_old_segments, refined_new_segments = (
                    _compute_char_diff_segments(
                        replaced_old_words[0],
                        replaced_new_words[0],
                    )
                )
                old_segments.extend(refined_old_segments)
                new_segments.extend(refined_new_segments)
            else:
                old_segments.append(
                    InlineSegment(
                        text="".join(replaced_old_words),
                        type=SegmentType.DELETED,
                    )
                )
                new_segments.append(
                    InlineSegment(
                        text="".join(replaced_new_words),
                        type=SegmentType.ADDED,
                    )
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

            for old_text, new_text in _align_replace_lines(old_chunk, new_chunk):
                if old_text is not None and new_text is not None:
                    if old_text == new_text:
                        result.append(
                            DiffLine(
                                old_line_no=old_line_no,
                                new_line_no=new_line_no,
                                old_content=old_text,
                                new_content=new_text,
                            )
                        )
                    else:
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
                elif old_text is not None:
                    result.append(
                        DiffLine(
                            old_line_no=old_line_no,
                            new_line_no=None,
                            old_content=old_text,
                            is_deleted=True,
                        )
                    )
                    old_line_no += 1
                elif new_text is not None:
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
