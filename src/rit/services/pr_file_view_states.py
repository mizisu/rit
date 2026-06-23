from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from rit.services.graphql_mutations import (
    GraphQLMutationError,
    ensure_graphql_mutation_succeeded,
    graphql_error_summary,
)
from rit.services.gh_request import GitHubInputRunner, run_request


__all__ = (
    "FileViewMutationError",
    "FileViewMutationRequest",
    "FileViewStatesGraphQLError",
    "FileViewStatesPage",
    "collect_file_view_states",
    "ensure_file_view_mutation_result",
    "ensure_file_view_mutation_succeeded",
    "fetch_file_view_states",
    "file_view_mutation_request",
    "file_view_states_page_request",
    "mark_file_as_viewed",
    "parse_file_view_states_page",
    "parse_file_view_states_result",
    "set_file_viewed_state",
    "unmark_file_as_viewed",
)


FILE_VIEW_STATES_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      files(first: 100, after: $after) {
        nodes {
          path
          viewerViewedState
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""

MARK_FILE_VIEWED_MUTATION = """
mutation($pullRequestId: ID!, $path: String!) {
  markFileAsViewed(input: {pullRequestId: $pullRequestId, path: $path}) {
    clientMutationId
  }
}
"""

UNMARK_FILE_VIEWED_MUTATION = """
mutation($pullRequestId: ID!, $path: String!) {
  unmarkFileAsViewed(input: {pullRequestId: $pullRequestId, path: $path}) {
    clientMutationId
  }
}
"""


class FileViewStatesGraphQLError(ValueError):
    """Raised when GitHub returns GraphQL errors for file viewed states."""


class FileViewMutationError(ValueError):
    """Raised when a file viewed-state mutation fails."""


@dataclass(frozen=True)
class FileViewStatesPage:
    """Parsed file viewed-state page from GitHub GraphQL."""

    states: dict[str, str]
    has_next_page: bool
    next_cursor: str | None


@dataclass(frozen=True)
class FileViewMutationRequest:
    """GraphQL request metadata for a file viewed-state mutation."""

    args: tuple[str, ...]
    viewed: bool
    action: str


def file_view_states_page_request(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    cursor: str | None,
) -> tuple[str, ...]:
    """Build gh args for one file viewed-state GraphQL page."""
    args = [
        "api",
        "graphql",
        "-f",
        f"query={FILE_VIEW_STATES_QUERY}",
        "-F",
        f"owner={owner}",
        "-F",
        f"repo={repo}",
        "-F",
        f"number={pr_number}",
    ]
    if cursor:
        args.extend(["-f", f"after={cursor}"])
    return tuple(args)


def file_view_mutation_request(
    *,
    pull_request_id: str,
    path: str,
    viewed: bool,
) -> FileViewMutationRequest:
    """Build gh args for a file viewed-state mutation."""
    mutation = MARK_FILE_VIEWED_MUTATION if viewed else UNMARK_FILE_VIEWED_MUTATION
    return FileViewMutationRequest(
        args=(
            "api",
            "graphql",
            "-f",
            f"query={mutation}",
            "-F",
            f"pullRequestId={pull_request_id}",
            "-f",
            f"path={path}",
        ),
        viewed=viewed,
        action="mark file as viewed" if viewed else "unmark file as viewed",
    )


def _mapping_value(data: object, key: str) -> object | None:
    if not isinstance(data, Mapping):
        return None
    for item_key, item_value in data.items():
        if item_key == key:
            return item_value
    return None


def parse_file_view_states_page(
    data: object,
) -> FileViewStatesPage:
    """Parse one GraphQL file viewed-state page."""
    errors = _mapping_value(data, "errors")
    if errors is not None:
        raise FileViewStatesGraphQLError(str(errors))

    data_node = _mapping_value(data, "data")
    repository_data = _mapping_value(data_node, "repository")
    pr_data = _mapping_value(repository_data, "pullRequest")
    if pr_data is None:
        return FileViewStatesPage(states={}, has_next_page=False, next_cursor=None)

    files_data = _mapping_value(pr_data, "files")
    if files_data is None:
        return FileViewStatesPage(states={}, has_next_page=False, next_cursor=None)

    states: dict[str, str] = {}
    nodes = _mapping_value(files_data, "nodes")
    if not isinstance(nodes, list):
        nodes = []
    for node in nodes:
        path = _mapping_value(node, "path")
        viewed_state = _mapping_value(node, "viewerViewedState")
        if isinstance(path, str) and isinstance(viewed_state, str):
            states[path] = viewed_state

    page_info = _mapping_value(files_data, "pageInfo")
    if page_info is None:
        return FileViewStatesPage(states=states, has_next_page=False, next_cursor=None)

    next_cursor = _mapping_value(page_info, "endCursor")
    return FileViewStatesPage(
        states=states,
        has_next_page=_mapping_value(page_info, "hasNextPage") is True,
        next_cursor=next_cursor if isinstance(next_cursor, str) else None,
    )


def parse_file_view_states_result(result: str) -> FileViewStatesPage:
    """Parse a gh GraphQL result for one file viewed-state page."""
    return parse_file_view_states_page(json.loads(result))


async def collect_file_view_states(
    fetch_page: Callable[[str | None], Awaitable[FileViewStatesPage]],
) -> dict[str, str]:
    """Collect file viewed states from paginated GraphQL pages."""
    states: dict[str, str] = {}
    cursor: str | None = None

    while True:
        page = await fetch_page(cursor)
        states.update(page.states)
        if not page.has_next_page:
            return states
        cursor = page.next_cursor


async def fetch_file_view_states(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> dict[str, str]:
    """Fetch all file viewed states through paginated GitHub GraphQL requests."""

    async def fetch_page(cursor: str | None) -> FileViewStatesPage:
        return parse_file_view_states_result(
            await run_request(
                file_view_states_page_request(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    cursor=cursor,
                ),
                runner,
            )
        )

    return await collect_file_view_states(fetch_page)


def ensure_file_view_mutation_result(result: str) -> None:
    """Raise when a file viewed-state mutation result contains GraphQL errors."""
    ensure_graphql_mutation_succeeded(json.loads(result))


def ensure_file_view_mutation_succeeded(
    result: str,
    request: FileViewMutationRequest,
) -> None:
    """Raise with action context when a file viewed-state mutation fails."""
    data = json.loads(result)
    try:
        ensure_graphql_mutation_succeeded(data)
    except GraphQLMutationError as error:
        message = graphql_error_summary(data) or str(error)
        raise FileViewMutationError(
            f"Failed to {request.action}: {message}"
        ) from error


async def set_file_viewed_state(
    *,
    pull_request_id: str,
    path: str,
    viewed: bool,
    runner: GitHubInputRunner,
) -> None:
    """Set one file's viewed state through a GitHub GraphQL mutation."""
    request = file_view_mutation_request(
        pull_request_id=pull_request_id,
        path=path,
        viewed=viewed,
    )
    ensure_file_view_mutation_succeeded(
        await run_request(request.args, runner),
        request,
    )


async def mark_file_as_viewed(
    *,
    pull_request_id: str,
    path: str,
    runner: GitHubInputRunner,
) -> None:
    """Mark one PR file as viewed through a GitHub GraphQL mutation."""
    await set_file_viewed_state(
        pull_request_id=pull_request_id,
        path=path,
        viewed=True,
        runner=runner,
    )


async def unmark_file_as_viewed(
    *,
    pull_request_id: str,
    path: str,
    runner: GitHubInputRunner,
) -> None:
    """Unmark one PR file as viewed through a GitHub GraphQL mutation."""
    await set_file_viewed_state(
        pull_request_id=pull_request_id,
        path=path,
        viewed=False,
        runner=runner,
    )
