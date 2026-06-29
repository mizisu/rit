import json

import pytest

from rit.services.pr_discussion import (
    discussion_from_pr,
    fast_discussion_from_data,
    fast_discussion_from_result,
    fetch_pr_discussion,
    fetch_pr_discussion_fast,
)
from rit.state.models import PR, ReviewState, ReviewThread


def _graphql_pr_data() -> dict[str, object]:
    return {
        "body": "PR body",
        "reviews": {
            "nodes": [
                {
                    "databaseId": 200,
                    "body": "review body",
                    "state": "COMMENTED",
                }
            ]
        },
        "reviewThreads": {
            "nodes": [
                {
                    "id": "thread-300",
                    "path": "app.py",
                    "line": 12,
                    "diffSide": "RIGHT",
                    "comments": {
                        "nodes": [
                            {
                                "databaseId": 300,
                                "body": "thread comment",
                                "path": "app.py",
                                "line": 12,
                                "pullRequestReview": {"databaseId": 200},
                            }
                        ]
                    },
                }
            ]
        },
        "comments": {"nodes": [{"databaseId": 100, "body": "issue comment"}]},
    }


def _graphql_result() -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": _graphql_pr_data(),
                }
            }
        }
    )


def test_discussion_from_pr_projects_full_graphql_discussion() -> None:
    discussion = discussion_from_pr(PR.model_validate(_graphql_pr_data()))

    assert discussion.body == "PR body"
    assert [
        (review.id, review.body, review.state) for review in discussion.reviews
    ] == [(200, "review body", ReviewState.COMMENTED)]
    assert [(comment.id, comment.body) for comment in discussion.issue_comments] == [
        (100, "issue comment")
    ]
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300


def test_fast_discussion_from_data_uses_graphql_review_threads() -> None:
    discussion = fast_discussion_from_data(_graphql_pr_data())

    assert discussion.body == "PR body"
    assert len(discussion.review_threads) == 1
    thread: ReviewThread = discussion.review_threads[0]
    assert thread.path == "app.py"
    assert thread.diff_side == "RIGHT"
    assert thread.root_comment_id == 300
    assert thread.comments[0].pull_request_review_id == 200


def test_fast_discussion_from_result_decodes_graphql_pr() -> None:
    discussion = fast_discussion_from_result(_graphql_result(), pr_number=123)

    assert discussion.body == "PR body"
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300


@pytest.mark.asyncio
async def test_fetch_pr_discussion_runs_full_graphql_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return _graphql_result()

    discussion = await fetch_pr_discussion(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert discussion.body == "PR body"
    assert len(discussion.review_threads) == 1
    assert len(calls) == 1
    args, input_text = calls[0]
    assert args[:2] == ["api", "graphql"]
    assert "owner=owner" in args
    assert "repo=repo" in args
    assert "number=123" in args
    assert input_text is None


@pytest.mark.asyncio
async def test_fetch_pr_discussion_fast_runs_one_graphql_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return _graphql_result()

    discussion = await fetch_pr_discussion_fast(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert discussion.body == "PR body"
    assert len(discussion.review_threads) == 1
    assert len(calls) == 1
    assert calls[0][0][:2] == ["api", "graphql"]
    assert "owner=owner" in calls[0][0]
    assert "repo=repo" in calls[0][0]
    assert "number=123" in calls[0][0]
    assert calls[0][1] is None
