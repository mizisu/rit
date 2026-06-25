import json
import inspect

import pytest

import rit.services.pr_graphql_response as pr_graphql_response_module
from rit.services.pr_graphql_response import (
    PullRequestGraphQLError,
    PullRequestNotFound,
    fetch_pull_request_all,
    fetch_pull_request_graphql_node_id,
    fetch_pull_request_graphql_pr,
    fetch_pull_request_summary,
    parse_pull_request_graphql_data,
    parse_pull_request_graphql_node_id_result,
    parse_pull_request_graphql_pr,
    parse_pull_request_graphql_pr_result,
)
from rit.services.pr_graphql_queries import PullRequestGraphQLView


def test_pr_graphql_response_parser_does_not_use_runtime_casts() -> None:
    source = inspect.getsource(pr_graphql_response_module)

    assert "cast(" not in source


def test_parse_pull_request_graphql_data_returns_pull_request_node() -> None:
    pr_data = {"number": 123, "title": "Ship it"}

    parsed = parse_pull_request_graphql_data(
        {
            "data": {
                "repository": {
                    "pullRequest": pr_data,
                }
            }
        },
        pr_number=123,
    )

    assert parsed == pr_data


def test_parse_pull_request_graphql_data_uses_direct_dict_lookup() -> None:
    class NoItemsDict(dict):
        def items(self):
            raise AssertionError("GraphQL parser should not scan mapping items")

    pr_data = NoItemsDict({"number": 123, "title": "Ship it"})

    parsed = parse_pull_request_graphql_data(
        NoItemsDict(
            {
                "data": NoItemsDict(
                    {
                        "repository": NoItemsDict(
                            {
                                "pullRequest": pr_data,
                            }
                        )
                    }
                )
            }
        ),
        pr_number=123,
    )

    assert parsed == {"number": 123, "title": "Ship it"}


def test_parse_pull_request_graphql_data_single_key_dict_skips_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr_data = {"number": 123}
    monkeypatch.setattr(
        pr_graphql_response_module,
        "all",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single-key GraphQL PR dict should not scan all keys")
        ),
        raising=False,
    )

    parsed = parse_pull_request_graphql_data(
        {"data": {"repository": {"pullRequest": pr_data}}},
        pr_number=123,
    )

    assert parsed is pr_data


def test_parse_pull_request_graphql_data_raises_graphql_errors() -> None:
    with pytest.raises(PullRequestGraphQLError) as exc_info:
        parse_pull_request_graphql_data(
            {"errors": [{"message": "rate limited"}]},
            pr_number=123,
        )

    assert "rate limited" in str(exc_info.value)


def test_parse_pull_request_graphql_data_raises_not_found_for_missing_pr() -> None:
    with pytest.raises(PullRequestNotFound) as exc_info:
        parse_pull_request_graphql_data(
            {"data": {"repository": {"pullRequest": None}}},
            pr_number=123,
        )

    assert "PR #123 not found" in str(exc_info.value)


def test_parse_pull_request_graphql_pr_returns_pr_model() -> None:
    pr = parse_pull_request_graphql_pr(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "id": "PR_node",
                        "number": 123,
                        "title": "Ship it",
                        "author": {"login": "alice"},
                    }
                }
            }
        },
        pr_number=123,
    )

    assert pr.node_id == "PR_node"
    assert pr.number == 123
    assert pr.title == "Ship it"
    assert pr.user is not None
    assert pr.user.login == "alice"


def test_parse_pull_request_graphql_pr_result_decodes_pr_model() -> None:
    pr = parse_pull_request_graphql_pr_result(
        json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                            "number": 123,
                            "title": "Ship it",
                            "author": {"login": "alice"},
                        }
                    }
                }
            }
        ),
        pr_number=123,
    )

    assert pr.node_id == "PR_node"
    assert pr.number == 123
    assert pr.title == "Ship it"


def test_parse_pull_request_graphql_node_id_result_decodes_node_id() -> None:
    node_id = parse_pull_request_graphql_node_id_result(
        json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                        }
                    }
                }
            }
        ),
        pr_number=123,
    )

    assert node_id == "PR_node"


def test_parse_pull_request_graphql_node_id_result_raises_when_id_missing() -> None:
    with pytest.raises(PullRequestNotFound, match="PR #123 node ID not found"):
        parse_pull_request_graphql_node_id_result(
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "",
                            }
                        }
                    }
                }
            ),
            pr_number=123,
        )


@pytest.mark.asyncio
async def test_fetch_pull_request_graphql_pr_runs_request_and_parses_model() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                            "number": 123,
                            "title": "Ship it",
                            "author": {"login": "alice"},
                        }
                    }
                }
            }
        )

    pr = await fetch_pull_request_graphql_pr(
        view=PullRequestGraphQLView.SUMMARY,
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert pr.node_id == "PR_node"
    assert pr.number == 123
    assert pr.title == "Ship it"
    assert pr.user is not None
    assert pr.user.login == "alice"
    assert len(calls) == 1
    args, input_text = calls[0]
    assert args[:2] == ["api", "graphql"]
    assert "owner=owner" in args
    assert "repo=repo" in args
    assert "number=123" in args
    assert input_text is None


@pytest.mark.asyncio
async def test_fetch_pull_request_summary_runs_summary_view_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                            "number": 123,
                            "title": "Ship it",
                            "body": "summary body",
                        }
                    }
                }
            }
        )

    pr = await fetch_pull_request_summary(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert pr.title == "Ship it"
    assert pr.body == "summary body"
    assert len(calls) == 1
    args, input_text = calls[0]
    assert args[:2] == ["api", "graphql"]
    assert "owner=owner" in args
    assert "repo=repo" in args
    assert "number=123" in args
    assert any("\n      body\n" in arg for arg in args)
    assert input_text is None


@pytest.mark.asyncio
async def test_fetch_pull_request_all_runs_full_view_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                            "number": 123,
                            "title": "Ship it",
                            "files": {"nodes": []},
                        }
                    }
                }
            }
        )

    pr = await fetch_pull_request_all(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert pr.number == 123
    assert pr.title == "Ship it"
    assert len(calls) == 1
    args, input_text = calls[0]
    assert args[:2] == ["api", "graphql"]
    assert any("reviewThreads(first: 100)" in arg for arg in args)
    assert input_text is None


@pytest.mark.asyncio
async def test_fetch_pull_request_graphql_node_id_runs_request_and_parses_id() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                        }
                    }
                }
            }
        )

    node_id = await fetch_pull_request_graphql_node_id(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert node_id == "PR_node"
    assert len(calls) == 1
    args, input_text = calls[0]
    assert args[:2] == ["api", "graphql"]
    assert "owner=owner" in args
    assert "repo=repo" in args
    assert "number=123" in args
    assert input_text is None
