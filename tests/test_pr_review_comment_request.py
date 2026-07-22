import json

from rit.services.pr_review_comment_request import (
    create_review_comment,
    create_review_comment_request,
    parse_created_review_comment_response,
    parse_review_comment_response,
    update_review_comment,
    update_review_comment_request,
)


def test_create_review_comment_request_targets_diff_line() -> None:
    request = create_review_comment_request(
        "owner/repo",
        123,
        body="ship it",
        commit_id="deadbeef",
        path="src/app.py",
        line=42,
        side="RIGHT",
        start_line=40,
        start_side="RIGHT",
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
        "body": "ship it",
        "commit_id": "deadbeef",
        "path": "src/app.py",
        "line": 42,
        "side": "RIGHT",
        "start_line": 40,
        "start_side": "RIGHT",
    }


def test_parse_created_review_comment_response_preserves_rest_identity() -> None:
    comment = parse_created_review_comment_response(
        json.dumps(
            {
                "id": 90,
                "node_id": "PRRC_node_90",
                "body": "ship it",
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
            }
        )
    )

    assert comment.id == 90
    assert comment.node_id == "PRRC_node_90"
    assert comment.line == 42
    assert comment.side == "RIGHT"


async def test_create_review_comment_runs_one_rest_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(
            {
                "id": 90,
                "body": "ship it",
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
            }
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

    assert comment.id == 90
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
        "body": "ship it",
        "commit_id": "deadbeef",
        "path": "src/app.py",
        "line": 42,
        "side": "RIGHT",
    }


def _response() -> dict[str, object]:
    return {
        "data": {
            "updatePullRequestReviewComment": {
                "pullRequestReviewComment": {
                    "nodeId": "PRRC_node_90",
                    "databaseId": 90,
                    "body": "updated body",
                    "path": "src/app.py",
                }
            }
        }
    }


def test_update_review_comment_request_uses_graphql_node_id() -> None:
    request = update_review_comment_request("PRRC_node_90", body="updated body")

    assert request.args == ("api", "graphql", "--input", "-")
    payload = json.loads(request.input_text)
    assert payload["variables"] == {
        "input": {
            "pullRequestReviewCommentId": "PRRC_node_90",
            "body": "updated body",
        }
    }
    assert "updatePullRequestReviewComment" in payload["query"]


def test_parse_review_comment_response_preserves_graphql_identity() -> None:
    comment = parse_review_comment_response(json.dumps(_response()))

    assert comment.id == 90
    assert comment.node_id == "PRRC_node_90"
    assert comment.body == "updated body"
    assert comment.path == "src/app.py"


async def test_update_review_comment_runs_graphql_mutation() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps(_response())

    comment = await update_review_comment(
        "PRRC_node_90",
        body="updated body",
        runner=runner,
    )

    assert comment.id == 90
    assert calls[0][0] == ["api", "graphql", "--input", "-"]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1])["variables"]["input"][
        "pullRequestReviewCommentId"
    ] == "PRRC_node_90"
