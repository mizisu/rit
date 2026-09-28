import asyncio
import json

import pytest

from rit.services.graphql_request import mapping
from rit.services.pr_file_request import (
    _populate_file_list_patches,
    fetch_file_content,
    fetch_pr_files,
    parse_pr_files_page,
)
from rit.state.file_projection import diff_from_file_patch
from rit.state.models import PR, FileViewedState, PRFile
from rit.state.store import PRStore
from rit.ui.widgets.diff_full_file_preview import build_full_file_diff

CANONICAL_DIFF_ARGS = [
    "api",
    "repos/owner/repo/pulls/123",
    "-H",
    "Accept: application/vnd.github.v3.diff",
]

MODIFIED_PATCH = (
    "diff --git a/a.py b/a.py\n"
    "--- a/a.py\n+++ b/a.py\n"
    "@@ -2,2 +2,2 @@\n-old\n+new\n keep\n"
)
ADDED_PATCH = (
    "diff --git a/b.py b/b.py\nnew file mode 100644\n"
    "--- /dev/null\n+++ b/b.py\n@@ -0,0 +1 @@\n+added\n"
)


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


async def test_fetch_pr_files_uses_graphql_cursor_pagination_and_canonical_patch() -> (
    None
):
    calls: list[dict[str, object]] = []
    diff_calls: list[list[str]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args == CANONICAL_DIFF_ARGS:
            assert input_text is None
            diff_calls.append(args)
            return ADDED_PATCH + MODIFIED_PATCH
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        payload = json.loads(input_text)
        assert "files(first:" in payload["query"]
        calls.append(payload)
        if payload["variables"]["after"] is None:
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
    assert [mapping(call["variables"])["after"] for call in calls] == [
        None,
        "cursor-1",
    ]
    assert files[0].patch == MODIFIED_PATCH.rstrip("\n")
    assert files[1].patch == ADDED_PATCH.rstrip("\n")
    assert diff_calls == [CANONICAL_DIFF_ARGS]


@pytest.mark.parametrize("changed_ref", [None, "baseRefOid", "headRefOid"])
async def test_fetch_pr_files_falls_back_to_paginated_patches_on_diff_limit(
    changed_ref: str | None,
) -> None:
    nodes = [
        {
            "path": f"file-{index}.py",
            "changeType": "MODIFIED",
            "additions": 1,
            "deletions": 1,
            "viewerViewedState": "VIEWED",
        }
        for index in range(301)
    ]
    body = "@@ -2,2 +2,2 @@\n-old\n+new\n keep"
    rest_requested = False
    cursors: list[str | None] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        nonlocal rest_requested
        if args == CANONICAL_DIFF_ARGS:
            raise RuntimeError(
                "gh: diff exceeded the maximum number of files (HTTP 406)"
            )
        if args == [
            "api",
            "repos/owner/repo/pulls/123/files?per_page=100",
            "--paginate",
        ]:
            rest_requested = True
            return "\n".join(
                json.dumps(
                    [
                        {
                            "filename": node["path"],
                            "status": "modified",
                            "additions": 1,
                            "deletions": 1,
                            "patch": body,
                        }
                        for node in nodes[start : start + 100]
                    ]
                )
                for start in range(0, len(nodes), 100)
            )
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        variables = json.loads(input_text)["variables"]
        if variables["first"] == 1:
            assert rest_requested
            page = _files_page(nodes[:1])
            if changed_ref is not None:
                pr = dict(
                    mapping(mapping(mapping(page["data"])["repository"])["pullRequest"])
                )
                pr[changed_ref] = "changed-revision"
                page = {"data": {"repository": {"pullRequest": pr}}}
            return json.dumps(page)
        cursors.append(variables["after"])
        start = int(variables["after"] or 0)
        return json.dumps(
            _files_page(
                nodes[start : start + 100],
                has_next_page=start < 300,
                end_cursor=str(start + 100),
            )
        )

    if changed_ref is not None:
        with pytest.raises(RuntimeError, match="PR changed while loading"):
            await fetch_pr_files("owner", "repo", 123, runner=runner)
        return
    files = await fetch_pr_files("owner", "repo", 123, runner=runner)

    assert cursors == [None, "100", "200", "300"]
    assert len(files) == 301
    assert all(file.viewer_viewed_state is FileViewedState.VIEWED for file in files)
    assert all(file.patch == body for file in files)
    diff = diff_from_file_patch(files[-1])
    assert diff.hunks[0].lines[0].old_line_no == 2
    assert diff.hunks[0].lines[-1].new_line_no == 3


@pytest.mark.parametrize(
    ("patch", "additions", "deletions", "valid"),
    [
        ("@@ -1 +1 @@\n-old\n+new", 1, 1, True),
        ("@@ -1 +1 @@\n-a\u2028b\n+c\u2028d", 1, 1, True),
        (
            "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file",
            1,
            1,
            True,
        ),
        (None, 0, 0, True),
        (None, 1, 1, False),
        ("@@ -1,2 +1,2 @@\n-old\n+new", 1, 1, False),
        ("@@ -1 +1 @@\n-old\n+new", 2, 1, False),
    ],
)
def test_file_list_patches_reject_incomplete_hunks(
    patch: str | None,
    additions: int,
    deletions: int,
    valid: bool,
) -> None:
    file = PRFile(
        filename="new name.py",
        status="renamed",
        additions=additions,
        deletions=deletions,
    )
    result = json.dumps(
        [
            {
                "filename": file.filename,
                "previous_filename": "old name.py",
                "status": "renamed",
                "additions": additions,
                "deletions": deletions,
                "patch": patch,
            }
        ]
    )
    if valid:
        _populate_file_list_patches([file], result)
        assert file.patch == (patch or "")
        assert file.previous_filename == "old name.py"
    else:
        with pytest.raises(RuntimeError, match="incomplete or invalid patch"):
            _populate_file_list_patches([file], result)


@pytest.mark.parametrize(
    "filenames", [[], ["a.py", "a.py"], ["a.py", "b.py"], ["b.py"]]
)
def test_file_list_patches_reject_incomplete_or_duplicate_file_lists(
    filenames: list[str],
) -> None:
    result = json.dumps([{"filename": name} for name in filenames])
    with pytest.raises(RuntimeError, match="changed-file list"):
        _populate_file_list_patches([PRFile(filename="a.py")], result)


@pytest.mark.parametrize("field", ["baseRefOid", "headRefOid"])
async def test_fetch_pr_files_rejects_revision_changes_between_pages(
    field: str,
) -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args == CANONICAL_DIFF_ARGS:
            return MODIFIED_PATCH + ADDED_PATCH
        assert input_text is not None
        after = json.loads(input_text)["variables"]["after"]
        page = _files_page(
            [{"path": "a.py" if after is None else "b.py", "changeType": "MODIFIED"}],
            has_next_page=after is None,
            end_cursor="next" if after is None else None,
        )
        if after is not None:
            pr = dict(
                mapping(mapping(mapping(page["data"])["repository"])["pullRequest"])
            )
            pr[field] = "different-revision"
            page = {"data": {"repository": {"pullRequest": pr}}}
        return json.dumps(page)

    with pytest.raises(RuntimeError, match="PR changed while loading"):
        await fetch_pr_files("owner", "repo", 123, runner=runner)


async def test_fetch_pr_files_stops_at_known_total() -> None:
    calls = 0

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        nonlocal calls
        calls += 1
        if args == CANONICAL_DIFF_ARGS:
            return MODIFIED_PATCH + ADDED_PATCH
        assert input_text is not None
        assert "files(first:" in json.loads(input_text)["query"]
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


async def test_fetch_pr_files_starts_patch_and_metadata_concurrently() -> None:
    metadata_started = asyncio.Event()
    patch_started = asyncio.Event()

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args == CANONICAL_DIFF_ARGS:
            patch_started.set()
            await metadata_started.wait()
            return MODIFIED_PATCH
        metadata_started.set()
        await patch_started.wait()
        return json.dumps(_files_page([{"path": "a.py", "changeType": "MODIFIED"}]))

    files = await asyncio.wait_for(
        fetch_pr_files("owner", "repo", 123, runner=runner), timeout=2
    )

    assert files[0].patch == MODIFIED_PATCH.rstrip("\n")


@pytest.mark.parametrize("failed_request", ["metadata", "patch", None])
async def test_fetch_pr_files_cleans_up_requests_on_failure_or_cancellation(
    failed_request: str | None,
) -> None:
    started = {name: asyncio.Event() for name in ("metadata", "patch")}
    finished: set[str] = set()

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        name = "patch" if args == CANONICAL_DIFF_ARGS else "metadata"
        started[name].set()
        try:
            await asyncio.gather(*(event.wait() for event in started.values()))
            if name == failed_request:
                raise RuntimeError("request failed")
            await asyncio.Future()
        finally:
            finished.add(name)
        raise AssertionError("blocked request should be cancelled")

    task = asyncio.create_task(fetch_pr_files("owner", "repo", 123, runner=runner))
    if failed_request is None:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())), timeout=2
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    else:
        with pytest.raises(RuntimeError, match="request failed"):
            await asyncio.wait_for(task, timeout=2)

    assert finished == {"metadata", "patch"}


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
        if args == CANONICAL_DIFF_ARGS:
            return patch + ADDED_PATCH
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        assert "files(first:" in json.loads(input_text)["query"]
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
                    {"path": "b.py", "changeType": "ADDED"},
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
    assert files[1].patch == ADDED_PATCH.rstrip("\n")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "raw_diff",
    ["", "diff --git a/src/new.py b/src/new.py\nnew file mode 100644\n"],
)
async def test_fetch_pr_files_rejects_missing_rename_source(raw_diff: str) -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args == CANONICAL_DIFF_ARGS:
            return raw_diff
        assert input_text is not None
        assert "files(first:" in json.loads(input_text)["query"]
        return json.dumps(
            _files_page([{"path": "src/new.py", "changeType": "RENAMED"}])
        )

    with pytest.raises(RuntimeError, match="source path for 'src/new.py'"):
        await fetch_pr_files("owner", "repo", 123, runner=runner)


@pytest.mark.parametrize("raw_diff", ["", ADDED_PATCH])
async def test_fetch_pr_files_rejects_missing_canonical_patch(raw_diff: str) -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args == CANONICAL_DIFF_ARGS:
            return raw_diff
        return json.dumps(_files_page([{"path": "a.py", "changeType": "MODIFIED"}]))

    with pytest.raises(RuntimeError, match="did not include 'a.py'"):
        await fetch_pr_files("owner", "repo", 123, runner=runner)


@pytest.mark.parametrize(
    ("path", "change_type", "patch"),
    [
        (
            "asset.bin",
            "MODIFIED",
            "diff --git a/asset.bin b/asset.bin\nBinary files a/asset.bin and b/asset.bin differ\n",
        ),
        (
            "empty.py",
            "ADDED",
            "diff --git a/empty.py b/empty.py\nnew file mode 100644\nindex 0000000..e69de29\n",
        ),
        (
            "run.sh",
            "MODIFIED",
            "diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\n",
        ),
        (
            "gone.py",
            "DELETED",
            "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n",
        ),
        (
            "한글.py",
            "ADDED",
            'diff --git "a/\\355\\225\\234\\352\\270\\200.py" "b/\\355\\225\\234\\352\\270\\200.py"\nnew file mode 100644\n--- /dev/null\n+++ "b/\\355\\225\\234\\352\\270\\200.py"\n@@ -0,0 +1 @@\n+added\n',
        ),
    ],
)
async def test_fetch_pr_files_keeps_canonical_file_kinds(
    path: str, change_type: str, patch: str
) -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args == CANONICAL_DIFF_ARGS:
            return patch
        return json.dumps(_files_page([{"path": path, "changeType": change_type}]))

    files = await fetch_pr_files("owner", "repo", 123, runner=runner)

    assert files[0].filename == path
    assert files[0].patch == patch.rstrip("\n")


async def test_preview_loads_source_on_demand_without_changing_comment_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore(owner="owner", repo="repo", pr_number=123)
    store.state.pr = PR(number=123, head_sha="head-sha")
    blob_requests: list[str] = []
    content = "first\nnew\nkeep\nextra context\nlast\n"

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args == CANONICAL_DIFF_ARGS:
            return MODIFIED_PATCH
        assert input_text is not None
        payload = json.loads(input_text)
        if "files(first:" in payload["query"]:
            return json.dumps(_files_page([{"path": "a.py", "changeType": "MODIFIED"}]))
        blob_requests.append(payload["variables"]["expression"])
        return json.dumps({"data": {"repository": {"object": {"text": content}}}})

    monkeypatch.setattr(store._service, "_run_gh", runner)
    await store.load_files()
    canonical = await store.get_file_diff_async("a.py")
    assert canonical is not None
    assert blob_requests == []

    full_text = await store.get_file_content("a.py")
    assert full_text == content
    assert await store.get_file_content("a.py") == content
    assert blob_requests == ["head-sha:a.py"]
    preview = build_full_file_diff("a.py", full_text, source_diff=canonical)
    assert preview.hunks[-1].lines[-1].new_line_no == 5
    assert store.is_inline_comment_diff_line(path="a.py", line=2, side="RIGHT")
    assert not store.is_inline_comment_diff_line(path="a.py", line=5, side="RIGHT")
    assert store.get_file_diff("a.py") is canonical


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
