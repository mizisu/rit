from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Sequence,
)
from typing import Protocol

from rit.core.diff import (
    ParsedFilePatch,
    ParsedFilePatchSummary,
    parse_multi_file_patch,
)
from rit.core.types import FileDiff
from rit.state.file_collection import (
    apply_file_summary,
    apply_parsed_file,
)
from rit.state.models import PR, LoadingState, PRComment, PRFile

__all__ = (
    "DiffSectionStreamer",
    "DiffSummaryParser",
    "MutableFileIngestState",
    "RawDiffTextGetter",
    "append_file_batch",
    "append_file_summaries",
    "append_parsed_files",
    "begin_file_ingest",
    "load_raw_diff_text",
    "load_file_metadata",
    "load_streamed_diff_summaries",
)


class MutableFileIngestState(Protocol):
    files_loading: LoadingState
    files: list[PRFile]
    files_by_filename: dict[str, PRFile]
    file_diffs: dict[str, FileDiff]
    comments_by_file: dict[str, list[PRComment]]
    files_loaded_count: int
    files_total_count: int
    pr: PR | None


class PRFilesGetter(Protocol):
    def __call__(
        self,
        pr_number: int,
        *,
        total_count: int | None = None,
    ) -> Awaitable[Sequence[PRFile]]: ...


class DiffSectionStreamer(Protocol):
    def __call__(self, pr_number: int) -> AsyncIterator[str]: ...


class DiffSummaryParser(Protocol):
    def __call__(self, sections: list[str]) -> Awaitable[int]: ...


class RawDiffTextGetter(Protocol):
    def __call__(self, pr_number: int) -> Awaitable[str]: ...


def begin_file_ingest(state: MutableFileIngestState) -> None:
    """Reset loaded file indexes for a new ingest run."""
    state.files_loading = LoadingState.LOADING
    state.files = []
    state.files_by_filename = {}
    state.file_diffs = {}
    state.files_loaded_count = 0
    state.files_total_count = state.pr.changed_files if state.pr is not None else 0


def append_file_batch(
    state: MutableFileIngestState,
    batch: Sequence[PRFile],
) -> int:
    """Append remotely loaded files and return the number newly inserted."""
    if not batch:
        return 0

    files = state.files
    files_by_filename = state.files_by_filename
    comments_by_file = state.comments_by_file
    total_count = state.files_total_count
    added_count = 0

    for file in batch:
        filename = file.filename
        if filename in files_by_filename:
            continue

        file.comments = comments_by_file.get(filename, [])
        files.append(file)
        files_by_filename[filename] = file
        added_count += 1

    loaded_count = len(files)
    state.files_loaded_count = loaded_count
    if added_count:
        state.files_total_count = (
            total_count if total_count >= loaded_count else loaded_count
        )
    return added_count


async def load_file_metadata(
    state: MutableFileIngestState,
    *,
    pr_number: int,
    get_files: PRFilesGetter,
    on_progress: Callable[[], None],
) -> bool:
    """Load changed-file metadata from a complete GraphQL source."""
    total_count = state.files_total_count or None
    try:
        files = await get_files(pr_number, total_count=total_count)
    except RuntimeError:
        return False
    if not files:
        return False
    append_file_batch(state, files)
    on_progress()
    return True


def append_parsed_files(
    state: MutableFileIngestState,
    parsed_files: Iterable[ParsedFilePatch],
) -> int:
    """Apply parsed raw-diff files to the ingest state."""
    added_count = 0
    for parsed_file in parsed_files:
        result = apply_parsed_file(
            state.files,
            state.files_by_filename,
            state.file_diffs,
            state.comments_by_file,
            parsed_file,
            total_count=state.files_total_count,
        )
        state.files_loaded_count = result.loaded_count
        state.files_total_count = result.total_count
        if result.added:
            added_count += 1
    return added_count


def append_file_summaries(
    state: MutableFileIngestState,
    summaries: Iterable[ParsedFilePatchSummary],
) -> int:
    """Apply lightweight streamed raw-diff summaries to the ingest state."""
    applied_count = 0
    for summary in summaries:
        result = apply_file_summary(
            state.files,
            state.files_by_filename,
            state.comments_by_file,
            summary,
            total_count=state.files_total_count,
        )
        state.files_loaded_count = result.loaded_count
        state.files_total_count = result.total_count
        applied_count += 1
    return applied_count


async def load_raw_diff_text(
    state: MutableFileIngestState,
    *,
    pr_number: int,
    get_diff_text: RawDiffTextGetter,
    on_progress: Callable[[], None],
) -> bool:
    """Load PR files from the full raw diff text source."""
    try:
        raw_diff = await get_diff_text(pr_number)
    except RuntimeError:
        return False
    parsed_files = await asyncio.to_thread(parse_multi_file_patch, raw_diff)

    if not parsed_files:
        return False

    append_parsed_files(state, parsed_files)
    state.files_loading = LoadingState.LOADED
    on_progress()
    return True


async def load_streamed_diff_summaries(
    state: MutableFileIngestState,
    *,
    pr_number: int,
    stream_sections: DiffSectionStreamer,
    parse_summaries: DiffSummaryParser,
    on_progress: Callable[[], None],
) -> bool:
    """Load lightweight file summaries from a streamed raw diff source."""
    loaded_any = False
    sections: list[str] = []
    batch_size = 1
    posted_count = 0

    try:
        stream = stream_sections(pr_number)
    except RuntimeError:
        return False

    while True:
        try:
            section = await anext(stream)
        except StopAsyncIteration:
            break
        except RuntimeError:
            if loaded_any:
                raise
            return False

        sections.append(section)
        if len(sections) < batch_size:
            continue

        parsed_count = await parse_summaries(sections)
        sections.clear()
        if parsed_count:
            loaded_any = True
            posted_count = state.files_loaded_count
            on_progress()
        batch_size = 100

    if sections:
        parsed_count = await parse_summaries(sections)
        if parsed_count:
            loaded_any = True

    if not loaded_any:
        return False

    state.files_loading = LoadingState.LOADED
    if state.files_loaded_count != posted_count:
        on_progress()
    return True
