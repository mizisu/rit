import json

import pytest

import rit.services.pr_discussion as pr_discussion
from rit.services.pr_discussion import (
    discussion_from_pr,
    fast_discussion_from_data,
    fast_discussion_from_result,
    fast_discussion_from_results,
    fetch_pr_discussion,
    fetch_pr_discussion_fast,
)
from rit.state.models import PR, ReviewState, ReviewThread


def test_discussion_from_pr_projects_full_graphql_discussion() -> None:
    pr = PR(
        body="PR body",
        reviews={
            "nodes": [
                {"databaseId": 200, "body": "review body", "state": "COMMENTED"}
            ]
        },
        comments={"nodes": [{"databaseId": 100, "body": "issue comment"}]},
        reviewThreads={
            "nodes": [
                {
                    "id": "thread-300",
                    "path": "app.py",
                    "line": 12,
                    "comments": {
                        "nodes": [
                            {
                                "databaseId": 300,
                                "body": "thread comment",
                                "path": "app.py",
                                "line": 12,
                                "side": "RIGHT",
                            }
                        ]
                    },
                }
            ]
        },
    )

    discussion = discussion_from_pr(pr)

    assert discussion.body == "PR body"
    assert [(review.id, review.body, review.state) for review in discussion.reviews] == [
        (200, "review body", ReviewState.COMMENTED)
    ]
    assert [(comment.id, comment.body) for comment in discussion.issue_comments] == [
        (100, "issue comment")
    ]
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300


def test_fast_discussion_from_data_builds_threads_from_rest_comments() -> None:
    discussion = fast_discussion_from_data(
        {
            "body": "PR body",
            "reviews": {
                "nodes": [
                    {"databaseId": 200, "body": "review body", "state": "COMMENTED"}
                ]
            },
            "comments": {"nodes": [{"databaseId": 100, "body": "issue comment"}]},
        },
        [
            {
                "id": 300,
                "body": "root",
                "user": {"login": "coderabbitai[bot]"},
                "path": "app.py",
                "line": 12,
                "side": "RIGHT",
                "pull_request_review_id": 200,
            },
            {
                "id": 301,
                "body": "reply",
                "path": "app.py",
                "line": 12,
                "side": "RIGHT",
                "in_reply_to_id": 300,
                "pull_request_review_id": 200,
            },
        ],
    )

    assert discussion.body == "PR body"
    assert [(review.id, review.body, review.state) for review in discussion.reviews] == [
        (200, "review body", ReviewState.COMMENTED)
    ]
    assert [(comment.id, comment.body) for comment in discussion.issue_comments] == [
        (100, "issue comment")
    ]
    assert len(discussion.review_threads) == 1
    thread: ReviewThread = discussion.review_threads[0]
    assert thread.path == "app.py"
    assert [comment.id for comment in thread.comments] == [300, 301]
    assert thread.comments[0].user is not None
    assert thread.comments[0].user.login == "coderabbitai"


def test_fast_discussion_from_data_empty_rest_comments_skips_model_adapter(
    monkeypatch,
) -> None:
    class Adapter:
        def validate_python(self, _data: object) -> list[object]:
            raise AssertionError("empty REST review comments should skip validation")

    monkeypatch.setattr(pr_discussion, "_PRCommentListAdapter", Adapter())

    discussion = fast_discussion_from_data(
        {
            "body": "PR body",
            "reviews": {"nodes": []},
            "comments": {"nodes": []},
        },
        [],
    )

    assert discussion.body == "PR body"
    assert discussion.reviews == []
    assert discussion.issue_comments == []
    assert discussion.review_threads == []


def test_fast_discussion_from_data_single_rest_comment_skips_model_adapter(
    monkeypatch,
) -> None:
    class Adapter:
        def validate_python(self, _data: object) -> list[object]:
            raise AssertionError("single REST review comment should skip list validation")

    monkeypatch.setattr(pr_discussion, "_PRCommentListAdapter", Adapter())

    discussion = fast_discussion_from_data(
        {
            "body": "PR body",
            "reviews": {"nodes": []},
            "comments": {"nodes": []},
        },
        [
            {
                "id": 300,
                "body": "root",
                "path": "app.py",
                "line": 12,
                "side": "RIGHT",
            }
        ],
    )

    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300


def test_fast_discussion_from_result_empty_rest_comments_skips_json_decode(
    monkeypatch,
) -> None:
    def loads(_result: str) -> object:
        raise AssertionError("empty REST review comments should skip JSON decode")

    monkeypatch.setattr(pr_discussion.json, "loads", loads)

    discussion = fast_discussion_from_result(
        {
            "body": "PR body",
            "reviews": {"nodes": []},
            "comments": {"nodes": []},
        },
        "[]",
    )

    assert discussion.review_threads == []


def test_fast_discussion_from_result_decodes_rest_review_comments() -> None:
    discussion = fast_discussion_from_result(
        {
            "body": "PR body",
            "reviews": {
                "nodes": [
                    {"databaseId": 200, "body": "review body", "state": "COMMENTED"}
                ]
            },
            "comments": {"nodes": [{"databaseId": 100, "body": "issue comment"}]},
        },
        json.dumps(
            [
                {
                    "id": 300,
                    "body": "root",
                    "path": "app.py",
                    "line": 12,
                    "side": "RIGHT",
                    "pull_request_review_id": 200,
                }
            ]
        ),
    )

    assert discussion.body == "PR body"
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300


def test_fast_discussion_from_results_decodes_graphql_pr_and_rest_comments() -> None:
    discussion = fast_discussion_from_results(
        json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
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
                            "comments": {
                                "nodes": [
                                    {"databaseId": 100, "body": "issue comment"}
                                ]
                            },
                        }
                    }
                }
            }
        ),
        json.dumps(
            [
                {
                    "id": 300,
                    "body": "root",
                    "path": "app.py",
                    "line": 12,
                    "side": "RIGHT",
                    "pull_request_review_id": 200,
                }
            ]
        ),
        pr_number=123,
    )

    assert discussion.body == "PR body"
    assert [(review.id, review.body, review.state) for review in discussion.reviews] == [
        (200, "review body", ReviewState.COMMENTED)
    ]
    assert [(comment.id, comment.body) for comment in discussion.issue_comments] == [
        (100, "issue comment")
    ]
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300


@pytest.mark.asyncio
async def test_fetch_pr_discussion_runs_full_graphql_discussion_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
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
                            "comments": {
                                "nodes": [
                                    {"databaseId": 100, "body": "issue comment"}
                                ]
                            },
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "id": "thread-300",
                                        "path": "app.py",
                                        "line": 12,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "databaseId": 300,
                                                    "body": "thread comment",
                                                    "path": "app.py",
                                                    "line": 12,
                                                    "side": "RIGHT",
                                                }
                                            ]
                                        },
                                    }
                                ]
                            },
                        }
                    }
                }
            }
        )

    discussion = await fetch_pr_discussion(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert discussion.body == "PR body"
    assert [(review.id, review.body, review.state) for review in discussion.reviews] == [
        (200, "review body", ReviewState.COMMENTED)
    ]
    assert [(comment.id, comment.body) for comment in discussion.issue_comments] == [
        (100, "issue comment")
    ]
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300
    assert len(calls) == 1
    args, input_text = calls[0]
    assert args[:2] == ["api", "graphql"]
    assert "owner=owner" in args
    assert "repo=repo" in args
    assert "number=123" in args
    assert input_text is None


@pytest.mark.asyncio
async def test_fetch_pr_discussion_fast_runs_graphql_and_rest_requests() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        if args[:2] == ["api", "graphql"]:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
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
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 100,
                                            "body": "issue comment",
                                        }
                                    ]
                                },
                            }
                        }
                    }
                }
            )
        return json.dumps(
            [
                {
                    "id": 300,
                    "body": "root",
                    "path": "app.py",
                    "line": 12,
                    "side": "RIGHT",
                    "pull_request_review_id": 200,
                }
            ]
        )

    discussion = await fetch_pr_discussion_fast(
        owner="owner",
        repo="repo",
        pr_number=123,
        runner=runner,
    )

    assert discussion.body == "PR body"
    assert [(review.id, review.body, review.state) for review in discussion.reviews] == [
        (200, "review body", ReviewState.COMMENTED)
    ]
    assert [(comment.id, comment.body) for comment in discussion.issue_comments] == [
        (100, "issue comment")
    ]
    assert len(discussion.review_threads) == 1
    assert discussion.review_threads[0].root_comment_id == 300
    assert len(calls) == 2
    assert any(
        call[0][:2] == ["api", "graphql"]
        and "owner=owner" in call[0]
        and "repo=repo" in call[0]
        and "number=123" in call[0]
        for call in calls
    )
    assert any(
        call[0] == [
            "api",
            "/repos/owner/repo/pulls/123/comments?per_page=100",
        ]
        for call in calls
    )
    assert all(input_text is None for _, input_text in calls)
