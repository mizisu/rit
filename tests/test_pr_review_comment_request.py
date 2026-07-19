import json

from rit.services.pr_review_comment_request import (
    parse_review_comment_response,
    update_review_comment,
    update_review_comment_request,
)


def test_update_review_comment_request_targets_existing_comment() -> None:
    request = update_review_comment_request(
        "owner/repo",
        90,
        body="updated body",
    )

    assert request.args == (
        "api",
        "--method",
        "PATCH",
        "/repos/owner/repo/pulls/comments/90",
        "--input",
        "-",
    )
    assert json.loads(request.input_text) == {"body": "updated body"}


def test_parse_review_comment_response_preserves_comment_identity() -> None:
    comment = parse_review_comment_response(
        json.dumps({"id": 90, "body": "updated body", "path": "src/app.py"})
    )

    assert comment.id == 90
    assert comment.body == "updated body"
    assert comment.path == "src/app.py"


async def test_update_review_comment_runs_request_and_parses_response() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"id": 90, "body": "updated body"})

    comment = await update_review_comment(
        "owner/repo",
        90,
        body="updated body",
        runner=runner,
    )

    assert comment.id == 90
    assert comment.body == "updated body"
    assert calls[0][0] == [
        "api",
        "--method",
        "PATCH",
        "/repos/owner/repo/pulls/comments/90",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {"body": "updated body"}
