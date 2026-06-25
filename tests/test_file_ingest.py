from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import rit.state.file_ingest as file_ingest_module
from rit.core.diff import ParsedFilePatch, ParsedFilePatchSummary
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import LoadingState, PR, PRComment, PRFile
from rit.state.file_ingest import (
    append_file_batch,
    append_file_summaries,
    append_parsed_files,
    begin_file_ingest,
    file_page_progress,
    load_raw_diff_text,
    load_rest_file_pages,
    load_streamed_diff_summaries,
)


@dataclass
class IngestState:
    files_loading: LoadingState = LoadingState.IDLE
    files: list[PRFile] = field(default_factory=list)
    files_by_filename: dict[str, PRFile] = field(default_factory=dict)
    file_diffs: dict[str, FileDiff] = field(default_factory=dict)
    comments_by_file: dict[str, list[PRComment]] = field(default_factory=dict)
    files_loaded_count: int = 0
    files_total_count: int = 0
    pr: PR | None = None


def _parsed_file(filename: str = "src/app.py") -> ParsedFilePatch:
    diff = FileDiff(
        filename=filename,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[
                    DiffLine(
                        old_line_no=1,
                        new_line_no=None,
                        old_content="old",
                        is_deleted=True,
                    ),
                    DiffLine(
                        old_line_no=None,
                        new_line_no=1,
                        new_content="new",
                        is_added=True,
                    ),
                ],
            )
        ],
    )
    return ParsedFilePatch(diff=diff, patch="@@ -1 +1 @@\n-old\n+new")


def test_begin_file_ingest_resets_loaded_indexes_without_pr_total() -> None:
    state = IngestState(
        files=[PRFile(filename="old.py")],
        files_by_filename={"old.py": PRFile(filename="old.py")},
        file_diffs={"old.py": FileDiff(filename="old.py")},
        files_loaded_count=7,
        files_total_count=9,
    )

    begin_file_ingest(state)

    assert state.files_loading == LoadingState.LOADING
    assert state.files == []
    assert state.files_by_filename == {}
    assert state.file_diffs == {}
    assert state.files_loaded_count == 0
    assert state.files_total_count == 0


def test_begin_file_ingest_uses_pr_changed_file_count_as_total_hint() -> None:
    state = IngestState(pr=PR(number=123, changedFiles=12), files_total_count=3)

    begin_file_ingest(state)

    assert state.files_total_count == 12


def test_append_file_batch_updates_counts_and_indexes_comments() -> None:
    comment = PRComment(id=101, body="note", path="src/app.py")
    state = IngestState(
        comments_by_file={"src/app.py": [comment]},
        files_total_count=4,
    )

    append_file_batch(state, [PRFile(filename="src/app.py")])

    assert state.files_loaded_count == 1
    assert state.files_total_count == 4
    assert state.files_by_filename["src/app.py"] is state.files[0]
    assert state.files[0].comments == [comment]


def test_append_file_batch_preserves_known_total_without_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = IngestState(files_total_count=4)

    monkeypatch.setattr(
        file_ingest_module,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("known file total should not call max")
        ),
        raising=False,
    )

    append_file_batch(state, [PRFile(filename="src/app.py")])

    assert state.files_loaded_count == 1
    assert state.files_total_count == 4


def test_append_file_batch_indexes_files_without_single_file_append_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comment = PRComment(id=101, body="note", path="src/app.py")
    state = IngestState(
        comments_by_file={"src/app.py": [comment]},
        files_total_count=1,
    )

    def append_file_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("batch append should not allocate per-file results")

    monkeypatch.setattr(
        file_ingest_module,
        "append_file",
        append_file_forbidden,
        raising=False,
    )

    added = append_file_batch(
        state,
        [
            PRFile(filename="src/app.py"),
            PRFile(filename="src/lib.py"),
            PRFile(filename="src/app.py"),
        ],
    )

    assert added == 2
    assert state.files_loaded_count == 2
    assert state.files_total_count == 2
    assert [file.filename for file in state.files] == ["src/app.py", "src/lib.py"]
    assert state.files_by_filename == {
        "src/app.py": state.files[0],
        "src/lib.py": state.files[1],
    }
    assert state.files[0].comments == [comment]
    assert state.files[1].comments == []


def test_append_parsed_files_caches_diff_and_counts_added_files() -> None:
    state = IngestState()

    added = append_parsed_files(state, [_parsed_file()])

    assert added == 1
    assert state.files_loaded_count == 1
    assert state.files_total_count == 1
    assert state.files[0].filename == "src/app.py"
    assert state.file_diffs["src/app.py"].filename == "src/app.py"


def test_append_file_summaries_applies_lightweight_file_metadata() -> None:
    state = IngestState()

    added = append_file_summaries(
        state,
        [
            ParsedFilePatchSummary(
                filename="src/app.py",
                patch="diff --git a/src/app.py b/src/app.py",
                additions=3,
                deletions=2,
            )
        ],
    )

    assert added == 1
    assert state.files_loaded_count == 1
    assert state.files[0].additions == 3
    assert state.files[0].deletions == 2


def test_file_page_progress_reads_loaded_total_and_pr_changed_files() -> None:
    state = IngestState(
        pr=PR(number=123, changedFiles=101),
        files_loaded_count=100,
        files_total_count=0,
    )

    progress = file_page_progress(state)

    assert progress.initial_remaining_pages() == (2,)


@pytest.mark.asyncio
async def test_load_raw_diff_text_fetches_parses_and_posts_progress() -> None:
    state = IngestState(pr=PR(number=123, changedFiles=2), files_total_count=2)
    progress_updates: list[tuple[int, int]] = []
    calls: list[int] = []

    async def get_diff_text(pr_number: int) -> str:
        calls.append(pr_number)
        return (
            "diff --git a/one.py b/one.py\n"
            "index 1111111..2222222 100644\n"
            "--- a/one.py\n"
            "+++ b/one.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "diff --git a/two.py b/two.py\n"
            "index 3333333..4444444 100644\n"
            "--- a/two.py\n"
            "+++ b/two.py\n"
            "@@ -1 +1 @@\n"
            "-before\n"
            "+after\n"
        )

    loaded = await load_raw_diff_text(
        state,
        pr_number=123,
        get_diff_text=get_diff_text,
        on_progress=lambda: progress_updates.append(
            (state.files_loaded_count, state.files_total_count)
        ),
    )

    assert loaded is True
    assert calls == [123]
    assert progress_updates == [(2, 2)]
    assert state.files_loading == LoadingState.LOADED
    assert [file.filename for file in state.files] == ["one.py", "two.py"]
    assert list(state.file_diffs) == ["one.py", "two.py"]


@pytest.mark.asyncio
async def test_load_raw_diff_text_returns_false_without_parsed_files() -> None:
    state = IngestState()
    progress_updates = 0

    async def get_diff_text(pr_number: int) -> str:
        return ""

    loaded = await load_raw_diff_text(
        state,
        pr_number=123,
        get_diff_text=get_diff_text,
        on_progress=lambda: progress_updates + 1,
    )

    assert loaded is False
    assert state.files_loading == LoadingState.IDLE


@pytest.mark.asyncio
async def test_load_raw_diff_text_propagates_non_runtime_source_errors() -> None:
    state = IngestState()

    async def get_diff_text(pr_number: int) -> str:
        raise ValueError("bad raw diff source state")

    with pytest.raises(ValueError, match="bad raw diff source state"):
        await load_raw_diff_text(
            state,
            pr_number=123,
            get_diff_text=get_diff_text,
            on_progress=lambda: None,
        )

    assert state.files_loading == LoadingState.IDLE


@pytest.mark.asyncio
async def test_load_raw_diff_text_propagates_parser_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = IngestState()

    async def get_diff_text(pr_number: int) -> str:
        return "diff --git a/one.py b/one.py\n--- a/one.py\n+++ b/one.py\n"

    def parse_multi_file_patch(raw_diff: str):
        raise RuntimeError("parser failed")

    monkeypatch.setattr(
        file_ingest_module,
        "parse_multi_file_patch",
        parse_multi_file_patch,
    )

    with pytest.raises(RuntimeError, match="parser failed"):
        await load_raw_diff_text(
            state,
            pr_number=123,
            get_diff_text=get_diff_text,
            on_progress=lambda: None,
        )

    assert state.files_loading == LoadingState.IDLE


@pytest.mark.asyncio
async def test_load_streamed_diff_summaries_posts_first_and_final_batches() -> None:
    state = IngestState(pr=PR(number=123, changedFiles=2), files_total_count=2)
    parsed_batches: list[tuple[str, ...]] = []
    progress_updates: list[tuple[int, int]] = []

    async def stream_sections(pr_number: int):
        assert pr_number == 123
        yield "one"
        for index in range(2, 102):
            yield f"skip-{index}"
        yield "two"

    async def parse_summaries(sections: list[str]) -> int:
        parsed_batches.append(tuple(sections))
        if sections == ["one"]:
            return append_file_summaries(
                state,
                [
                    ParsedFilePatchSummary(
                        filename="one.py",
                        patch="diff --git a/one.py b/one.py",
                        additions=1,
                        deletions=1,
                    )
                ],
            )
        return append_file_summaries(
            state,
            [
                ParsedFilePatchSummary(
                    filename="two.py",
                    patch="diff --git a/two.py b/two.py",
                    additions=1,
                    deletions=1,
                )
            ],
        )

    loaded = await load_streamed_diff_summaries(
        state,
        pr_number=123,
        stream_sections=stream_sections,
        parse_summaries=parse_summaries,
        on_progress=lambda: progress_updates.append(
            (state.files_loaded_count, state.files_total_count)
        ),
    )

    assert loaded is True
    assert parsed_batches[0] == ("one",)
    assert len(parsed_batches[1]) == 100
    assert progress_updates == [(1, 2), (2, 2)]
    assert state.files_loading == LoadingState.LOADED
    assert [file.filename for file in state.files] == ["one.py", "two.py"]


@pytest.mark.asyncio
async def test_load_streamed_diff_summaries_returns_false_without_loaded_files() -> (
    None
):
    state = IngestState()
    progress_updates = 0

    async def stream_sections(pr_number: int):
        yield "empty"

    async def parse_summaries(sections: list[str]) -> int:
        return 0

    loaded = await load_streamed_diff_summaries(
        state,
        pr_number=123,
        stream_sections=stream_sections,
        parse_summaries=parse_summaries,
        on_progress=lambda: progress_updates + 1,
    )

    assert loaded is False
    assert state.files_loading == LoadingState.IDLE


@pytest.mark.asyncio
async def test_load_streamed_diff_summaries_propagates_parser_errors() -> None:
    state = IngestState()

    async def stream_sections(pr_number: int):
        yield "diff-section"

    async def parse_summaries(sections: list[str]) -> int:
        raise RuntimeError("parser failed")

    with pytest.raises(RuntimeError, match="parser failed"):
        await load_streamed_diff_summaries(
            state,
            pr_number=123,
            stream_sections=stream_sections,
            parse_summaries=parse_summaries,
            on_progress=lambda: None,
        )

    assert state.files_loading == LoadingState.IDLE


@pytest.mark.asyncio
async def test_load_rest_file_pages_posts_first_page_then_final_state() -> None:
    first_page = [PRFile(filename=f"file-{index}.py") for index in range(100)]
    second_page = [PRFile(filename="file-100.py")]
    state = IngestState(pr=PR(number=123, changedFiles=101), files_total_count=101)
    progress_updates: list[tuple[int, int]] = []
    page_calls: list[tuple[int, int, int]] = []
    multi_page_calls: list[tuple[int, tuple[int, ...], int]] = []

    async def get_page(
        pr_number: int,
        *,
        page: int,
        per_page: int,
    ) -> list[PRFile]:
        page_calls.append((pr_number, page, per_page))
        return first_page

    async def get_pages(
        pr_number: int,
        *,
        pages: tuple[int, ...],
        per_page: int,
    ) -> dict[int, list[PRFile]]:
        multi_page_calls.append((pr_number, pages, per_page))
        return {2: second_page}

    loaded = await load_rest_file_pages(
        state,
        pr_number=123,
        get_page=get_page,
        get_pages=get_pages,
        on_progress=lambda: progress_updates.append(
            (state.files_loaded_count, state.files_total_count)
        ),
    )

    assert loaded is True
    assert page_calls == [(123, 1, 100)]
    assert multi_page_calls == [(123, (2,), 100)]
    assert progress_updates == [(100, 101), (101, 101)]
    assert state.files_loading == LoadingState.LOADED
    assert [file.filename for file in state.files][-1] == "file-100.py"


@pytest.mark.asyncio
async def test_load_rest_file_pages_reuses_progress_between_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = [PRFile(filename=f"file-{index}.py") for index in range(100)]
    second_page = [PRFile(filename="file-100.py")]
    state = IngestState(pr=PR(number=123, changedFiles=101), files_total_count=101)
    progress_calls = 0
    real_file_page_progress = file_ingest_module.file_page_progress

    def counting_file_page_progress(state: IngestState):
        nonlocal progress_calls
        progress_calls += 1
        return real_file_page_progress(state)

    async def get_page(
        pr_number: int,
        *,
        page: int,
        per_page: int,
    ) -> list[PRFile]:
        return first_page

    async def get_pages(
        pr_number: int,
        *,
        pages: tuple[int, ...],
        per_page: int,
    ) -> dict[int, list[PRFile]]:
        return {2: second_page}

    monkeypatch.setattr(
        file_ingest_module,
        "file_page_progress",
        counting_file_page_progress,
    )

    loaded = await load_rest_file_pages(
        state,
        pr_number=123,
        get_page=get_page,
        get_pages=get_pages,
        on_progress=lambda: None,
    )

    assert loaded is True
    assert progress_calls == 2


@pytest.mark.asyncio
async def test_load_rest_file_pages_returns_false_when_rest_limit_is_exceeded() -> (
    None
):
    first_page = [PRFile(filename=f"file-{index}.py") for index in range(100)]
    state = IngestState(pr=PR(number=123, changedFiles=3001), files_total_count=3001)
    progress_updates: list[int] = []

    async def get_page(
        pr_number: int,
        *,
        page: int,
        per_page: int,
    ) -> list[PRFile]:
        return first_page

    async def get_pages(
        pr_number: int,
        *,
        pages: tuple[int, ...],
        per_page: int,
    ) -> dict[int, list[PRFile]]:
        raise AssertionError("remaining pages should not be fetched")

    loaded = await load_rest_file_pages(
        state,
        pr_number=123,
        get_page=get_page,
        get_pages=get_pages,
        on_progress=lambda: progress_updates.append(state.files_loaded_count),
    )

    assert loaded is False
    assert progress_updates == [100]
    assert state.files_loading == LoadingState.IDLE


@pytest.mark.asyncio
async def test_load_rest_file_pages_propagates_non_runtime_first_page_errors() -> None:
    state = IngestState()

    async def get_page(
        pr_number: int,
        *,
        page: int,
        per_page: int,
    ) -> list[PRFile]:
        raise ValueError("bad REST page adapter state")

    async def get_pages(
        pr_number: int,
        *,
        pages: tuple[int, ...],
        per_page: int,
    ) -> dict[int, list[PRFile]]:
        raise AssertionError("remaining pages should not be fetched")

    with pytest.raises(ValueError, match="bad REST page adapter state"):
        await load_rest_file_pages(
            state,
            pr_number=123,
            get_page=get_page,
            get_pages=get_pages,
            on_progress=lambda: None,
        )

    assert state.files_loading == LoadingState.IDLE
