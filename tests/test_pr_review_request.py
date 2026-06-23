import json

from rit.services.pr_review_request import (
    create_pending_review,
    create_issue_comment,
    create_issue_comment_request,
    create_review_comment,
    create_pending_review_request,
    delete_pending_review,
    delete_pending_review_request,
    list_review_comments,
    list_review_comments_request,
    parse_issue_comment_response,
    parse_review_comments_response,
    parse_review_response,
    pull_request_review_comments_request,
    run_review_request,
    submit_pending_review,
    submit_pending_review_request,
    submit_review,
    submit_review_request,
)
from rit.state.models import PendingReviewComment


def _pending_comment() -> PendingReviewComment:
    return PendingReviewComment(
        path="src/app.py",
        line=42,
        side="RIGHT",
        body="ship it",
    )


def test_create_pending_review_request_posts_payload_to_reviews_endpoint() -> None:
    request = create_pending_review_request(
        "owner/repo",
        123,
        payload={"event": "COMMENT"},
    )

    assert request.args == (
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews",
        "--input",
        "-",
    )
    assert json.loads(request.input_text) == {"event": "COMMENT"}


def test_submit_review_request_posts_payload_to_reviews_endpoint() -> None:
    request = submit_review_request(
        "owner/repo",
        123,
        payload={"event": "APPROVE", "body": "nice"},
    )

    assert request.args == (
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews",
        "--input",
        "-",
    )
    assert json.loads(request.input_text) == {"event": "APPROVE", "body": "nice"}


def test_submit_pending_review_request_posts_event_payload() -> None:
    request = submit_pending_review_request(
        "owner/repo",
        123,
        review_id=80,
        payload={"event": "COMMENT"},
    )

    assert request.args == (
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews/80/events",
        "--input",
        "-",
    )
    assert json.loads(request.input_text) == {"event": "COMMENT"}


def test_delete_pending_review_request_targets_review() -> None:
    assert delete_pending_review_request("owner/repo", 123, review_id=80) == (
        "api",
        "--method",
        "DELETE",
        "/repos/owner/repo/pulls/123/reviews/80",
    )


async def test_delete_pending_review_runs_delete_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return ""

    await delete_pending_review(
        "owner/repo",
        123,
        review_id=80,
        runner=runner,
    )

    assert calls == [
        (
            [
                "api",
                "--method",
                "DELETE",
                "/repos/owner/repo/pulls/123/reviews/80",
            ],
            None,
        )
    ]


def test_review_comment_requests_target_review_comment_endpoints() -> None:
    assert pull_request_review_comments_request("owner/repo", 123) == (
        "api",
        "/repos/owner/repo/pulls/123/comments?per_page=100",
    )
    assert list_review_comments_request("owner/repo", 123, review_id=80) == (
        "api",
        "/repos/owner/repo/pulls/123/reviews/80/comments",
        "--paginate",
    )


def test_create_issue_comment_request_uses_form_body_field() -> None:
    assert create_issue_comment_request("owner/repo", 123, body="ship it") == (
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/issues/123/comments",
        "-f",
        "body=ship it",
    )


def test_parse_review_response_returns_pr_review_model() -> None:
    review = parse_review_response(
        json.dumps({"id": 80, "state": "COMMENTED", "body": "LGTM"})
    )

    assert review.id == 80
    assert review.state.value == "COMMENTED"
    assert review.body == "LGTM"


async def test_run_review_request_sends_input_request_and_parses_review() -> None:
    calls: list[tuple[list[str], str | None]] = []
    request = create_pending_review_request(
        "owner/repo",
        123,
        payload={"event": "COMMENT"},
    )

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"id": 80, "state": "COMMENTED", "body": "LGTM"})

    review = await run_review_request(request, runner)

    assert review.id == 80
    assert review.state.value == "COMMENTED"
    assert review.body == "LGTM"
    assert calls == [(list(request.args), request.input_text)]


async def test_create_pending_review_runs_payload_and_preserves_requested_body() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"id": 80, "state": "PENDING", "body": ""})

    review = await create_pending_review(
        "owner/repo",
        123,
        comments=[_pending_comment()],
        body="summary",
        commit_id="deadbeef",
        runner=runner,
    )

    assert review.id == 80
    assert review.state.value == "PENDING"
    assert review.body == "summary"
    assert calls[0][0] == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {
        "comments": [
            {
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ],
        "body": "summary",
        "commit_id": "deadbeef",
    }


async def test_submit_pending_review_runs_event_payload() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"id": 80, "state": "COMMENTED", "body": "done"})

    review = await submit_pending_review(
        "owner/repo",
        123,
        review_id=80,
        event="COMMENT",
        body="done",
        runner=runner,
    )

    assert review.id == 80
    assert review.state.value == "COMMENTED"
    assert calls[0][0] == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews/80/events",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {"event": "COMMENT", "body": "done"}


async def test_submit_review_runs_review_payload() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"id": 81, "state": "COMMENTED", "body": ""})

    review = await submit_review(
        "owner/repo",
        123,
        event="COMMENT",
        comments=[_pending_comment()],
        commit_id="deadbeef",
        runner=runner,
    )

    assert review.id == 81
    assert calls[0][0] == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {
        "event": "COMMENT",
        "commit_id": "deadbeef",
        "comments": [
            {
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ],
    }


async def test_create_review_comment_submits_review_and_selects_created_comment() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        if len(calls) == 1:
            return json.dumps({"id": 80, "state": "COMMENTED", "body": ""})
        return json.dumps(
            [
                {
                    "id": 300,
                    "pull_request_review_id": 80,
                    "body": "ship it",
                    "path": "src/app.py",
                    "line": 42,
                    "side": "RIGHT",
                    "user": {"login": "alice"},
                }
            ]
        )

    comment = await create_review_comment(
        "owner/repo",
        123,
        body="ship it",
        commit_id="deadbeef",
        path="src/app.py",
        line=42,
        side="RIGHT",
        runner=runner,
    )

    assert comment.id == 300
    assert comment.pull_request_review_id == 80
    assert calls[0][0] == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {
        "event": "COMMENT",
        "commit_id": "deadbeef",
        "comments": [
            {
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ],
    }
    assert calls[1] == (
        [
            "api",
            "/repos/owner/repo/pulls/123/reviews/80/comments",
            "--paginate",
        ],
        None,
    )


def test_parse_issue_comment_response_returns_issue_comment_model() -> None:
    comment = parse_issue_comment_response(
        json.dumps({"id": 100, "body": "issue note", "user": {"login": "alice"}})
    )

    assert comment.id == 100
    assert comment.body == "issue note"
    assert comment.user is not None
    assert comment.user.login == "alice"


async def test_create_issue_comment_runs_request_and_parses_response() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {"id": 100, "body": "issue note", "user": {"login": "alice"}}
        )

    comment = await create_issue_comment(
        "owner/repo",
        123,
        body="issue note",
        runner=runner,
    )

    assert comment.id == 100
    assert comment.body == "issue note"
    assert calls == [
        (
            [
                "api",
                "--method",
                "POST",
                "/repos/owner/repo/issues/123/comments",
                "-f",
                "body=issue note",
            ],
            None,
        )
    ]


def test_parse_review_comments_response_flattens_paginated_json() -> None:
    comments = parse_review_comments_response(
        json.dumps(
            [
                {
                    "id": 300,
                    "body": "first",
                    "path": "a.py",
                    "line": 1,
                    "side": "RIGHT",
                }
            ]
        )
        + json.dumps(
            [
                {
                    "id": 301,
                    "body": "second",
                    "path": "b.py",
                    "line": 2,
                    "side": "RIGHT",
                }
            ]
        )
    )

    assert [comment.id for comment in comments] == [300, 301]


async def test_list_review_comments_runs_request_and_parses_paginated_response() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            [
                {
                    "id": 300,
                    "body": "first",
                    "path": "a.py",
                    "line": 1,
                    "side": "RIGHT",
                }
            ]
        ) + json.dumps(
            [
                {
                    "id": 301,
                    "body": "second",
                    "path": "b.py",
                    "line": 2,
                    "side": "RIGHT",
                }
            ]
        )

    comments = await list_review_comments(
        "owner/repo",
        123,
        review_id=80,
        runner=runner,
    )

    assert [comment.id for comment in comments] == [300, 301]
    assert calls == [
        (
            [
                "api",
                "/repos/owner/repo/pulls/123/reviews/80/comments",
                "--paginate",
            ],
            None,
        )
    ]
