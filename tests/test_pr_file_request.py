import json
from typing import get_type_hints

from rit.services.pr_file_request import (
    fetch_file_content,
    fetch_pr_file_pages,
    fetch_pr_files,
    fetch_pr_files_page,
    file_content_request,
    parse_pr_files_page,
    parse_pr_files_result,
    pr_files_page_request,
)


def test_parse_pr_files_page_accepts_unknown_json_value_without_any_contract() -> None:
    assert get_type_hints(parse_pr_files_page)["data"] is object


def test_pr_files_page_request_targets_pull_files_page() -> None:
    assert pr_files_page_request(
        "owner/repo",
        123,
        page=2,
        per_page=50,
    ) == (
        "api",
        "/repos/owner/repo/pulls/123/files?per_page=50&page=2",
    )


def test_parse_pr_files_page_accepts_list_response() -> None:
    files = parse_pr_files_page(
        [
            {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@"},
            {"filename": "b.py", "status": "added"},
        ]
    )

    assert [file.filename for file in files] == ["a.py", "b.py"]


def test_parse_pr_files_result_decodes_json_page() -> None:
    files = parse_pr_files_result(
        json.dumps(
            [
                {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@"},
                {"filename": "b.py", "status": "added"},
            ]
        )
    )

    assert [file.filename for file in files] == ["a.py", "b.py"]


async def test_fetch_pr_files_page_runs_request_and_parses_files() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            [
                {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@"},
                {"filename": "b.py", "status": "added"},
            ]
        )

    files = await fetch_pr_files_page(
        "owner/repo",
        123,
        page=2,
        per_page=50,
        runner=runner,
    )

    assert [file.filename for file in files] == ["a.py", "b.py"]
    assert calls == [
        (
            [
                "api",
                "/repos/owner/repo/pulls/123/files?per_page=50&page=2",
            ],
            None,
        )
    ]


async def test_fetch_pr_file_pages_fetches_requested_pages_by_number() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        page = args[1].rsplit("page=", 1)[1]
        return json.dumps(
            [
                {
                    "filename": f"file-{page}.py",
                    "status": "modified",
                    "patch": "@@ -1 +1 @@",
                }
            ]
        )

    pages = await fetch_pr_file_pages(
        "owner/repo",
        123,
        pages=(2, 3),
        per_page=50,
        concurrency=1,
        runner=runner,
    )

    assert sorted(pages) == [2, 3]
    assert [file.filename for file in pages[2]] == ["file-2.py"]
    assert [file.filename for file in pages[3]] == ["file-3.py"]
    assert [call[0] for call in calls] == [
        ["api", "/repos/owner/repo/pulls/123/files?per_page=50&page=2"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=50&page=3"],
    ]
    assert all(input_text is None for _, input_text in calls)


async def test_fetch_pr_files_fetches_first_page_and_known_remaining_pages() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        if args[1].endswith("&page=1"):
            return json.dumps(
                [
                    {
                        "filename": f"file-{index}.py",
                        "status": "modified",
                        "patch": "@@ -1 +1 @@",
                    }
                    for index in range(100)
                ]
            )
        return json.dumps(
            [{"filename": "file-100.py", "status": "added"}]
        )

    files = await fetch_pr_files(
        "owner/repo",
        123,
        total_count=101,
        runner=runner,
    )

    assert len(files) == 101
    assert files[0].filename == "file-0.py"
    assert files[-1].filename == "file-100.py"
    assert [call[0] for call in calls] == [
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=1"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=2"],
    ]
    assert all(input_text is None for _, input_text in calls)


def test_parse_pr_files_page_wraps_single_object_response() -> None:
    files = parse_pr_files_page(
        {"filename": "a.py", "status": "removed", "previous_filename": "old.py"}
    )

    assert len(files) == 1
    assert files[0].filename == "a.py"


def test_file_content_request_asks_for_raw_github_media_type() -> None:
    assert file_content_request("owner/repo", "src/app.py", ref="deadbeef") == (
        "api",
        "/repos/owner/repo/contents/src/app.py?ref=deadbeef",
        "-H",
        "Accept: application/vnd.github.raw+json",
    )


async def test_fetch_file_content_runs_raw_content_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "file contents"

    result = await fetch_file_content(
        "owner/repo",
        "src/app.py",
        ref="deadbeef",
        runner=runner,
    )

    assert result == "file contents"
    assert calls == [
        (
            [
                "api",
                "/repos/owner/repo/contents/src/app.py?ref=deadbeef",
                "-H",
                "Accept: application/vnd.github.raw+json",
            ],
            None,
        )
    ]
