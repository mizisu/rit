import json

import pytest

from rit.services.graphql_request import mapping
from rit.services.pr_file_request import (
    fetch_file_content,
    fetch_pr_files,
    parse_pr_files_page,
)
from rit.state.file_projection import diff_from_file_patch
from rit.state.models import FileViewedState


def _files_page(
    nodes: list[dict[str, object]],
    *,
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "files": {
                        "nodes": nodes,
                        "pageInfo": {
                            "hasNextPage": has_next_page,
                            "endCursor": end_cursor,
                        },
                    },
                    "baseRefOid": "base-sha",
                    "headRefOid": "head-sha",
                }
            }
        }
    }


def test_parse_pr_files_page_projects_graphql_metadata() -> None:
    page = parse_pr_files_page(
        _files_page(
            [
                {
                    "path": "src/app.py",
                    "changeType": "RENAMED",
                    "additions": 3,
                    "deletions": 2,
                    "viewerViewedState": "VIEWED",
                }
            ],
            has_next_page=True,
            end_cursor="cursor-1",
        )
    )

    assert page.has_next_page is True
    assert page.end_cursor == "cursor-1"
    assert len(page.files) == 1
    assert page.files[0].filename == "src/app.py"
    assert page.files[0].status == "renamed"
    assert page.files[0].changes == 5
    assert page.files[0].viewer_viewed_state is FileViewedState.VIEWED


async def test_fetch_pr_files_uses_graphql_cursor_pagination() -> None:
    calls: list[dict[str, object]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if "fragment BlobText" in payload["query"]:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "base0": {"text": "old", "isBinary": False},
                            "head0": {"text": "new", "isBinary": False},
                            "head1": {"text": "added", "isBinary": False},
                        }
                    }
                }
            )
        after = payload["variables"]["after"]
        if after is None:
            return json.dumps(
                _files_page(
                    [{"path": "a.py", "changeType": "MODIFIED"}],
                    has_next_page=True,
                    end_cursor="cursor-1",
                )
            )
        return json.dumps(_files_page([{"path": "b.py", "changeType": "ADDED"}]))

    files = await fetch_pr_files("owner", "repo", 123, runner=runner)

    assert [file.filename for file in files] == ["a.py", "b.py"]
    file_calls = [call for call in calls if "files(first:" in str(call["query"])]
    assert [mapping(call["variables"])["after"] for call in file_calls] == [
        None,
        "cursor-1",
    ]
    assert files[0].patch.startswith("diff --git a/a.py b/a.py")
    assert files[1].patch.startswith("diff --git a/b.py b/b.py\nnew file mode")


async def test_fetch_pr_files_stops_at_known_total() -> None:
    calls = 0

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        nonlocal calls
        calls += 1
        assert input_text is not None
        if "fragment BlobText" in json.loads(input_text)["query"]:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "base0": {"text": "old"},
                            "head0": {"text": "new"},
                        }
                    }
                }
            )
        return json.dumps(
            _files_page(
                [{"path": "a.py", "changeType": "MODIFIED"}],
                has_next_page=True,
                end_cursor="cursor-1",
            )
        )

    files = await fetch_pr_files("owner", "repo", 123, total_count=1, runner=runner)

    assert [file.filename for file in files] == ["a.py"]
    assert calls == 2


@pytest.mark.parametrize(
    ("change_type", "operation", "hunk", "counts"),
    [
        (
            "RENAMED",
            "rename",
            "@@ -1,2 +1,2 @@\n keep\n-old\n+new\n",
            (1, 1),
        ),
        ("RENAMED", "rename", "", (0, 0)),
        (
            "COPIED",
            "copy",
            "@@ -1,2 +1,2 @@\n keep\n-old\n+new\n",
            (1, 1),
        ),
    ],
)
async def test_fetch_pr_files_preserves_renamed_file_patch(
    change_type: str,
    operation: str,
    hunk: str,
    counts: tuple[int, int],
) -> None:
    patch = (
        "diff --git a/src/old.py b/src/new.py\n"
        f"{operation} from src/old.py\n"
        f"{operation} to src/new.py\n"
    )
    if hunk:
        patch += "--- a/src/old.py\n+++ b/src/new.py\n" + hunk
    calls: list[list[str]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append(args)
        if args[:2] == ["pr", "diff"]:
            return patch
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        payload = json.loads(input_text)
        if "fragment BlobText" in payload["query"]:
            assert payload["variables"] == {
                "owner": "owner",
                "repo": "repo",
                "head0Expression": "head-sha:added.py",
            }
            return json.dumps({"data": {"repository": {"head0": {"text": "added"}}}})
        return json.dumps(
            _files_page(
                [
                    {
                        "path": "src/new.py",
                        "changeType": change_type,
                        "additions": counts[0],
                        "deletions": counts[1],
                        "viewerViewedState": "VIEWED",
                    },
                    {"path": "added.py", "changeType": "ADDED"},
                ]
            )
        )

    files = await fetch_pr_files("owner", "repo", 123, runner=runner)

    file = files[0]
    assert file.status == change_type.lower()
    assert file.previous_filename == "src/old.py"
    assert file.patch == patch.rstrip("\n")
    assert file.viewer_viewed_state is FileViewedState.VIEWED
    diff = diff_from_file_patch(file)
    assert diff.old_filename == "src/old.py"
    assert diff.change_counts == counts
    assert not diff.is_new
    assert "new file mode" in files[1].patch
    assert [call for call in calls if call[:2] == ["pr", "diff"]] == [
        ["pr", "diff", "123", "--repo", "owner/repo", "--color", "never"]
    ]


@pytest.mark.parametrize(
    "raw_diff",
    ["", "diff --git a/src/new.py b/src/new.py\nnew file mode 100644\n"],
)
async def test_fetch_pr_files_rejects_missing_rename_source(raw_diff: str) -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args[:2] == ["pr", "diff"]:
            return raw_diff
        assert input_text is not None
        assert "files(first:" in json.loads(input_text)["query"]
        return json.dumps(
            _files_page([{"path": "src/new.py", "changeType": "RENAMED"}])
        )

    with pytest.raises(RuntimeError, match="source path for 'src/new.py'"):
        await fetch_pr_files("owner", "repo", 123, runner=runner)


async def test_fetch_file_content_reads_graphql_blob_text() -> None:
    calls: list[dict[str, object]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        calls.append(json.loads(input_text))
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "object": {
                            "text": "print('hello')\n",
                            "isBinary": False,
                            "isTruncated": False,
                        }
                    }
                }
            }
        )

    content = await fetch_file_content(
        "owner", "repo", "src/app.py", ref="deadbeef", runner=runner
    )

    assert content == "print('hello')\n"
    assert mapping(calls[0]["variables"])["expression"] == "deadbeef:src/app.py"


@pytest.mark.parametrize("field", ["isBinary", "isTruncated"])
async def test_fetch_file_content_rejects_unavailable_graphql_text(field: str) -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        return json.dumps(
            {"data": {"repository": {"object": {"text": None, field: True}}}}
        )

    with pytest.raises(ValueError):
        await fetch_file_content(
            "owner", "repo", "asset.bin", ref="deadbeef", runner=runner
        )
