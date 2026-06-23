from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from rit.services.gh_request import GitHubInputRunner, run_request


__all__ = (
    "GraphQLMutationError",
    "ThreadResolutionMutationError",
    "ThreadResolutionMutationRequest",
    "ensure_graphql_mutation_succeeded",
    "graphql_error_summary",
    "parse_thread_resolution_mutation_result",
    "parse_thread_resolution_mutation_succeeded",
    "parse_thread_resolution_result",
    "resolve_thread",
    "run_thread_resolution_mutation",
    "set_thread_resolution_state",
    "thread_resolution_mutation_request",
    "unresolve_thread",
)


class GraphQLMutationError(ValueError):
    """Raised when GitHub returns GraphQL mutation errors."""


class ThreadResolutionMutationError(ValueError):
    """Raised when a thread resolution mutation fails."""


@dataclass(frozen=True)
class ThreadResolutionMutationRequest:
    """GraphQL request metadata for resolving or unresolving a review thread."""

    args: tuple[str, ...]
    mutation_name: str
    expected_resolved: bool
    action: str


def thread_resolution_mutation_request(
    thread_id: str,
    *,
    resolve: bool,
) -> ThreadResolutionMutationRequest:
    """Build gh args and parse metadata for a review-thread resolution mutation."""
    mutation_name = "resolveReviewThread" if resolve else "unresolveReviewThread"
    mutation = f"""
mutation($threadId: ID!) {{
  {mutation_name}(input: {{threadId: $threadId}}) {{
    thread {{
      isResolved
    }}
  }}
}}
"""
    return ThreadResolutionMutationRequest(
        args=(
            "api",
            "graphql",
            "-f",
            f"query={mutation}",
            "-F",
            f"threadId={thread_id}",
        ),
        mutation_name=mutation_name,
        expected_resolved=resolve,
        action="resolve" if resolve else "unresolve",
    )


def ensure_graphql_mutation_succeeded(data: object) -> None:
    """Raise when a GraphQL mutation response contains errors."""
    errors = _graphql_errors(data)
    if errors:
        raise GraphQLMutationError(str(errors))


def parse_thread_resolution_result(
    data: object,
    *,
    mutation_name: str,
    expected_resolved: bool,
) -> bool:
    """Return whether a review-thread resolution mutation reached the target state."""
    ensure_graphql_mutation_succeeded(data)
    response = _mapping(data)
    thread = _mapping(
        _mapping(_mapping(response.get("data")).get(mutation_name)).get("thread")
    )
    return thread.get("isResolved", not expected_resolved) == expected_resolved


def parse_thread_resolution_mutation_result(
    result: str,
    request: ThreadResolutionMutationRequest,
) -> bool:
    """Parse a gh GraphQL result for a review-thread resolution mutation."""
    return parse_thread_resolution_result(
        json.loads(result),
        mutation_name=request.mutation_name,
        expected_resolved=request.expected_resolved,
    )


def parse_thread_resolution_mutation_succeeded(
    result: str,
    request: ThreadResolutionMutationRequest,
) -> bool:
    """Parse a thread resolution mutation result with action context on failure."""
    data = json.loads(result)
    try:
        return parse_thread_resolution_result(
            data,
            mutation_name=request.mutation_name,
            expected_resolved=request.expected_resolved,
        )
    except GraphQLMutationError as error:
        message = graphql_error_summary(data) or str(error)
        raise ThreadResolutionMutationError(
            f"Failed to {request.action} thread: {message}"
        ) from error


async def run_thread_resolution_mutation(
    request: ThreadResolutionMutationRequest,
    runner: GitHubInputRunner,
) -> bool:
    """Run and parse a review-thread resolution mutation."""
    result = await run_request(request.args, runner)
    return parse_thread_resolution_mutation_succeeded(result, request)


async def set_thread_resolution_state(
    thread_id: str,
    *,
    resolve: bool,
    runner: GitHubInputRunner,
) -> bool:
    """Set a review thread's resolved state through a GitHub GraphQL mutation."""
    return await run_thread_resolution_mutation(
        thread_resolution_mutation_request(thread_id, resolve=resolve),
        runner,
    )


async def resolve_thread(thread_id: str, *, runner: GitHubInputRunner) -> bool:
    """Resolve a review thread through a GitHub GraphQL mutation."""
    return await set_thread_resolution_state(
        thread_id,
        resolve=True,
        runner=runner,
    )


async def unresolve_thread(thread_id: str, *, runner: GitHubInputRunner) -> bool:
    """Unresolve a review thread through a GitHub GraphQL mutation."""
    return await set_thread_resolution_state(
        thread_id,
        resolve=False,
        runner=runner,
    )


def graphql_error_summary(data: object) -> str | None:
    """Return a compact message summary from a GraphQL error payload."""
    errors = _graphql_errors(data)
    if isinstance(errors, list):
        messages: list[str] = []
        for error in errors:
            error_data = _mapping(error)
            message = error_data.get("message")
            if isinstance(message, str) and message:
                messages.append(message)
        if messages:
            return "; ".join(messages)
    return str(errors) if errors else None


def _graphql_errors(data: object) -> object | None:
    return _mapping(data).get("errors")


def _mapping(data: object) -> dict[object, object]:
    if not isinstance(data, Mapping):
        return {}
    return {key: value for key, value in data.items()}
