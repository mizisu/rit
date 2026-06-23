import asyncio

import pytest

from rit.core.diff import parse_patch
from rit.core.types import FileDiff
from rit.state.models import PRFile
from rit.ui.components.combined_diff import (
    build_combined_diff_document,
    load_missing_combined_file_diffs,
)


def test_combined_diff_document_records_file_starts_and_line_lookup() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    files = [
        PRFile(filename="one.py", status="modified", patch=patch),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]
    file_diffs = {
        filename: parse_patch(patch, filename) for filename in ["one.py", "two.py"]
    }

    document = build_combined_diff_document(files, file_diffs)

    assert document is not None
    assert document.diff.filename == "All files"
    assert document.diff.show_hunk_headers is False
    assert document.file_line_starts == {"one.py": 0, "two.py": 2}
    assert document.line_index_for_location("one.py", 1, "LEFT") == 0
    assert document.line_index_for_location("one.py", 1, "RIGHT") == 1
    assert document.line_index_for_location("two.py", 1, "LEFT") == 2
    assert document.line_index_for_location("two.py", 1, "RIGHT") == 3
    assert document.file_for_line(0) == "one.py"
    assert document.file_for_line(3) == "two.py"


def test_combined_diff_document_preserves_file_metadata_on_start_hunks() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    files = [
        PRFile(
            filename="renamed.py",
            status="renamed",
            patch=patch,
            previousFilename="old.py",
            additions=9,
            deletions=4,
        )
    ]
    file_diffs = {"renamed.py": parse_patch(patch, "renamed.py")}

    document = build_combined_diff_document(files, file_diffs)

    assert document is not None
    hunk = document.diff.hunks[0]
    assert hunk.starts_file is True
    assert hunk.file_path == "renamed.py"
    assert hunk.file_old_path == "old.py"
    assert hunk.file_status == "renamed"
    assert hunk.file_additions == 9
    assert hunk.file_deletions == 4


def test_combined_diff_document_adds_placeholder_for_files_without_textual_changes() -> (
    None
):
    files = [
        PRFile(
            filename="image.png",
            status="modified",
            additions=0,
            deletions=0,
        )
    ]
    file_diffs = {"image.png": FileDiff(filename="image.png", hunks=[])}

    document = build_combined_diff_document(files, file_diffs)

    assert document is not None
    assert document.file_line_starts == {"image.png": 0}
    hunk = document.diff.hunks[0]
    assert hunk.starts_file is True
    assert hunk.file_path == "image.png"
    assert hunk.header == "no textual changes"
    assert hunk.lines[0].file_path == "image.png"
    assert hunk.lines[0].new_content == "No textual changes"
    assert document.file_for_line(0) == "image.png"
    assert document.line_index_for_location("image.png", 1, "RIGHT") is None


def test_combined_diff_document_returns_none_until_every_diff_is_loaded() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    files = [
        PRFile(filename="one.py", status="modified", patch=patch),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]

    document = build_combined_diff_document(
        files,
        {"one.py": parse_patch(patch, "one.py")},
    )

    assert document is None


@pytest.mark.asyncio
async def test_load_missing_combined_file_diffs_skips_cached_files() -> None:
    loaded_diffs = {"one.py": FileDiff(filename="one.py")}
    calls: list[str] = []

    async def load_diff(filename: str) -> FileDiff:
        calls.append(filename)
        return FileDiff(filename=filename)

    await load_missing_combined_file_diffs(
        ("one.py", "two.py", "three.py"),
        loaded_diffs,
        load_diff,
        concurrency=2,
    )

    assert calls == ["two.py", "three.py"]


@pytest.mark.asyncio
async def test_load_missing_combined_file_diffs_limits_concurrency() -> None:
    started: list[str] = []
    first_batch_started = asyncio.Event()
    release_loads = asyncio.Event()

    async def load_diff(filename: str) -> FileDiff:
        started.append(filename)
        if len(started) == 2:
            first_batch_started.set()
        await release_loads.wait()
        return FileDiff(filename=filename)

    task = asyncio.create_task(
        load_missing_combined_file_diffs(
            ("one.py", "two.py", "three.py"),
            {},
            load_diff,
            concurrency=2,
        )
    )
    try:
        await asyncio.wait_for(first_batch_started.wait(), timeout=1.0)
        await asyncio.sleep(0)

        assert started == ["one.py", "two.py"]
    finally:
        release_loads.set()

    await task

    assert started == ["one.py", "two.py", "three.py"]
