from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile

__all__ = (
    "COMBINED_DIFF_FILENAME",
    "CombinedDiffDocument",
    "build_combined_diff_document",
    "load_missing_combined_file_diffs",
)


COMBINED_DIFF_FILENAME = "All files"


@dataclass(frozen=True)
class CombinedDiffDocument:
    """Synthetic diff document for rendering all PR files in one scroll."""

    diff: FileDiff
    file_line_starts: dict[str, int]
    file_start_lines: tuple[int, ...]
    file_start_names: tuple[str, ...]
    line_lookup: dict[tuple[str, int, Literal["LEFT", "RIGHT"]], int]

    def line_index_for_location(
        self,
        filename: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> int | None:
        return self.line_lookup.get((filename, line, side))

    def file_for_line(self, line_index: int) -> str | None:
        if not self.file_start_lines:
            return None
        if len(self.file_start_lines) == 1:
            return (
                self.file_start_names[0]
                if line_index >= self.file_start_lines[0]
                else None
            )

        index = bisect_right(self.file_start_lines, line_index) - 1
        if index < 0:
            return None
        return self.file_start_names[index]


def build_combined_diff_document(
    files: Sequence[PRFile],
    file_diffs: Mapping[str, FileDiff],
) -> CombinedDiffDocument | None:
    """Build a combined diff document once every file diff is available."""
    hunks: list[DiffHunk] = []
    file_line_starts: dict[str, int] = {}
    file_start_lines: list[int] = []
    file_start_names: list[str] = []
    line_lookup: dict[tuple[str, int, Literal["LEFT", "RIGHT"]], int] = {}
    next_line_index = 0
    is_fully_refined = True

    for file in files:
        filename = file.filename
        diff = file_diffs.get(filename)
        if diff is None:
            return None
        is_fully_refined = is_fully_refined and diff.is_fully_refined
        file_start_recorded = False

        if not diff.hunks:
            _record_file_start(
                filename,
                next_line_index,
                file_line_starts,
                file_start_lines,
                file_start_names,
            )
            hunks.append(_placeholder_hunk(file, filename))
            next_line_index += 1
            continue

        for hunk in diff.hunks:
            starts_file = not file_start_recorded
            if starts_file:
                _record_file_start(
                    filename,
                    next_line_index,
                    file_line_starts,
                    file_start_lines,
                    file_start_names,
                )
                file_start_recorded = True

            lines = [
                _combined_line(
                    filename,
                    line,
                    next_line_index + offset,
                    line_lookup,
                )
                for offset, line in enumerate(hunk.lines)
            ]
            hunks.append(
                DiffHunk(
                    old_start=hunk.old_start,
                    old_count=hunk.old_count,
                    new_start=hunk.new_start,
                    new_count=hunk.new_count,
                    header=hunk.header,
                    lines=lines,
                    starts_file=starts_file,
                    file_path=filename if starts_file else None,
                    file_old_path=file.previous_filename if starts_file else None,
                    file_status=file.status,
                    file_additions=file.additions,
                    file_deletions=file.deletions,
                )
            )
            next_line_index += len(lines)

    return CombinedDiffDocument(
        diff=FileDiff(
            filename=COMBINED_DIFF_FILENAME,
            hunks=hunks,
            is_fully_refined=is_fully_refined,
            show_hunk_headers=False,
        ),
        file_line_starts=file_line_starts,
        file_start_lines=tuple(file_start_lines),
        file_start_names=tuple(file_start_names),
        line_lookup=line_lookup,
    )


async def load_missing_combined_file_diffs(
    filenames: Sequence[str],
    file_diffs: Mapping[str, FileDiff],
    load_diff: Callable[[str], Awaitable[FileDiff | None]],
    *,
    concurrency: int,
) -> None:
    """Load missing file diffs with bounded concurrency."""
    missing = [filename for filename in filenames if filename not in file_diffs]
    if not missing:
        return
    if len(missing) == 1:
        await load_diff(missing[0])
        return

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def load(filename: str) -> None:
        async with semaphore:
            await load_diff(filename)

    await asyncio.gather(*(load(filename) for filename in missing))


def _record_file_start(
    filename: str,
    line_index: int,
    file_line_starts: dict[str, int],
    file_start_lines: list[int],
    file_start_names: list[str],
) -> None:
    file_line_starts[filename] = line_index
    file_start_lines.append(line_index)
    file_start_names.append(filename)


def _placeholder_hunk(file: PRFile, filename: str) -> DiffHunk:
    return DiffHunk(
        old_start=0,
        old_count=0,
        new_start=0,
        new_count=0,
        header="no textual changes",
        lines=[
            DiffLine(
                old_line_no=None,
                new_line_no=None,
                old_content="",
                new_content="No textual changes",
                file_path=filename,
            )
        ],
        starts_file=True,
        file_path=filename,
        file_old_path=file.previous_filename,
        file_status=file.status,
        file_additions=file.additions,
        file_deletions=file.deletions,
    )


def _combined_line(
    filename: str,
    line: DiffLine,
    line_index: int,
    line_lookup: dict[tuple[str, int, Literal["LEFT", "RIGHT"]], int],
) -> DiffLine:
    if line.old_line_no is not None:
        line_lookup[(filename, line.old_line_no, "LEFT")] = line_index
    if line.new_line_no is not None:
        line_lookup[(filename, line.new_line_no, "RIGHT")] = line_index
    return replace(line, file_path=filename)
