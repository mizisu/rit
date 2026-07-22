import json

from rit.services.pr_issue_comment_request import create_issue_comment


async def test_create_issue_comment_queries_subject_then_adds_graphql_comment() -> None:
    calls: list[dict[str, object]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if "addComment" not in payload["query"]:
            return json.dumps(
                {
                    "data": {
                        "repository": {"pullRequest": {"id": "PR_node"}}
                    }
                }
            )
        return json.dumps(
            {
                "data": {
                    "addComment": {
                        "commentEdge": {
                            "node": {
                                "databaseId": 90,
                                "body": "ship it",
                                "author": {"login": "alice"},
                            }
                        }
                    }
                }
            }
        )

    comment = await create_issue_comment(
        "owner",
        "repo",
        123,
        body="ship it",
        runner=runner,
    )

    assert comment.id == 90
    assert comment.user is not None
    assert comment.user.login == "alice"
    assert calls[1]["variables"] == {
        "input": {"subjectId": "PR_node", "body": "ship it"}
    }
