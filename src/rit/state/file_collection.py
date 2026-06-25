from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass

from rit.core.diff import ParsedFilePatch, ParsedFilePatchSummary
from rit.core.types import FileDiff
from rit.state.file_projection import file_from_diff, file_from_summary
from rit.state.models import FileViewedState, PRComment, PRFile


__all__ = (
    "FileAppendResult",
    "FileSelection",
    "append_file",
    "apply_file_summary",
    "apply_file_view_state",
    "apply_file_view_states",
    "apply_parsed_file",
    "cache_file_diff",
    "find_file",
    "load_file_diff",
    "select_file",
    "sync_file_comments",
)


@dataclass(frozen=True)
class FileAppendResult:
    """Result of inserting a PR file into the loaded file collection."""

    added: bool
    loaded_count: int
    total_count: int


@dataclass(frozen=True)
class FileSelection:
    """Store-ready result of selecting a changed file."""

    filename: str
    diff: FileDiff | None


def append_file(
    files: list[PRFile],
    files_by_filename: MutableMapping[str, PRFile],
    comments_by_file: Mapping[str, list[PRComment]],
    file: PRFile,
    *,
    total_count: int,
) -> FileAppendResult:
    """Append and index a file unless that filename is already loaded."""
    filename = file.filename
    if filename in files_by_filename:
        return FileAppendResult(
            added=False,
            loaded_count=len(files),
            total_count=total_count,
        )

    file.comments = comments_by_file.get(filename, [])
    files.append(file)
    files_by_filename[filename] = file
    loaded_count = len(files)
    return FileAppendResult(
        added=True,
        loaded_count=loaded_count,
        total_count=total_count if total_count >= loaded_count else loaded_count,
    )


def apply_parsed_file(
    files: list[PRFile],
    files_by_filename: MutableMapping[str, PRFile],
    file_diffs: MutableMapping[str, FileDiff],
    comments_by_file: Mapping[str, list[PRComment]],
    parsed_file: ParsedFilePatch,
    *,
    total_count: int,
) -> FileAppendResult:
    """Apply a parsed raw-diff file to file indexes and the diff cache."""
    diff = parsed_file.diff
    filename = diff.filename
    if filename in file_diffs:
        return FileAppendResult(
            added=False,
            loaded_count=len(files),
            total_count=total_count,
        )

    file = file_from_diff(diff)
    file.patch = parsed_file.patch
    file_diffs[filename] = diff

    existing = files_by_filename.get(filename)
    if existing is not None:
        _replace_file_metadata(existing, file)
        return FileAppendResult(
            added=False,
            loaded_count=len(files),
            total_count=total_count,
        )

    return append_file(
        files,
        files_by_filename,
        comments_by_file,
        file,
        total_count=total_count,
    )


def apply_file_summary(
    files: list[PRFile],
    files_by_filename: MutableMapping[str, PRFile],
    comments_by_file: Mapping[str, list[PRComment]],
    summary: ParsedFilePatchSummary,
    *,
    total_count: int,
) -> FileAppendResult:
    """Apply a lightweight raw-diff summary to file indexes."""
    if summary.filename in files_by_filename:
        return FileAppendResult(
            added=False,
            loaded_count=len(files),
            total_count=total_count,
        )

    return append_file(
        files,
        files_by_filename,
        comments_by_file,
        file_from_summary(summary),
        total_count=total_count,
    )


def find_file(
    filename: str,
    files: Sequence[PRFile],
    files_by_filename: MutableMapping[str, PRFile],
) -> PRFile | None:
    """Return a file by name, backfilling the filename index if needed."""
    file = files_by_filename.get(filename)
    if file is not None:
        return file

    file_count = len(files)
    if file_count == 0:
        return None
    if file_count == 1:
        item = files[0]
        item_filename = item.filename
        files_by_filename.setdefault(item_filename, item)
        return item if item_filename == filename else None

    for item in files:
        item_filename = item.filename
        files_by_filename.setdefault(item_filename, item)
        if item_filename == filename:
            return item
    return None


def cache_file_diff(
    filename: str,
    file_diffs: MutableMapping[str, FileDiff],
    diff: FileDiff,
) -> FileDiff:
    """Cache a parsed file diff while preserving an existing entry."""
    return file_diffs.setdefault(filename, diff)


def load_file_diff(
    filename: str,
    *,
    files: Sequence[PRFile],
    files_by_filename: MutableMapping[str, PRFile],
    file_diffs: MutableMapping[str, FileDiff],
    parse: Callable[[PRFile], FileDiff],
) -> FileDiff | None:
    """Return a cached or newly parsed diff for a loaded file."""
    cached = file_diffs.get(filename)
    if cached is not None:
        return cached

    file = find_file(filename, files, files_by_filename)
    if file is None:
        return None

    return cache_file_diff(filename, file_diffs, parse(file))


def select_file(
    filename: str,
    *,
    files: Sequence[PRFile],
    files_by_filename: MutableMapping[str, PRFile],
    file_diffs: Mapping[str, FileDiff],
) -> FileSelection | None:
    """Return selection data when a file or cached diff can be selected."""
    diff = file_diffs.get(filename)
    if find_file(filename, files, files_by_filename) is None and diff is None:
        return None
    return FileSelection(filename=filename, diff=diff)


def sync_file_comments(
    files: Sequence[PRFile],
    comments_by_file: Mapping[str, list[PRComment]],
) -> None:
    """Attach current comment lists to loaded files."""
    for file in files:
        file.comments = comments_by_file.get(file.filename, [])


def apply_file_view_states(
    files: Sequence[PRFile],
    states: Mapping[str, str],
) -> None:
    """Apply GitHub viewed-state strings to loaded files."""
    if not states:
        return

    for file in files:
        raw_state = states.get(file.filename)
        if not raw_state:
            continue
        try:
            file.viewer_viewed_state = FileViewedState(raw_state)
        except ValueError:
            continue


def apply_file_view_state(
    files: Sequence[PRFile],
    files_by_filename: MutableMapping[str, PRFile],
    filename: str,
    state: FileViewedState,
) -> None:
    """Apply one viewed state to a loaded file when present."""
    file = find_file(filename, files, files_by_filename)
    if file is not None:
        file.viewer_viewed_state = state


def _replace_file_metadata(existing: PRFile, replacement: PRFile) -> None:
    existing.status = replacement.status
    existing.additions = replacement.additions
    existing.deletions = replacement.deletions
    existing.changes = replacement.changes
    existing.patch = replacement.patch
    existing.previous_filename = replacement.previous_filename
