from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from rit.services.gh_request import (
    GitHubInputRequest,
    GitHubInputRunner,
    run_input_request,
)

__all__ = (
    "GraphQLRequestError",
    "connection_nodes",
    "graphql_request",
    "mapping",
    "run_graphql",
)


class GraphQLRequestError(ValueError):
    """Raised when GitHub returns an invalid or failed GraphQL response."""


def graphql_request(
    query: str,
    variables: Mapping[str, object],
) -> GitHubInputRequest:
    """Build a gh GraphQL request with JSON variables on stdin."""
    return GitHubInputRequest(
        args=("api", "graphql", "--input", "-"),
        input_text=json.dumps({"query": query, "variables": variables}),
    )


async def run_graphql(
    query: str,
    variables: Mapping[str, object],
    *,
    runner: GitHubInputRunner,
) -> Mapping[str, object]:
    """Run a GraphQL document and return its validated response object."""
    result = await run_input_request(graphql_request(query, variables), runner)
    data = json.loads(result)
    if not isinstance(data, Mapping):
        raise GraphQLRequestError("GitHub GraphQL response was not an object")

    errors = data.get("errors")
    if errors:
        messages: list[str] = []
        if isinstance(errors, list):
            for error in errors:
                if not isinstance(error, Mapping):
                    continue
                message = error.get("message")
                if isinstance(message, str) and message:
                    messages.append(message)
        raise GraphQLRequestError("; ".join(messages) if messages else str(errors))
    return data


def mapping(value: object) -> Mapping[str, object]:
    """Return a typed mapping view or an empty mapping."""
    return cast("Mapping[str, object]", value) if isinstance(value, Mapping) else {}


def connection_nodes(value: object) -> list[object]:
    """Return the nodes from a GraphQL connection."""
    nodes = mapping(value).get("nodes")
    return cast("list[object]", nodes) if isinstance(nodes, list) else []
