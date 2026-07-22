from __future__ import annotations

import asyncio
import difflib
from dataclasses import dataclass

from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import connection_nodes, mapping, run_graphql
from rit.state.models import FileViewedState, PRFile

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
) -> list[PRFile]:
    """Fetch all changed-file metadata through GraphQL cursor pagination."""
    files: list[PRFile] = []
    after: str | None = None
    base_ref_oid = ""
    head_ref_oid = ""
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
        base_ref_oid = page.base_ref_oid or base_ref_oid
        head_ref_oid = page.head_ref_oid or head_ref_oid
        files.extend(page.files)
        if total_count is not None and len(files) >= total_count:
            files = files[:total_count]
            break
        if not page.has_next_page:
            break
        if not page.end_cursor or page.end_cursor == after:
            raise ValueError("GitHub GraphQL file pagination returned no next cursor")
        after = page.end_cursor

    if files and (not base_ref_oid or not head_ref_oid):
        raise ValueError("GitHub GraphQL response did not include PR base/head refs")
    await _populate_file_patches(
        owner,
        repo,
        files,
        base_ref_oid=base_ref_oid,
        head_ref_oid=head_ref_oid,
        runner=runner,
    )
    return files


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
    except (TypeError, ValueError):
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


async def _populate_file_patches(
    owner: str,
    repo: str,
    files: list[PRFile],
    *,
    base_ref_oid: str,
    head_ref_oid: str,
    runner: GitHubInputRunner,
) -> None:
    semaphore = asyncio.Semaphore(4)

    async def populate_batch(batch: list[PRFile]) -> None:
        async with semaphore:
            await _populate_file_patch_batch(
                owner,
                repo,
                batch,
                base_ref_oid=base_ref_oid,
                head_ref_oid=head_ref_oid,
                runner=runner,
            )

    await asyncio.gather(
        *(populate_batch(files[index : index + 40]) for index in range(0, len(files), 40))
    )


async def _populate_file_patch_batch(
    owner: str,
    repo: str,
    files: list[PRFile],
    *,
    base_ref_oid: str,
    head_ref_oid: str,
    runner: GitHubInputRunner,
) -> None:
    fields: list[str] = []
    variables: dict[str, object] = {"owner": owner, "repo": repo}
    variable_definitions = ["$owner: String!", "$repo: String!"]
    for index, file in enumerate(files):
        if file.status != "added":
            name = f"base{index}"
            variable = f"{name}Expression"
            variable_definitions.append(f"${variable}: String!")
            variables[variable] = f"{base_ref_oid}:{file.filename}"
            fields.append(f"{name}: object(expression: ${variable}) {{ ...BlobText }}")
        if file.status != "removed":
            name = f"head{index}"
            variable = f"{name}Expression"
            variable_definitions.append(f"${variable}: String!")
            variables[variable] = f"{head_ref_oid}:{file.filename}"
            fields.append(f"{name}: object(expression: ${variable}) {{ ...BlobText }}")

    query = f"""
query({', '.join(variable_definitions)}) {{
  repository(owner: $owner, name: $repo) {{
    {' '.join(fields)}
  }}
}}
fragment BlobText on Blob {{
  text
  isBinary
  isTruncated
}}
"""
    data = await run_graphql(query, variables, runner=runner)
    repository = mapping(mapping(data.get("data")).get("repository"))
    for index, file in enumerate(files):
        old_blob = mapping(repository.get(f"base{index}"))
        new_blob = mapping(repository.get(f"head{index}"))
        file.patch = _build_file_patch(file, old_blob=old_blob, new_blob=new_blob)


def _build_file_patch(
    file: PRFile,
    *,
    old_blob: object,
    new_blob: object,
) -> str:
    old = mapping(old_blob)
    new = mapping(new_blob)
    old_path = file.previous_filename or file.filename
    header = f"diff --git a/{old_path} b/{file.filename}\n"
    if file.status == "added":
        header += "new file mode 100644\n"
    elif file.status == "removed":
        header += "deleted file mode 100644\n"

    if old.get("isBinary") is True or new.get("isBinary") is True:
        return (
            header
            + f"Binary files a/{old_path} and b/{file.filename} differ"
        )
    if old.get("isTruncated") is True or new.get("isTruncated") is True:
        return header

    old_text = old.get("text")
    new_text = new.get("text")
    old_lines = old_text.splitlines() if isinstance(old_text, str) else []
    new_lines = new_text.splitlines() if isinstance(new_text, str) else []
    from_file = "/dev/null" if file.status == "added" else f"a/{old_path}"
    to_file = "/dev/null" if file.status == "removed" else f"b/{file.filename}"
    unified = "\n".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=from_file,
            tofile=to_file,
            lineterm="",
        )
    )
    return header + unified
