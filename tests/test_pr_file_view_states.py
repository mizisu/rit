import json
from collections.abc import Iterator, Mapping
from typing import get_type_hints

import pytest

from rit.services.graphql_mutations import GraphQLMutationError
import rit.services.pr_file_view_states as pr_file_view_states
from rit.services.pr_file_view_states import (
    FileViewMutationError,
    FileViewStatesGraphQLError,
    FileViewStatesPage,
    collect_file_view_states,
    ensure_file_view_mutation_result,
    ensure_file_view_mutation_succeeded,
    fetch_file_view_states,
    file_view_mutation_request,
    file_view_states_page_request,
    mark_file_as_viewed,
    parse_file_view_states_result,
    parse_file_view_states_page,
    set_file_viewed_state,
    unmark_file_as_viewed,
)


def test_parse_file_view_states_page_accepts_unknown_json_without_any_contract() -> None:
    assert get_type_hints(parse_file_view_states_page)["data"] is object


def test_parse_file_view_states_page_returns_states_and_next_cursor() -> None:
    page = parse_file_view_states_page(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "files": {
                            "nodes": [
                                {
                                    "path": "src/app.py",
                                    "viewerViewedState": "VIEWED",
                                },
                                {
                                    "path": "src/lib.py",
                                    "viewerViewedState": "UNVIEWED",
                                },
                            ],
                            "pageInfo": {
                                "hasNextPage": True,
                                "endCursor": "cursor-2",
                            },
                        }
                    }
                }
            }
        }
    )

    assert page.states == {
        "src/app.py": "VIEWED",
        "src/lib.py": "UNVIEWED",
    }
    assert page.has_next_page is True
    assert page.next_cursor == "cursor-2"


def test_parse_file_view_states_page_single_node_skips_iteration() -> None:
    class SingleNodeList(list):
        def __iter__(self):
            raise AssertionError("single viewed-state node should not be iterated")

    page = parse_file_view_states_page(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "files": {
                            "nodes": SingleNodeList(
                                [
                                    {
                                        "path": "src/app.py",
                                        "viewerViewedState": "VIEWED",
                                    }
                                ]
                            ),
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": None,
                            },
                        }
                    }
                }
            }
        }
    )

    assert page.states == {"src/app.py": "VIEWED"}
    assert page.has_next_page is False


def test_parse_file_view_states_page_uses_direct_mapping_lookup() -> None:
    class NoItemsMapping(Mapping[str, object]):
        def __init__(self, values: dict[str, object]) -> None:
            self._values = values

        def __getitem__(self, key: str) -> object:
            return self._values[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._values)

        def __len__(self) -> int:
            return len(self._values)

        def items(self):
            raise AssertionError("view state parser should not scan mapping items")

    page = parse_file_view_states_page(
        NoItemsMapping(
            {
                "data": NoItemsMapping(
                    {
                        "repository": NoItemsMapping(
                            {
                                "pullRequest": NoItemsMapping(
                                    {
                                        "files": NoItemsMapping(
                                            {
                                                "nodes": [
                                                    NoItemsMapping(
                                                        {
                                                            "path": "src/app.py",
                                                            "viewerViewedState": "VIEWED",
                                                        }
                                                    )
                                                ],
                                                "pageInfo": NoItemsMapping(
                                                    {
                                                        "hasNextPage": False,
                                                        "endCursor": None,
                                                    }
                                                ),
                                            }
                                        )
                                    }
                                )
                            }
                        )
                    }
                )
            }
        )
    )

    assert page.states == {"src/app.py": "VIEWED"}
    assert page.has_next_page is False


def test_parse_file_view_states_result_decodes_json_page() -> None:
    page = parse_file_view_states_result(
        json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "files": {
                                "nodes": [
                                    {
                                        "path": "src/app.py",
                                        "viewerViewedState": "VIEWED",
                                    }
                                ],
                                "pageInfo": {
                                    "hasNextPage": False,
                                    "endCursor": None,
                                },
                            }
                        }
                    }
                }
            }
        )
    )

    assert page.states == {"src/app.py": "VIEWED"}
    assert page.has_next_page is False
    assert page.next_cursor is None


def test_parse_file_view_states_result_treats_non_object_payload_as_complete() -> None:
    page = parse_file_view_states_result("[]")

    assert page.states == {}
    assert page.has_next_page is False
    assert page.next_cursor is None


def test_parse_file_view_states_page_treats_missing_pull_request_as_complete() -> None:
    page = parse_file_view_states_page({"data": {"repository": {}}})

    assert page.states == {}
    assert page.has_next_page is False
    assert page.next_cursor is None


def test_parse_file_view_states_page_raises_graphql_errors() -> None:
    with pytest.raises(FileViewStatesGraphQLError) as exc_info:
        parse_file_view_states_page({"errors": [{"message": "nope"}]})

    assert "nope" in str(exc_info.value)


@pytest.mark.asyncio
async def test_collect_file_view_states_fetches_until_last_page() -> None:
    cursors: list[str | None] = []

    async def fetch_page(cursor: str | None) -> FileViewStatesPage:
        cursors.append(cursor)
        if cursor is None:
            return FileViewStatesPage(
                states={"src/app.py": "VIEWED"},
                has_next_page=True,
                next_cursor="cursor-2",
            )
        return FileViewStatesPage(
            states={"src/lib.py": "UNVIEWED"},
            has_next_page=False,
            next_cursor=None,
        )

    states = await collect_file_view_states(fetch_page)

    assert cursors == [None, "cursor-2"]
    assert states == {
        "src/app.py": "VIEWED",
        "src/lib.py": "UNVIEWED",
    }


@pytest.mark.asyncio
async def test_fetch_file_view_states_runs_paginated_graphql_requests() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        if "after=cursor-2" in args:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "files": {
                                    "nodes": [
                                        {
                                            "path": "src/lib.py",
                                            "viewerViewedState": "UNVIEWED",
                                        }
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                }
                            }
                        }
                    }
                }
            )
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "files": {
                                "nodes": [
                                    {
                                        "path": "src/app.py",
                                        "viewerViewedState": "VIEWED",
                                    }
                                ],
                                "pageInfo": {
                                    "hasNextPage": True,
                                    "endCursor": "cursor-2",
                                },
                            }
                        }
                    }
                }
            }
        )

    states = await fetch_file_view_states(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert states == {
        "src/app.py": "VIEWED",
        "src/lib.py": "UNVIEWED",
    }
    assert calls[0][0][:2] == ["api", "graphql"]
    assert "owner=owner" in calls[0][0]
    assert "repo=repo" in calls[0][0]
    assert "number=123" in calls[0][0]
    assert calls[0][1] is None
    assert "after=cursor-2" in calls[1][0]
    assert calls[1][1] is None


def test_file_view_states_page_request_builds_paginated_graphql_args() -> None:
    first_page = file_view_states_page_request(
        owner="owner",
        repo="repo",
        pr_number=123,
        cursor=None,
    )
    next_page = file_view_states_page_request(
        owner="owner",
        repo="repo",
        pr_number=123,
        cursor="cursor-2",
    )

    assert first_page[:2] == ("api", "graphql")
    assert "-F" in first_page
    assert "owner=owner" in first_page
    assert "repo=repo" in first_page
    assert "number=123" in first_page
    assert not any(arg == "after=cursor-2" for arg in first_page)
    assert "after=cursor-2" in next_page


def test_file_view_states_first_page_request_avoids_list_to_tuple_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pr_file_view_states,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("first viewed-state page request should not rebuild args")
        ),
        raising=False,
    )

    request = file_view_states_page_request(
        owner="owner",
        repo="repo",
        pr_number=123,
        cursor=None,
    )

    assert request[:2] == ("api", "graphql")
    assert "number=123" in request


def test_file_view_states_cursor_page_request_avoids_list_to_tuple_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pr_file_view_states,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cursor viewed-state page request should not rebuild args")
        ),
        raising=False,
    )

    request = file_view_states_page_request(
        owner="owner",
        repo="repo",
        pr_number=123,
        cursor="cursor-2",
    )

    assert request[:2] == ("api", "graphql")
    assert request[-2:] == ("-f", "after=cursor-2")


def test_file_view_mutation_request_uses_mark_or_unmark_mutation() -> None:
    mark = file_view_mutation_request(
        pull_request_id="PR_kw",
        path="src/app.py",
        viewed=True,
    )
    unmark = file_view_mutation_request(
        pull_request_id="PR_kw",
        path="src/app.py",
        viewed=False,
    )

    assert mark.action == "mark file as viewed"
    assert mark.viewed is True
    assert mark.args[:2] == ("api", "graphql")
    assert "pullRequestId=PR_kw" in mark.args
    assert "path=src/app.py" in mark.args
    assert any("markFileAsViewed" in arg for arg in mark.args)
    assert unmark.action == "unmark file as viewed"
    assert unmark.viewed is False
    assert any("unmarkFileAsViewed" in arg for arg in unmark.args)


def test_ensure_file_view_mutation_result_checks_graphql_errors() -> None:
    ensure_file_view_mutation_result(json.dumps({"data": {"markFileAsViewed": {}}}))

    with pytest.raises(GraphQLMutationError) as exc_info:
        ensure_file_view_mutation_result(
            json.dumps({"errors": [{"message": "mutation failed"}]})
        )

    assert "mutation failed" in str(exc_info.value)


def test_ensure_file_view_mutation_succeeded_wraps_mark_failures() -> None:
    request = file_view_mutation_request(
        pull_request_id="PR_kw",
        path="src/app.py",
        viewed=True,
    )

    with pytest.raises(FileViewMutationError) as exc_info:
        ensure_file_view_mutation_succeeded(
            json.dumps({"errors": [{"message": "mutation failed"}]}),
            request,
        )

    assert str(exc_info.value) == "Failed to mark file as viewed: mutation failed"


def test_ensure_file_view_mutation_succeeded_wraps_unmark_failures() -> None:
    request = file_view_mutation_request(
        pull_request_id="PR_kw",
        path="src/app.py",
        viewed=False,
    )

    with pytest.raises(FileViewMutationError) as exc_info:
        ensure_file_view_mutation_succeeded(
            json.dumps({"errors": [{"message": "mutation failed"}]}),
            request,
        )

    assert str(exc_info.value) == "Failed to unmark file as viewed: mutation failed"


def test_ensure_file_view_mutation_succeeded_accepts_successful_results() -> None:
    request = file_view_mutation_request(
        pull_request_id="PR_kw",
        path="src/app.py",
        viewed=True,
    )

    ensure_file_view_mutation_succeeded(
        json.dumps({"data": {"markFileAsViewed": {}}}),
        request,
    )


@pytest.mark.asyncio
async def test_set_file_viewed_state_runs_mutation_and_checks_result() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"data": {"markFileAsViewed": {}}})

    await set_file_viewed_state(
        pull_request_id="PR_kw",
        path="src/app.py",
        viewed=True,
        runner=runner,
    )

    assert len(calls) == 1
    args, input_text = calls[0]
    assert args[:2] == ["api", "graphql"]
    assert any("markFileAsViewed" in arg for arg in args)
    assert "pullRequestId=PR_kw" in args
    assert "path=src/app.py" in args
    assert input_text is None


@pytest.mark.asyncio
async def test_mark_file_as_viewed_runs_mark_mutation() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"data": {"markFileAsViewed": {}}})

    await mark_file_as_viewed(
        pull_request_id="PR_kw",
        path="src/app.py",
        runner=runner,
    )

    assert len(calls) == 1
    assert any("markFileAsViewed" in arg for arg in calls[0][0])
    assert not any("unmarkFileAsViewed" in arg for arg in calls[0][0])
    assert calls[0][1] is None


@pytest.mark.asyncio
async def test_unmark_file_as_viewed_runs_unmark_mutation() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"data": {"unmarkFileAsViewed": {}}})

    await unmark_file_as_viewed(
        pull_request_id="PR_kw",
        path="src/app.py",
        runner=runner,
    )

    assert len(calls) == 1
    assert any("unmarkFileAsViewed" in arg for arg in calls[0][0])
    assert calls[0][1] is None


@pytest.mark.asyncio
async def test_set_file_viewed_state_wraps_mutation_failures() -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        return json.dumps({"errors": [{"message": "mutation failed"}]})

    with pytest.raises(
        FileViewMutationError,
        match="Failed to unmark file as viewed: mutation failed",
    ):
        await set_file_viewed_state(
            pull_request_id="PR_kw",
            path="src/app.py",
            viewed=False,
            runner=runner,
        )
