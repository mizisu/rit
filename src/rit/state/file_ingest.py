from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from typing import Protocol

from rit.core.diff import (
    ParsedFilePatch,
    ParsedFilePatchSummary,
    parse_multi_file_patch,
)
from rit.core.types import FileDiff
from rit.services.pr_file_pagination import (
    PR_FILES_PER_PAGE,
    PRFilePageProgress,
    collect_ordered_page_items,
)
from rit.state.file_collection import (
    append_file,
    apply_file_summary,
    apply_parsed_file,
)
from rit.state.models import LoadingState, PR, PRComment, PRFile


__all__ = (
    "DiffSectionStreamer",
    "DiffSummaryParser",
    "MutableFileIngestState",
    "PRFilePageGetter",
    "PRFilePagesGetter",
    "RawDiffTextGetter",
    "append_file_batch",
    "append_file_summaries",
    "append_parsed_files",
    "begin_file_ingest",
    "file_page_progress",
    "load_raw_diff_text",
    "load_rest_file_pages",
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


class PRFilePageGetter(Protocol):
    def __call__(
        self,
        pr_number: int,
        *,
        page: int,
        per_page: int,
    ) -> Awaitable[Sequence[PRFile]]: ...


class PRFilePagesGetter(Protocol):
    def __call__(
        self,
        pr_number: int,
        *,
        pages: tuple[int, ...],
        per_page: int,
    ) -> Awaitable[Mapping[int, Sequence[PRFile]]]: ...


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
    """Append REST-loaded files and return the number newly inserted."""
    added_count = 0
    for file in batch:
        result = append_file(
            state.files,
            state.files_by_filename,
            state.comments_by_file,
            file,
            total_count=state.files_total_count,
        )
        state.files_loaded_count = result.loaded_count
        state.files_total_count = result.total_count
        if result.added:
            added_count += 1
    return added_count


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
    added_count = 0
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
        if result.added:
            added_count += 1
    return added_count


def file_page_progress(state: MutableFileIngestState) -> PRFilePageProgress:
    """Return REST pagination progress for the current ingest state."""
    changed_files = state.pr.changed_files if state.pr is not None else 0
    return PRFilePageProgress(
        loaded_count=state.files_loaded_count,
        total_count_hint=state.files_total_count,
        changed_files=changed_files,
    )


async def load_rest_file_pages(
    state: MutableFileIngestState,
    *,
    pr_number: int,
    get_page: PRFilePageGetter,
    get_pages: PRFilePagesGetter,
    on_progress: Callable[[], None],
    per_page: int = PR_FILES_PER_PAGE,
) -> bool:
    """Load PR files from the REST file pages source."""
    try:
        first_page = await get_page(
            pr_number,
            page=1,
            per_page=per_page,
        )
    except RuntimeError:
        return False

    if not first_page:
        return False

    append_file_batch(state, first_page)
    on_progress()

    if file_page_progress(state).rest_limit_exceeded:
        return False

    remaining_pages = file_page_progress(state).initial_remaining_pages()
    saw_last_page = len(first_page) < per_page
    while remaining_pages:
        page_chunk, remaining_pages = file_page_progress(state).next_page_chunk(
            remaining_pages
        )
        if not page_chunk:
            break

        try:
            page_batches = await get_pages(
                pr_number,
                pages=page_chunk,
                per_page=per_page,
            )
        except RuntimeError:
            return False

        collected = collect_ordered_page_items(
            page_chunk,
            page_batches,
            per_page=per_page,
        )
        append_file_batch(state, collected.items)
        saw_last_page = collected.saw_last_page
        if collected.saw_last_page:
            break
        if file_page_progress(state).rest_limit_exceeded:
            return False

    if not file_page_progress(state).rest_list_complete(
        saw_last_page=saw_last_page
    ):
        return False

    state.files_loading = LoadingState.LOADED
    on_progress()
    return True


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
