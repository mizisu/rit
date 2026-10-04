from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass

from unidiff.errors import UnidiffParseError
from unidiff.patch import PatchSet

from rit.core.diff import parse_multi_file_patch_summaries
from rit.services.gh_paginated_json import parse_paginated_items
from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import connection_nodes, mapping, run_graphql
from rit.state.models import FileViewedState, PRFile
from rit.state.pr_overview import PRFileMetadata

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
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      files(first: $first, after: $after) {
        nodes {
          path
          additions
          deletions
          changeType
          viewerViewedState
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
      baseRefOid
      headRefOid
    }
  }
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
    files = [_parse_pr_file(node) for node in connection_nodes(connection)]
    page_info = mapping(connection.get("pageInfo"))
    end_cursor = page_info.get("endCursor")
    base_ref_oid = pull_request.get("baseRefOid")
    head_ref_oid = pull_request.get("headRefOid")
    return _PRFilesPage(
        files=files,
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
) -> list[PRFile]:
    """Load metadata and GitHub's canonical patch concurrently, without blobs."""
    metadata_task = asyncio.ensure_future(
        metadata
        if metadata is not None
        else fetch_pr_file_metadata(
            owner,
            repo,
            pr_number,
            total_count=total_count,
            per_page=per_page,
            runner=runner,
        )
    )
    patch = asyncio.ensure_future(
        runner(
            [
                "api",
                f"repos/{owner}/{repo}/pulls/{pr_number}",
                "-H",
                "Accept: application/vnd.github.v3.diff",
            ]
        )
    )
    try:
        try:
            snapshot, raw_diff = await asyncio.gather(metadata_task, patch)
        except RuntimeError as error:
            if (
                not patch.done()
                or patch.cancelled()
                or patch.exception() is not error
                or "HTTP 406" not in str(error)
            ):
                raise
            snapshot = await metadata_task
            files = [file.model_copy() for file in snapshot.files]
            result = await runner(
                [
                    "api",
                    f"repos/{owner}/{repo}/pulls/{pr_number}/files?per_page=100",
                    "--paginate",
                ]
            )
            _, current_base, current_head = await _fetch_pr_file_metadata(
                owner, repo, pr_number, total_count=1, per_page=1, runner=runner
            )
            if (snapshot.base_sha, snapshot.head_sha) != (current_base, current_head):
                raise RuntimeError(
                    "PR changed while loading files; refresh and try again"
                )
            await asyncio.to_thread(_populate_file_list_patches, files, result)
            return files
    finally:
        for task in (metadata_task, patch):
            if not task.done():
                task.cancel()
        await asyncio.gather(metadata_task, patch, return_exceptions=True)

    files = [file.model_copy() for file in snapshot.files]
    await asyncio.to_thread(_populate_file_patches, files, raw_diff)
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
    while True:
        page = parse_pr_files_page(
            await run_graphql(
                _PR_FILES_QUERY,
                {
                    "owner": owner,
                    "repo": repo,
                    "number": pr_number,
                    "first": min(max(per_page, 1), 100),
                    "after": after,
                },
                runner=runner,
            )
        )
        if page.files and (not page.base_ref_oid or not page.head_ref_oid):
            raise ValueError(
                "GitHub GraphQL response did not include PR base/head refs"
            )
        if head_ref_oid and (
            page.base_ref_oid != base_ref_oid or page.head_ref_oid != head_ref_oid
        ):
            raise RuntimeError("PR changed while loading files; refresh and try again")
        base_ref_oid = page.base_ref_oid
        head_ref_oid = page.head_ref_oid
        files.extend(page.files)
        if total_count is not None and len(files) >= total_count:
            files = files[:total_count]
            break
        if not page.has_next_page:
            break
        if not page.end_cursor or page.end_cursor in seen_cursors:
            raise ValueError("GitHub GraphQL file pagination returned no next cursor")
        seen_cursors.add(page.end_cursor)
        after = page.end_cursor

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


def _populate_file_list_patches(files: list[PRFile], result: str) -> None:
    patches: dict[str, PRFile] = {}
    for item in parse_paginated_items(result):
        data = dict(mapping(item))
        if data.get("patch") is None:
            data["patch"] = ""
        file = PRFile.model_validate(data)
        if not file.filename or file.filename in patches:
            raise RuntimeError("GitHub returned an invalid changed-file list")
        file.status = _STATUS_BY_CHANGE_TYPE.get(file.status.upper(), file.status)
        patches[file.filename] = file

    if len(patches) != len(files) or patches.keys() != {
        file.filename for file in files
    }:
        raise RuntimeError("GitHub returned an incomplete changed-file list")
    for file in files:
        patch = patches[file.filename]
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
        _validate_file_list_patch(patch)
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
