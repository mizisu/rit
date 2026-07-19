"""Viewed-file folding helpers for DiffView."""

from __future__ import annotations

from collections.abc import Callable

from rit.core.types import DiffHunk, DiffLine, FileDiff

__all__ = (
    "FOLDED_VIEWED_FILE_MESSAGE",
    "build_viewed_file_fold_diff",
    "is_folded_placeholder_line",
)


FOLDED_VIEWED_FILE_MESSAGE = "Viewed file collapsed — press Enter to expand"


def build_viewed_file_fold_diff(
    diff: FileDiff,
    *,
    is_collapsed: Callable[[str], bool],
) -> tuple[FileDiff, frozenset[str]]:
    """Return a render diff with collapsed viewed-file placeholders."""
    if not diff.hunks:
        return diff, frozenset()

    folded_hunks: list[DiffHunk] = []
    folded_files: set[str] = set()
    active_file = diff.filename
    active_old_file = diff.old_filename

    for hunk in diff.hunks:
        if hunk.starts_file and hunk.file_path:
            active_file = hunk.file_path
            active_old_file = hunk.file_old_path

        hunk_file = (
            hunk.file_path if hunk.starts_file and hunk.file_path else active_file
        )
        hunk_old_file = hunk.file_old_path if hunk.starts_file else active_old_file
        if is_collapsed(hunk_file):
            if hunk_file not in folded_files:
                folded_hunks.append(
                    _collapsed_file_hunk(
                        hunk,
                        filename=hunk_file,
                        old_filename=hunk_old_file,
                    )
                )
                folded_files.add(hunk_file)
            continue

        folded_hunks.append(hunk)

    if not folded_files:
        return diff, frozenset()

    return (
        FileDiff(
            filename=diff.filename,
            old_filename=diff.old_filename,
            hunks=folded_hunks,
            is_new=diff.is_new,
            is_deleted=diff.is_deleted,
            is_binary=diff.is_binary,
            is_fully_refined=diff.is_fully_refined,
            show_hunk_headers=diff.show_hunk_headers,
        ),
        frozenset(folded_files),
    )


def is_folded_placeholder_line(line: DiffLine) -> bool:
    """Return whether a line is the synthetic folded-file placeholder."""
    return line.syntax_highlighting_disabled and (
        line.new_content == FOLDED_VIEWED_FILE_MESSAGE
        or line.old_content == FOLDED_VIEWED_FILE_MESSAGE
    )


def _collapsed_file_hunk(
    source: DiffHunk,
    *,
    filename: str,
    old_filename: str | None,
) -> DiffHunk:
    line = _collapsed_placeholder_line(source, filename)
    return DiffHunk(
        old_start=source.old_start,
        old_count=0,
        new_start=source.new_start,
        new_count=0,
        header="viewed file collapsed",
        lines=[line],
        starts_file=True,
        file_path=filename,
        file_old_path=old_filename,
        file_status=source.file_status,
        file_additions=source.file_additions,
        file_deletions=source.file_deletions,
    )


def _collapsed_placeholder_line(source: DiffHunk, filename: str) -> DiffLine:
    if source.new_count > 0 or source.new_start > 0:
        return DiffLine(
            old_line_no=None,
            new_line_no=max(1, source.new_start),
            old_content="",
            new_content=FOLDED_VIEWED_FILE_MESSAGE,
            file_path=filename,
            syntax_highlighting_disabled=True,
        )

    return DiffLine(
        old_line_no=max(1, source.old_start),
        new_line_no=None,
        old_content=FOLDED_VIEWED_FILE_MESSAGE,
        new_content="",
        file_path=filename,
        syntax_highlighting_disabled=True,
    )
