import json
from datetime import UTC, datetime

import pytest

from rit.services.pr_discussion import (
    discussion_from_pr,
    fast_discussion_from_data,
    fast_discussion_from_result,
    fetch_pr_discussion,
    fetch_pr_discussion_fast,
)
from rit.services.pr_graphql_response import (
    PullRequestGraphQLError,
    fetch_pull_request_all,
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
                                "createdAt": "2026-06-29T13:12:45Z",
                                "publishedAt": "2026-07-05T11:38:20Z",
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
        "timelineItems": {
            "nodes": [
                {
                    "id": "commit-1",
                    "__typename": "PullRequestCommit",
                    "commit": {
                        "oid": "a" * 40,
                        "messageHeadline": "Keep [red]literal[/] text",
                        "committedDate": "2026-06-18T06:00:00Z",
                        "author": {"name": "Guest", "user": None},
                    },
                },
                {
                    "id": "force-1",
                    "__typename": "HeadRefForcePushedEvent",
                    "createdAt": "2026-06-18T07:00:00Z",
                    "actor": None,
                    "beforeCommit": None,
                    "afterCommit": {"oid": "b" * 40},
                },
                {
                    "id": "request-1",
                    "__typename": "ReviewRequestedEvent",
                    "createdAt": "2026-06-18T08:00:00Z",
                    "actor": {"login": "alice"},
                    "requestedReviewer": {"name": "Backend"},
                },
                {
                    "id": "issue-100",
                    "__typename": "IssueComment",
                    "databaseId": 100,
                    "createdAt": "2026-06-18T08:30:00Z",
                },
                {
                    "id": "review-200",
                    "__typename": "PullRequestReview",
                    "databaseId": 200,
                    "createdAt": "2026-06-18T09:00:00Z",
                },
            ],
        },
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
    assert discussion.review_threads[0].comments[0].published_at == datetime(
        2026, 7, 5, 11, 38, 20, tzinfo=UTC
    )
    commit, push, request, issue, review = discussion.timeline_events
    assert (issue.database_id, review.database_id) == (100, 200)
    assert (
        commit.actor,
        commit.created_at,
        commit.commit_oid,
        commit.commit_message,
    ) == (
        "Guest",
        datetime(2026, 6, 18, 6, tzinfo=UTC),
        "a" * 40,
        "Keep [red]literal[/] text",
    )
    assert (push.actor, push.before_oid, push.after_oid) == (None, "", "b" * 40)
    assert (request.actor, request.reviewer, request.reviewer_team) == (
        "alice",
        None,
        "Backend",
    )


def test_fast_discussion_from_data_uses_graphql_review_threads() -> None:
    discussion = fast_discussion_from_data(_graphql_pr_data())

    assert discussion.body == "PR body"
    assert len(discussion.review_threads) == 1
    thread: ReviewThread = discussion.review_threads[0]
    assert thread.path == "app.py"
    assert thread.diff_side == "RIGHT"
    assert thread.root_comment_id == 300
    assert thread.comments[0].pull_request_review_id == 200
    assert thread.comments[0].published_at == datetime(
        2026, 7, 5, 11, 38, 20, tzinfo=UTC
    )


def test_fast_discussion_from_result_decodes_graphql_pr() -> None:
    discussion = fast_discussion_from_result(_graphql_result(), pr_number=123)

    assert discussion.body == "PR body"
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300


@pytest.mark.parametrize(
    "fetch", [fetch_pr_discussion, fetch_pr_discussion_fast, fetch_pull_request_all]
)
@pytest.mark.asyncio
async def test_discussion_fetches_remaining_activity_pages_without_repeating_comments(
    fetch,
) -> None:
    events = [
        event.model_dump(mode="json")
        for event in PR.model_validate(_graphql_pr_data()).timeline_events
    ]
    calls = []

    def result(nodes, page_info):
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "timelineItems": {"nodes": nodes, "pageInfo": page_info}
                        }
                    }
                }
            }
        )

    async def runner(args, *, input_text=None):
        calls.append(args)
        if len(calls) == 1:
            return result(events[:1], {"hasNextPage": True, "endCursor": "cursor-1"})
        return result(
            events[1:2], {"hasNextPage": True, "endCursor": "cursor-2"}
        ) + result(events[2:], {"hasNextPage": False, "endCursor": "cursor-3"})

    discussion = await fetch(owner="owner", repo="repo", pr_number=123, runner=runner)

    assert [event.id for event in discussion.timeline_events] == [
        "commit-1",
        "force-1",
        "request-1",
        "issue-100",
        "review-200",
    ]
    assert len(calls) == 2
    assert "--paginate" in calls[1]
    assert "endCursor=cursor-1" in calls[1]
    assert "reviewThreads" not in calls[1][3]


@pytest.mark.asyncio
async def test_discussion_rejects_incomplete_activity_pagination() -> None:
    async def runner(args, *, input_text=None):
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "timelineItems": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": True},
                            }
                        }
                    }
                }
            }
        )

    with pytest.raises(PullRequestGraphQLError, match="pagination cursor"):
        await fetch_pr_discussion(
            owner="owner", repo="repo", pr_number=123, runner=runner
        )


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
    assert "timelineItems(first: 100, after: $endCursor" in args[3]
    assert "... on IssueComment { databaseId createdAt }" in args[3]
    assert "... on PullRequestReview { databaseId createdAt }" in args[3]
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
    assert "timelineItems(first: 100, after: $endCursor" in calls[0][0][3]
    assert "owner=owner" in calls[0][0]
    assert "repo=repo" in calls[0][0]
    assert "number=123" in calls[0][0]
    assert calls[0][1] is None
