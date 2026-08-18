import json

from rit.services.pr_file_comment_request import (
    create_file_comment,
    create_file_comment_request,
    parse_file_comment_response,
)


def test_create_file_comment_request_targets_entire_file() -> None:
    request = create_file_comment_request(
        "owner/repo",
        123,
        body="check this file",
        commit_id="deadbeef",
        path="src/app.py",
    )

    assert request.args == (
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/comments",
        "--input",
        "-",
    )
    assert json.loads(request.input_text) == {
        "body": "check this file",
        "commit_id": "deadbeef",
        "path": "src/app.py",
        "subject_type": "file",
    }


def test_parse_file_comment_response_preserves_subject_type() -> None:
    comment = parse_file_comment_response(
        json.dumps(
            {
                "id": 90,
                "body": "check this file",
                "path": "src/app.py",
                "line": 1,
                "original_line": 1,
                "side": "RIGHT",
                "position": 1,
                "original_position": 1,
            }
        )
    )

    assert comment.id == 90
    assert comment.path == "src/app.py"
    assert comment.line is None
    assert comment.original_line is None
    assert comment.side == ""
    assert comment.position is None
    assert comment.original_position is None
    assert comment.subject_type == "file"


async def test_create_file_comment_runs_one_rest_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "id": 90,
                "body": "check this file",
                "path": "src/app.py",
                "subject_type": "file",
            }
        )

    comment = await create_file_comment(
        "owner/repo",
        123,
        body="check this file",
        commit_id="deadbeef",
        path="src/app.py",
        runner=runner,
    )

    assert comment.subject_type == "file"
    assert len(calls) == 1
    assert calls[0][0] == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/comments",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {
        "body": "check this file",
        "commit_id": "deadbeef",
        "path": "src/app.py",
        "subject_type": "file",
    }
