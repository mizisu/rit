from __future__ import annotations

import json

from rit.services.gh_request import GitHubInputRunner, run_request
from rit.services.pr_graphql_queries import (
    PullRequestGraphQLView,
    pull_request_graphql_request,
)
from rit.state.models import PR


__all__ = (
    "PullRequestGraphQLError",
    "PullRequestNotFound",
    "fetch_pull_request_all",
    "fetch_pull_request_graphql_node_id",
    "fetch_pull_request_graphql_pr",
    "fetch_pull_request_graphql_result",
    "fetch_pull_request_summary",
    "parse_pull_request_graphql_data",
    "parse_pull_request_graphql_node_id_result",
    "parse_pull_request_graphql_pr",
    "parse_pull_request_graphql_pr_result",
    "parse_pull_request_graphql_result",
)


class PullRequestGraphQLError(ValueError):
    """Raised when GitHub returns GraphQL errors for PR data."""


class PullRequestNotFound(ValueError):
    """Raised when a GraphQL response does not include the requested PR."""


def _mapping_value(data: object, key: str) -> object | None:
    if not isinstance(data, dict):
        return None
    for item_key, item_value in data.items():
        if item_key == key:
            return item_value
    return None


def _required_mapping(value: object, *, pr_number: int) -> dict[object, object]:
    if isinstance(value, dict):
        return {key: item_value for key, item_value in value.items()}
    raise PullRequestNotFound(f"PR #{pr_number} not found")


def parse_pull_request_graphql_result(
    result: str,
    *,
    pr_number: int,
) -> dict[str, object]:
    """Parse a gh GraphQL JSON string into a pullRequest object."""
    return parse_pull_request_graphql_data(json.loads(result), pr_number=pr_number)


def parse_pull_request_graphql_data(
    data: object,
    *,
    pr_number: int,
) -> dict[str, object]:
    """Extract the pullRequest node from a GitHub GraphQL response."""
    response = _required_mapping(data, pr_number=pr_number)
    errors = _mapping_value(response, "errors")
    if errors:
        raise PullRequestGraphQLError(str(errors))

    graphql_data = _required_mapping(
        _mapping_value(response, "data"),
        pr_number=pr_number,
    )
    repository_data = _required_mapping(
        _mapping_value(graphql_data, "repository"),
        pr_number=pr_number,
    )
    pr_data = _required_mapping(
        _mapping_value(repository_data, "pullRequest"),
        pr_number=pr_number,
    )

    return {
        key: value
        for key, value in pr_data.items()
        if isinstance(key, str)
    }


def parse_pull_request_graphql_pr(data: object, *, pr_number: int) -> PR:
    """Parse a GitHub GraphQL PR response into a PR model."""
    return PR.model_validate(
        parse_pull_request_graphql_data(data, pr_number=pr_number)
    )


def parse_pull_request_graphql_pr_result(result: str, *, pr_number: int) -> PR:
    """Parse a gh GraphQL JSON string into a PR model."""
    return parse_pull_request_graphql_pr(json.loads(result), pr_number=pr_number)


def parse_pull_request_graphql_node_id_result(
    result: str,
    *,
    pr_number: int,
) -> str:
    """Parse a gh GraphQL JSON string into a pull request node ID."""
    pr_data = parse_pull_request_graphql_result(result, pr_number=pr_number)
    node_id = pr_data.get("id")
    if isinstance(node_id, str) and node_id:
        return node_id
    raise PullRequestNotFound(f"PR #{pr_number} node ID not found")


async def fetch_pull_request_graphql_result(
    *,
    view: PullRequestGraphQLView,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> str:
    """Fetch one pull request GraphQL view via gh."""
    return await run_request(
        pull_request_graphql_request(
            view=view,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
        ),
        runner,
    )


async def fetch_pull_request_graphql_pr(
    *,
    view: PullRequestGraphQLView,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> PR:
    """Fetch and parse one pull request GraphQL view."""
    return parse_pull_request_graphql_pr_result(
        await fetch_pull_request_graphql_result(
            view=view,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            runner=runner,
        ),
        pr_number=pr_number,
    )


async def fetch_pull_request_all(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> PR:
    """Fetch all PR data used by the store in one GraphQL request."""
    return await fetch_pull_request_graphql_pr(
        view=PullRequestGraphQLView.ALL,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        runner=runner,
    )


async def fetch_pull_request_summary(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> PR:
    """Fetch the PR summary needed for the header and sidebar."""
    return await fetch_pull_request_graphql_pr(
        view=PullRequestGraphQLView.SUMMARY,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        runner=runner,
    )


async def fetch_pull_request_graphql_node_id(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> str:
    """Fetch and parse a pull request GraphQL node ID."""
    return parse_pull_request_graphql_node_id_result(
        await fetch_pull_request_graphql_result(
            view=PullRequestGraphQLView.NODE_ID,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            runner=runner,
        ),
        pr_number=pr_number,
    )
