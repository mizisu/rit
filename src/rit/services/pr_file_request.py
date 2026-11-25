from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable
from dataclasses import dataclass

from unidiff.errors import UnidiffParseError
from unidiff.patch import PatchSet

from rit.core.diff import parse_multi_file_patch_summaries
from rit.services.gh_paginated_json import parse_paginated_items
from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import connection_nodes, mapping, run_graphql
from rit.state.models import FileViewedState, PRFile
from rit.state.pr_overview import FilePatchPreview, PRFileMetadata

__all__ = (
    "fetch_file_content",
    "fetch_pr_files",
    "parse_pr_files_page",
)


_PR_FILES_QUERY = """
query(
  $owner: String!
  $repo: String!
  $number: Int!
  $first: Int!
  $after: String
  $withTail: Boolean!
  $last: Int!
  $before: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      files(first: $first, after: $after) {
        totalCount
        nodes { ...FileMetadata }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
      tail: files(last: $last, before: $before) @include(if: $withTail) {
        nodes { ...FileMetadata }
        pageInfo { startCursor }
      }
      baseRefOid
      headRefOid
    }
  }
}
fragment FileMetadata on PullRequestChangedFile {
  path
  additions
  deletions
  changeType
  viewerViewedState
}
"""

_FILE_CONTENT_QUERY = """
query($owner: String!, $repo: String!, $expression: String!) {
  repository(owner: $owner, name: $repo) {
    object(expression: $expression) {
      ... on Blob {
        text
        isBinary
        isTruncated
      }
    }
  }
}
"""

_STATUS_BY_CHANGE_TYPE = {
    "ADDED": "added",
    "COPIED": "copied",
    "DELETED": "removed",
    "MODIFIED": "modified",
    "RENAMED": "renamed",
    "CHANGED": "modified",
}


@dataclass(frozen=True)
class _PRFilesPage:
    files: list[PRFile]
    total_count: int | None
    tail: list[PRFile]
    start_cursor: str | None
    has_next_page: bool
    end_cursor: str | None
    base_ref_oid: str
    head_ref_oid: str


def parse_pr_files_page(data: object) -> _PRFilesPage:
    """Parse one GraphQL changed-file connection page."""
    response = mapping(data)
    repository = mapping(mapping(response.get("data")).get("repository"))
    pull_request = mapping(repository.get("pullRequest"))
    connection = mapping(pull_request.get("files"))
    tail = mapping(pull_request.get("tail"))
    start_cursor = mapping(tail.get("pageInfo")).get("startCursor")
    count = connection.get("totalCount")
    page_info = mapping(connection.get("pageInfo"))
    end_cursor = page_info.get("endCursor")
    base_ref_oid = pull_request.get("baseRefOid")
    head_ref_oid = pull_request.get("headRefOid")
    return _PRFilesPage(
        files=[_parse_pr_file(node) for node in connection_nodes(connection)],
        total_count=count if isinstance(count, int) else None,
        tail=[_parse_pr_file(node) for node in connection_nodes(tail)],
        start_cursor=start_cursor if isinstance(start_cursor, str) else None,
        has_next_page=page_info.get("hasNextPage") is True,
        end_cursor=end_cursor if isinstance(end_cursor, str) else None,
        base_ref_oid=base_ref_oid if isinstance(base_ref_oid, str) else "",
        head_ref_oid=head_ref_oid if isinstance(head_ref_oid, str) else "",
    )


async def fetch_pr_files(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    total_count: int | None = None,
    per_page: int = 100,
    runner: GitHubInputRunner,
    metadata: Awaitable[PRFileMetadata] | None = None,
    preview: FilePatchPreview | None = None,
) -> list[PRFile]:
    """Load metadata and GitHub's canonical patch concurrently, without blobs."""
    if metadata is None:
        metadata = fetch_pr_file_metadata(
            owner,
            repo,
            pr_number,
            total_count=total_count,
            per_page=per_page,
            runner=runner,
        )

    async def raw_patch() -> str | None:
        try:
            return await runner(
                [
                    "api",
                    f"repos/{owner}/{repo}/pulls/{pr_number}",
                    "-H",
                    "Accept: application/vnd.github.v3.diff",
                ]
            )
        except RuntimeError as error:
            if "HTTP 406" not in str(error):
                raise
            return None

    # GitHub rejects the raw diff above 300 files, regardless of byte size.
    if total_count is not None and total_count > 300:
        return await _fetch_file_list_patches(
            owner, repo, pr_number, total_count, metadata, runner, preview
        )
    metadata_task = asyncio.ensure_future(metadata)
    patch = asyncio.create_task(raw_patch())
    try:
        snapshot, raw_diff = await asyncio.gather(metadata_task, patch)
    finally:
        for task in (metadata_task, patch):
            if not task.done():
                task.cancel()
        await asyncio.gather(metadata_task, patch, return_exceptions=True)

    if raw_diff is None:
        return await _fetch_file_list_patches(
            owner, repo, pr_number, len(snapshot.files), metadata_task, runner
        )
    files = [file.model_copy() for file in snapshot.files]
    await asyncio.to_thread(_populate_file_patches, files, raw_diff)
    return files


async def _fetch_file_list_patches(
    owner: str,
    repo: str,
    pr_number: int,
    count: int,
    metadata: Awaitable[PRFileMetadata],
    runner: GitHubInputRunner,
    preview: FilePatchPreview | None = None,
) -> list[PRFile]:
    slots = asyncio.Semaphore(4)

    async def fetch_page(page: int) -> str:
        async with slots:
            return await runner(
                [
                    "api",
                    f"repos/{owner}/{repo}/pulls/{pr_number}/files?per_page=100&page={page}",
                ]
            )

    metadata_task = asyncio.ensure_future(metadata)
    pages: dict[int, asyncio.Task[str]] = {}
    try:
        if count > 3000:
            raise RuntimeError("GitHub's changed-file list is limited to 3000 files")
        numbers = range(1, (count + 99) // 100 + 1)
        ordered = sorted(
            numbers, key=lambda number: preview is None or number != preview.page
        )
        pages = {n: asyncio.create_task(fetch_page(n)) for n in ordered}
        if preview is not None and preview.page in pages:
            raw_page = await pages[preview.page]
            first, base, head = await _fetch_pr_file_metadata(
                owner, repo, pr_number, total_count=1, per_page=1, runner=runner
            )
            if (base, head) != preview.refs:
                raise RuntimeError(
                    "PR changed while loading files; refresh and try again"
                )
            target = preview.target or (first[0] if first else None)
            if target is not None:
                file = await asyncio.to_thread(
                    _file_preview_from_page, target, raw_page
                )
                if file is not None:
                    preview.publish(file)
        results = await asyncio.gather(*pages.values())
        snapshot = await metadata_task
    finally:
        metadata_task.cancel()
        for task in pages.values():
            task.cancel()
        await asyncio.gather(metadata_task, *pages.values(), return_exceptions=True)

    _, base, head = await _fetch_pr_file_metadata(
        owner, repo, pr_number, total_count=1, per_page=1, runner=runner
    )
    if (snapshot.base_sha, snapshot.head_sha) != (base, head):
        raise RuntimeError("PR changed while loading files; refresh and try again")
    files = [file.model_copy() for file in snapshot.files]
    await asyncio.to_thread(_populate_file_list_patches, files, "\n".join(results))
    return files


async def fetch_pr_file_metadata(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    total_count: int | None = None,
    per_page: int = 100,
    runner: GitHubInputRunner,
) -> PRFileMetadata:
    """Fetch a complete metadata snapshot without downloading patches."""
    files, base, head = await _fetch_pr_file_metadata(
        owner,
        repo,
        pr_number,
        total_count=total_count,
        per_page=per_page,
        runner=runner,
    )
    if not base or not head:
        raise ValueError("GitHub GraphQL response did not include PR base/head refs")
    return PRFileMetadata(base, head, tuple(files))


async def _fetch_pr_file_metadata(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    total_count: int | None,
    per_page: int,
    runner: GitHubInputRunner,
) -> tuple[list[PRFile], str, str]:
    files: list[PRFile] = []
    after: str | None = None
    base_ref_oid = ""
    head_ref_oid = ""
    seen_cursors: set[str] = set()
    suffix: deque[PRFile] = deque()
    before: str | None = None
    page_size = min(max(per_page, 1), 100)
    connection_size: int | None = None
    while True:
        remaining = (
            None
            if connection_size is None
            else connection_size - len(files) - len(suffix)
        )
        # Once GitHub reports the size, read disjoint pages from both ends in one trip.
        first = min(page_size, remaining) if remaining is not None else page_size
        last = min(page_size, remaining - first) if remaining is not None else 0
        page = parse_pr_files_page(
            await run_graphql(
                _PR_FILES_QUERY,
                {
                    "owner": owner,
                    "repo": repo,
                    "number": pr_number,
                    "first": first,
                    "after": after,
                    "withTail": bool(last),
                    "last": last,
                    "before": before,
                },
                runner=runner,
            )
        )
        if page.files and (not page.base_ref_oid or not page.head_ref_oid):
            raise ValueError(
                "GitHub GraphQL response did not include PR base/head refs"
            )
        if head_ref_oid and (page.base_ref_oid, page.head_ref_oid) != (
            base_ref_oid,
            head_ref_oid,
        ):
            raise RuntimeError("PR changed while loading files; refresh and try again")
        base_ref_oid, head_ref_oid = page.base_ref_oid, page.head_ref_oid
        files.extend(page.files)
        if total_count is not None and len(files) >= total_count:
            files = files[:total_count]
            break
        if last:
            if (
                len(page.tail) != last
                or not page.start_cursor
                or before == page.start_cursor
            ):
                raise ValueError(
                    "GitHub GraphQL file pagination returned an invalid tail page"
                )
            suffix.extendleft(reversed(page.tail))
            before = page.start_cursor
        if total_count is None and page.total_count is not None:
            if connection_size is not None and connection_size != page.total_count:
                raise RuntimeError(
                    "PR changed while loading files; refresh and try again"
                )
            connection_size = page.total_count
            remaining = connection_size - len(files) - len(suffix)
            if remaining == 0:
                break
            if remaining < 0 or not page.has_next_page:
                raise ValueError(
                    "GitHub GraphQL returned an incomplete changed-file list"
                )
        if not page.has_next_page:
            break
        if not page.end_cursor or page.end_cursor in seen_cursors:
            raise ValueError("GitHub GraphQL file pagination returned no next cursor")
        seen_cursors.add(page.end_cursor)
        after = page.end_cursor

    if suffix:
        files.extend(suffix)
        if len({file.filename for file in files}) != len(files):
            raise ValueError("GitHub GraphQL returned overlapping changed-file pages")
    if files and (not base_ref_oid or not head_ref_oid):
        raise ValueError("GitHub GraphQL response did not include PR base/head refs")
    return files, base_ref_oid, head_ref_oid


async def fetch_file_content(
    owner: str,
    repo: str,
    path: str,
    *,
    ref: str,
    runner: GitHubInputRunner,
) -> str:
    """Fetch UTF-8 file content at a Git ref through GraphQL."""
    data = await run_graphql(
        _FILE_CONTENT_QUERY,
        {"owner": owner, "repo": repo, "expression": f"{ref}:{path}"},
        runner=runner,
    )
    repository = mapping(mapping(data.get("data")).get("repository"))
    blob = mapping(repository.get("object"))
    if not blob:
        raise ValueError(f"File {path!r} was not found at {ref}")
    if blob.get("isBinary") is True:
        raise ValueError(f"File {path!r} is binary")
    if blob.get("isTruncated") is True:
        raise ValueError(f"File {path!r} is too large for the GraphQL text field")
    text = blob.get("text")
    if not isinstance(text, str):
        raise ValueError(f"File {path!r} did not return text content")
    return text


def _parse_pr_file(value: object) -> PRFile:
    data = mapping(value)
    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("GraphQL changed file did not include a path")
    additions = _integer(data.get("additions"))
    deletions = _integer(data.get("deletions"))
    change_type = data.get("changeType")
    status = (
        _STATUS_BY_CHANGE_TYPE.get(change_type, "modified")
        if isinstance(change_type, str)
        else "modified"
    )
    raw_viewed_state = data.get("viewerViewedState")
    try:
        viewed_state = FileViewedState(raw_viewed_state)
    except TypeError, ValueError:
        viewed_state = FileViewedState.UNVIEWED
    return PRFile(
        filename=path,
        status=status,
        additions=additions,
        deletions=deletions,
        changes=additions + deletions,
        viewer_viewed_state=viewed_state,
    )


def _integer(value: object) -> int:
    return value if isinstance(value, int) else 0


def _populate_file_patches(files: list[PRFile], raw_diff: str) -> None:
    summaries = parse_multi_file_patch_summaries(raw_diff)
    patches_by_filename = {summary.filename: summary for summary in summaries}
    for file in files:
        summary = patches_by_filename.get(file.filename)
        if file.status in {"renamed", "copied"} and (
            summary is None or not summary.old_filename
        ):
            raise RuntimeError(
                f"GitHub diff did not include the source path for {file.filename!r}"
            )
        if summary is None:
            raise RuntimeError(f"GitHub diff did not include {file.filename!r}")
        file.previous_filename = summary.old_filename
        file.patch = summary.patch


def _file_preview_from_page(target: PRFile, result: str) -> PRFile | None:
    patch = _parse_file_list(result).get(target.filename)
    if patch is None:
        return None
    file = target.model_copy()
    _apply_file_list_patch(file, patch)
    return file


def _parse_file_list(result: str) -> dict[str, PRFile]:
    patches: dict[str, PRFile] = {}
    for item in parse_paginated_items(result):
        data = dict(mapping(item))
        omitted = data.get("patch") is None
        if omitted:
            data["patch"] = ""
        file = PRFile.model_validate(data)
        file.patch_unavailable = omitted and bool(file.additions or file.deletions)
        if not file.filename or file.filename in patches:
            raise RuntimeError("GitHub returned an invalid changed-file list")
        file.status = _STATUS_BY_CHANGE_TYPE.get(file.status.upper(), file.status)
        patches[file.filename] = file
    return patches


def _populate_file_list_patches(files: list[PRFile], result: str) -> None:
    patches = _parse_file_list(result)
    if len(patches) != len(files) or patches.keys() != {
        file.filename for file in files
    }:
        raise RuntimeError("GitHub returned an incomplete changed-file list")
    for file in files:
        _apply_file_list_patch(file, patches[file.filename])


def _apply_file_list_patch(file: PRFile, patch: PRFile) -> None:
    if (file.status, file.additions, file.deletions) != (
        patch.status,
        patch.additions,
        patch.deletions,
    ):
        raise RuntimeError("PR changed while loading files; refresh and try again")
    if file.status in {"renamed", "copied"} and not patch.previous_filename:
        raise RuntimeError(
            f"GitHub diff did not include the source path for {file.filename!r}"
        )
    if not patch.patch_unavailable:
        _validate_file_list_patch(patch)
    file.patch_unavailable = patch.patch_unavailable
    file.previous_filename = patch.previous_filename
    file.patch = patch.patch


def _validate_file_list_patch(file: PRFile) -> None:
    message = f"GitHub returned an incomplete or invalid patch for {file.filename!r}"
    try:
        patch = PatchSet("--- a/patch\n+++ b/patch\n" + file.patch, metadata_only=True)
    except UnidiffParseError as error:
        raise RuntimeError(message) from error
    if len(patch) != 1 or (patch.added, patch.removed) != (
        file.additions,
        file.deletions,
    ):
        raise RuntimeError(message)
