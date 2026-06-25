from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from rit.core.pagination import PR_FILES_PER_PAGE
from rit.state.file_ingest import (
    MutableFileIngestState,
    begin_file_ingest,
    load_raw_diff_text,
    load_rest_file_pages,
    load_streamed_diff_summaries,
)

__all__ = ("FileSummaryParser", "load_file_workspace")


class FileSummaryParser(Protocol):
    def __call__(self, sections: list[str]) -> Awaitable[int]: ...


ProgressCallback = Callable[[], None]


async def load_file_workspace(
    state: MutableFileIngestState,
    *,
    pr_number: int,
    source: Any,
    parse_summaries: FileSummaryParser,
    on_progress: ProgressCallback,
) -> str | None:
    """Load the PR file workspace from the fastest available source."""
    begin_file_ingest(state)

    try:
        if await _load_from_rest_pages(state, pr_number, source, on_progress):
            return None
        if await _load_from_streamed_raw_diff(
            state,
            pr_number,
            source,
            parse_summaries,
            on_progress,
        ):
            return None
        if await _load_from_raw_diff(state, pr_number, source, on_progress):
            return None
    except RuntimeError as error:
        return str(error)

    return "No changed files could be loaded"


async def _load_from_rest_pages(
    state: MutableFileIngestState,
    pr_number: int,
    source: Any,
    on_progress: ProgressCallback,
) -> bool:
    get_page = getattr(source, "get_pr_files_page", None)
    get_pages = getattr(source, "get_pr_file_pages", None)
    if get_page is None or get_pages is None:
        return False

    return await load_rest_file_pages(
        state,
        pr_number=pr_number,
        get_page=get_page,
        get_pages=get_pages,
        on_progress=on_progress,
        per_page=PR_FILES_PER_PAGE,
    )


async def _load_from_streamed_raw_diff(
    state: MutableFileIngestState,
    pr_number: int,
    source: Any,
    parse_summaries: FileSummaryParser,
    on_progress: ProgressCallback,
) -> bool:
    stream_sections = getattr(source, "iter_pr_diff_sections", None)
    if stream_sections is None:
        return False

    return await load_streamed_diff_summaries(
        state,
        pr_number=pr_number,
        stream_sections=stream_sections,
        parse_summaries=parse_summaries,
        on_progress=on_progress,
    )


async def _load_from_raw_diff(
    state: MutableFileIngestState,
    pr_number: int,
    source: Any,
    on_progress: ProgressCallback,
) -> bool:
    get_diff_text = getattr(source, "get_pr_diff_text", None)
    if get_diff_text is None:
        return False

    return await load_raw_diff_text(
        state,
        pr_number=pr_number,
        get_diff_text=get_diff_text,
        on_progress=on_progress,
    )
