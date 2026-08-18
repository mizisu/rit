import json
from typing import Any

import pytest

from rit.services.pr_review_graphql import (
    create_pending_review,
    delete_pending_review,
    graphql_request,
    list_review_comments,
    submit_pending_review,
)
from rit.state.models import PendingReviewComment, ReviewState


def test_graphql_request_sends_query_and_variables_through_stdin() -> None:
    request = graphql_request("query($id: ID!) { node(id: $id) { id } }", {"id": "n1"})

    assert request.args == ("api", "graphql", "--input", "-")
    assert json.loads(request.input_text) == {
        "query": "query($id: ID!) { node(id: $id) { id } }",
        "variables": {"id": "n1"},
    }


@pytest.mark.asyncio
async def test_create_pending_review_uses_graphql_threads_payload() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append((args, payload))
        if len(calls) == 1:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_node",
                                "reviews": {"nodes": []},
                            }
                        }
                    }
                }
            )
        return json.dumps(
            {
                "data": {
                    "addPullRequestReview": {
                        "pullRequestReview": {
                            "nodeId": "review_node",
                            "databaseId": 80,
                            "state": "PENDING",
                            "body": "",
                            "comments": {"nodes": []},
                        }
                    }
                }
            }
        )

    review = await create_pending_review(
        "owner",
        "repo",
        123,
        comments=[
            PendingReviewComment(
                body="range",
                path="src/app.py",
                line=12,
                side="RIGHT",
                start_line=4,
                start_side="RIGHT",
            )
        ],
        commit_id="deadbeef",
        runner=runner,
    )

    assert review.id == 80
    assert review.node_id == "review_node"
    assert review.state == ReviewState.PENDING
    assert calls[0][0] == ["api", "graphql", "--input", "-"]
    mutation_input = calls[1][1]["variables"]["input"]
    assert mutation_input == {
        "pullRequestId": "PR_node",
        "commitOID": "deadbeef",
        "threads": [
            {
                "path": "src/app.py",
                "line": 12,
                "side": "RIGHT",
                "body": "range",
                "startLine": 4,
                "startSide": "RIGHT",
            }
        ],
    }


@pytest.mark.asyncio
async def test_create_pending_review_adds_file_level_thread_to_pending_review() -> None:
    calls: list[dict[str, Any]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if len(calls) == 1:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_node",
                                "reviews": {"nodes": []},
                            }
                        }
                    }
                }
            )
        if len(calls) == 2:
            return json.dumps(
                {
                    "data": {
                        "addPullRequestReview": {
                            "pullRequestReview": {
                                "nodeId": "review_node",
                                "databaseId": 80,
                                "state": "PENDING",
                                "body": "",
                                "comments": {"nodes": []},
                            }
                        }
                    }
                }
            )
        return json.dumps(
            {"data": {"addPullRequestReviewThread": {"thread": {"id": "thread_node"}}}}
        )

    review = await create_pending_review(
        "owner",
        "repo",
        123,
        comments=[
            PendingReviewComment(
                body="whole file",
                path="src/app.py",
                line=0,
                subject_type="file",
            )
        ],
        commit_id="deadbeef",
        runner=runner,
    )

    assert review.id == 80
    assert calls[1]["variables"]["input"] == {
        "pullRequestId": "PR_node",
        "commitOID": "deadbeef",
    }
    assert calls[2]["variables"]["input"] == {
        "pullRequestReviewId": "review_node",
        "body": "whole file",
        "path": "src/app.py",
        "subjectType": "FILE",
    }


@pytest.mark.asyncio
async def test_list_review_comments_prefers_comment_range_over_thread_range() -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "id": "thread_node",
                                        "path": "src/app.py",
                                        "line": 205,
                                        "originalLine": 205,
                                        "startLine": 195,
                                        "originalStartLine": 195,
                                        "diffSide": "RIGHT",
                                        "startDiffSide": "RIGHT",
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "nodeId": "PRRC_node",
                                                    "databaseId": 300,
                                                    "body": "range",
                                                    "path": "src/app.py",
                                                    "line": 205,
                                                    "originalLine": 205,
                                                    "startLine": 201,
                                                    "originalStartLine": 201,
                                                    "commit": {"oid": "old-head"},
                                                    "originalCommit": {
                                                        "oid": "old-head"
                                                    },
                                                    "pullRequestReview": {
                                                        "databaseId": 80
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        )

    comments = await list_review_comments(
        "owner",
        "repo",
        123,
        review_id=80,
        runner=runner,
    )

    assert len(comments) == 1
    assert comments[0].id == 300
    assert comments[0].node_id == "PRRC_node"
    assert comments[0].line == 205
    assert comments[0].start_line == 201
    assert comments[0].side == "RIGHT"
    assert comments[0].start_side == "RIGHT"
    assert comments[0].commit_id == "old-head"
    assert comments[0].original_commit_id == "old-head"


@pytest.mark.asyncio
async def test_list_review_comments_ignores_file_thread_line_placeholder() -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "id": "thread_node",
                                        "path": "src/app.py",
                                        "line": 1,
                                        "originalLine": 1,
                                        "diffSide": "RIGHT",
                                        "subjectType": "FILE",
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "databaseId": 300,
                                                    "body": "whole file",
                                                    "path": "src/app.py",
                                                    "subjectType": "FILE",
                                                    "pullRequestReview": {
                                                        "databaseId": 80
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        )

    comments = await list_review_comments(
        "owner",
        "repo",
        123,
        review_id=80,
        runner=runner,
    )

    assert len(comments) == 1
    assert comments[0].subject_type == "FILE"
    assert comments[0].line is None
    assert comments[0].original_line is None
    assert comments[0].anchor_line is None


@pytest.mark.asyncio
async def test_submit_and_delete_pending_review_use_review_node_id() -> None:
    calls: list[dict[str, Any]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if len(calls) in {1, 3}:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_node",
                                "reviews": {
                                    "nodes": [{"id": "review_node", "databaseId": 80}]
                                },
                            }
                        }
                    }
                }
            )
        if len(calls) == 2:
            return json.dumps(
                {
                    "data": {
                        "submitPullRequestReview": {
                            "pullRequestReview": {
                                "nodeId": "review_node",
                                "databaseId": 80,
                                "state": "COMMENTED",
                                "body": "done",
                            }
                        }
                    }
                }
            )
        return json.dumps(
            {
                "data": {
                    "deletePullRequestReview": {
                        "pullRequestReview": {
                            "nodeId": "review_node",
                            "databaseId": 80,
                            "state": "PENDING",
                            "body": "",
                        }
                    }
                }
            }
        )

    submitted = await submit_pending_review(
        "owner",
        "repo",
        123,
        review_id=80,
        event="COMMENT",
        body="done",
        runner=runner,
    )
    await delete_pending_review(
        "owner",
        "repo",
        123,
        review_id=80,
        runner=runner,
    )

    assert submitted.id == 80
    assert calls[1]["variables"] == {
        "input": {
            "pullRequestReviewId": "review_node",
            "event": "COMMENT",
            "body": "done",
        }
    }
    assert calls[3]["variables"] == {"reviewId": "review_node"}
