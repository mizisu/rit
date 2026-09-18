import json
from typing import Any

import pytest

from rit.services.github import GitHubError, GitHubService
from rit.services.graphql_mutations import GraphQLMutationError
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


@pytest.mark.parametrize("failure", [None, "cli", "graphql", "offline"])
async def test_body_only_review_creation_and_updates_never_submit(
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    calls: list[dict[str, Any]] = []
    state = "PENDING"

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if payload["query"].lstrip().startswith("query"):
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_node",
                                "reviews": {
                                    "nodes": [
                                        {
                                            "id": "review_node",
                                            "databaseId": 80,
                                            "state": state,
                                        }
                                    ]
                                },
                            }
                        }
                    }
                }
            )
        name = (
            "updatePullRequestReview"
            if "updatePullRequestReview(" in payload["query"]
            else "addPullRequestReview"
        )
        if name == "updatePullRequestReview" and failure:
            message = "Could not edit a review with a missing body."
            if failure == "graphql":
                return json.dumps({"errors": [{"message": message}]})
            raise GitHubError("offline" if failure == "offline" else f"gh: {message}")
        return json.dumps(
            {
                "data": {
                    name: {
                        "pullRequestReview": {
                            "databaseId": 80,
                            "nodeId": "review_node",
                            "state": "PENDING",
                            "body": payload["variables"]["input"]["body"],
                        }
                    }
                }
            }
        )

    service = GitHubService(owner="owner", repo="repo")
    monkeypatch.setattr(service, "_run_gh", runner)
    await service.create_pending_review(123, comments=[], body="draft")
    assert calls[-1]["variables"]["input"] == {
        "pullRequestId": "PR_node",
        "body": "draft",
    }
    if failure == "offline":
        with pytest.raises(GitHubError, match="offline"):
            await service.update_pending_review(123, 80, body="updated draft")
    else:
        updated = await service.update_pending_review(123, 80, body="updated draft")
        if failure:
            assert updated is None
        else:
            assert updated is not None and updated.body == "updated draft"
    assert calls[-1]["variables"]["input"] == {
        "pullRequestReviewId": "review_node",
        "body": "updated draft",
    }
    assert "updatePullRequestReview(" in calls[-1]["query"]

    state = "COMMENTED"
    with pytest.raises(GraphQLMutationError, match="no longer pending"):
        await service.update_pending_review(
            123, 80, body="do not edit a published review"
        )
    assert len(calls) == 5
    assert all("submitPullRequestReview(" not in call["query"] for call in calls)


@pytest.mark.parametrize("count", [None, 0, 1])
async def test_delete_body_only_review_requires_verified_empty_comments(
    count: int | None,
) -> None:
    calls: list[dict[str, Any]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                            "reviews": {
                                "nodes": [
                                    {
                                        "id": "review_node",
                                        "databaseId": 80,
                                        "comments": {"totalCount": count},
                                    }
                                ]
                            },
                        }
                    }
                }
            }
        )

    if count == 0:
        await delete_pending_review(
            "owner", "repo", 123, review_id=80, runner=runner, require_empty=True
        )
        assert len(calls) == 2
        assert "deletePullRequestReview(" in calls[-1]["query"]
    else:
        with pytest.raises(GraphQLMutationError, match="not replacing pending review"):
            await delete_pending_review(
                "owner", "repo", 123, review_id=80, runner=runner, require_empty=True
            )
        assert len(calls) == 1
