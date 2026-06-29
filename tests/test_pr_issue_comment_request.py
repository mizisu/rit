import json

from rit.services.pr_issue_comment_request import (
    create_issue_comment,
    create_issue_comment_request,
    parse_issue_comment_response,
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


def test_parse_issue_comment_response_returns_issue_comment_model() -> None:
    comment = parse_issue_comment_response(
        json.dumps(
            {
                "id": 90,
                "body": "ship it",
                "user": {"login": "alice"},
            }
        )
    )

    assert comment.id == 90
    assert comment.body == "ship it"
    assert comment.user is not None
    assert comment.user.login == "alice"


async def test_create_issue_comment_runs_request_and_parses_response() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"id": 90, "body": "ship it"})

    comment = await create_issue_comment(
        "owner/repo",
        123,
        body="ship it",
        runner=runner,
    )

    assert comment.id == 90
    assert comment.body == "ship it"
    assert calls == [
        (
            [
                "api",
                "--method",
                "POST",
                "/repos/owner/repo/issues/123/comments",
                "-f",
                "body=ship it",
            ],
            None,
        )
    ]
