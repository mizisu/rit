from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from rit.state.file_ingest import (
    MutableFileIngestState,
    begin_file_ingest,
    load_file_metadata,
    load_raw_diff_text,
    load_streamed_diff_summaries,
)
from rit.state.models import LoadingState

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
        metadata_loaded = await _load_from_graphql_files(
            state, pr_number, source, on_progress
        )
        try:
            diff_loaded = await _load_from_streamed_raw_diff(
                state,
                pr_number,
                source,
                parse_summaries,
                on_progress,
            )
        except RuntimeError:
            if not metadata_loaded:
                raise
            diff_loaded = False
        if metadata_loaded or diff_loaded:
            state.files_loading = LoadingState.LOADED
            return None
        if await _load_from_raw_diff(state, pr_number, source, on_progress):
            return None
    except RuntimeError as error:
        return str(error)

    return "No changed files could be loaded"


async def _load_from_graphql_files(
    state: MutableFileIngestState,
    pr_number: int,
    source: Any,
    on_progress: ProgressCallback,
) -> bool:
    get_files = getattr(source, "get_pr_files", None)
    if get_files is None:
        return False

    return await load_file_metadata(
        state,
        pr_number=pr_number,
        get_files=get_files,
        on_progress=on_progress,
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
