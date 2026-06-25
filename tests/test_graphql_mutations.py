import inspect
import json

import pytest

import rit.services.graphql_mutations as graphql_mutations_module
from rit.services.graphql_mutations import (
    GraphQLMutationError,
    ThreadResolutionMutationError,
    ensure_graphql_mutation_succeeded,
    parse_thread_resolution_mutation_succeeded,
    parse_thread_resolution_mutation_result,
    parse_thread_resolution_result,
    resolve_thread,
    run_thread_resolution_mutation,
    set_thread_resolution_state,
    thread_resolution_mutation_request,
    unresolve_thread,
)


def test_graphql_mutations_parser_does_not_use_runtime_casts() -> None:
    source = inspect.getsource(graphql_mutations_module)

    assert "cast(" not in source


def test_parse_thread_resolution_result_returns_true_when_state_matches() -> None:
    result = parse_thread_resolution_result(
        {
            "data": {
                "resolveReviewThread": {
                    "thread": {
                        "isResolved": True,
                    }
                }
            }
        },
        mutation_name="resolveReviewThread",
        expected_resolved=True,
    )

    assert result is True


def test_parse_thread_resolution_result_uses_direct_mapping_lookup() -> None:
    class NoItemsDict(dict):
        def items(self):
            raise AssertionError("GraphQL mutation parser should not copy mappings")

    result = parse_thread_resolution_result(
        NoItemsDict(
            {
                "data": NoItemsDict(
                    {
                        "resolveReviewThread": NoItemsDict(
                            {
                                "thread": NoItemsDict(
                                    {
                                        "isResolved": True,
                                    }
                                )
                            }
                        )
                    }
                )
            }
        ),
        mutation_name="resolveReviewThread",
        expected_resolved=True,
    )

    assert result is True


def test_parse_thread_resolution_mutation_result_decodes_request_result() -> None:
    request = thread_resolution_mutation_request("thread-1", resolve=True)

    result = parse_thread_resolution_mutation_result(
        json.dumps(
            {
                "data": {
                    "resolveReviewThread": {
                        "thread": {
                            "isResolved": True,
                        }
                    }
                }
            }
        ),
        request,
    )

    assert result is True


async def test_run_thread_resolution_mutation_runs_request_and_parses_result() -> None:
    request = thread_resolution_mutation_request("thread-1", resolve=True)
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "resolveReviewThread": {
                        "thread": {
                            "isResolved": True,
                        }
                    }
                }
            }
        )

    result = await run_thread_resolution_mutation(request, runner)

    assert result is True
    assert calls == [(list(request.args), None)]


async def test_set_thread_resolution_state_builds_runs_and_parses_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "unresolveReviewThread": {
                        "thread": {
                            "isResolved": False,
                        }
                    }
                }
            }
        )

    result = await set_thread_resolution_state(
        "thread-1",
        resolve=False,
        runner=runner,
    )

    assert result is True
    assert calls[0][0][:2] == ["api", "graphql"]
    assert any("unresolveReviewThread" in arg for arg in calls[0][0])
    assert calls[0][0][-2:] == ["-F", "threadId=thread-1"]
    assert calls[0][1] is None


async def test_resolve_thread_runs_resolve_mutation() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "resolveReviewThread": {
                        "thread": {
                            "isResolved": True,
                        }
                    }
                }
            }
        )

    result = await resolve_thread("thread-1", runner=runner)

    assert result is True
    assert any("resolveReviewThread" in arg for arg in calls[0][0])
    assert not any("unresolveReviewThread" in arg for arg in calls[0][0])
    assert calls[0][0][-2:] == ["-F", "threadId=thread-1"]
    assert calls[0][1] is None


async def test_unresolve_thread_runs_unresolve_mutation() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "unresolveReviewThread": {
                        "thread": {
                            "isResolved": False,
                        }
                    }
                }
            }
        )

    result = await unresolve_thread("thread-1", runner=runner)

    assert result is True
    assert any("unresolveReviewThread" in arg for arg in calls[0][0])
    assert calls[0][0][-2:] == ["-F", "threadId=thread-1"]
    assert calls[0][1] is None


def test_parse_thread_resolution_mutation_succeeded_wraps_graphql_errors() -> None:
    request = thread_resolution_mutation_request("thread-1", resolve=False)

    with pytest.raises(ThreadResolutionMutationError) as exc_info:
        parse_thread_resolution_mutation_succeeded(
            json.dumps({"errors": [{"message": "permission denied"}]}),
            request,
        )

    assert str(exc_info.value) == "Failed to unresolve thread: permission denied"
    assert isinstance(exc_info.value.__cause__, GraphQLMutationError)


def test_parse_thread_resolution_mutation_succeeded_returns_resolution_state() -> None:
    request = thread_resolution_mutation_request("thread-1", resolve=True)

    result = parse_thread_resolution_mutation_succeeded(
        json.dumps(
            {
                "data": {
                    "resolveReviewThread": {
                        "thread": {
                            "isResolved": True,
                        }
                    }
                }
            }
        ),
        request,
    )

    assert result is True


def test_parse_thread_resolution_result_returns_false_when_state_mismatches() -> None:
    result = parse_thread_resolution_result(
        {
            "data": {
                "unresolveReviewThread": {
                    "thread": {
                        "isResolved": True,
                    }
                }
            }
        },
        mutation_name="unresolveReviewThread",
        expected_resolved=False,
    )

    assert result is False


def test_parse_thread_resolution_result_treats_missing_thread_as_mismatch() -> None:
    result = parse_thread_resolution_result(
        {"data": {"resolveReviewThread": {}}},
        mutation_name="resolveReviewThread",
        expected_resolved=True,
    )

    assert result is False


def test_parse_thread_resolution_result_raises_graphql_errors() -> None:
    with pytest.raises(GraphQLMutationError) as exc_info:
        parse_thread_resolution_result(
            {"errors": [{"message": "permission denied"}]},
            mutation_name="resolveReviewThread",
            expected_resolved=True,
        )

    assert "permission denied" in str(exc_info.value)


def test_ensure_graphql_mutation_succeeded_raises_graphql_errors() -> None:
    with pytest.raises(GraphQLMutationError) as exc_info:
        ensure_graphql_mutation_succeeded(
            {"errors": [{"message": "mark failed"}]},
        )

    assert "mark failed" in str(exc_info.value)


def test_ensure_graphql_mutation_succeeded_accepts_empty_success_payload() -> None:
    ensure_graphql_mutation_succeeded({"data": {"markFileAsViewed": None}})


def test_thread_resolution_mutation_request_builds_resolve_graphql_args() -> None:
    request = thread_resolution_mutation_request("thread-1", resolve=True)

    assert request.mutation_name == "resolveReviewThread"
    assert request.expected_resolved is True
    assert request.action == "resolve"
    assert request.args[:4] == ("api", "graphql", "-f", request.args[3])
    assert "resolveReviewThread(input:" in request.args[3]
    assert request.args[-2:] == ("-F", "threadId=thread-1")


def test_thread_resolution_mutation_request_builds_unresolve_graphql_args() -> None:
    request = thread_resolution_mutation_request("thread-1", resolve=False)

    assert request.mutation_name == "unresolveReviewThread"
    assert request.expected_resolved is False
    assert request.action == "unresolve"
    assert "unresolveReviewThread(input:" in request.args[3]
