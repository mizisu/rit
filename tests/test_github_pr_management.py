import json

import pytest

from rit.services import github
from rit.services.github import GitHubError, GitHubRepo, GitHubService


class CaptureGitHubService(GitHubService):
    def __init__(self, outputs: list[str | Exception] | None = None) -> None:
        super().__init__(owner="owner", repo="repo")
        self.calls: list[tuple[list[str], str | None]] = []
        self.outputs = outputs or []

    async def get_repo(self) -> GitHubRepo:
        return GitHubRepo(owner="owner", name="repo")

    async def _run_gh(self, args: list[str], *, input_text: str | None = None) -> str:
        self.calls.append((args, input_text))
        if self.outputs:
            output = self.outputs.pop(0)
            if isinstance(output, Exception):
                raise output
            return output
        return "{}"


def test_pr_summary_query_fetches_body_for_early_description() -> None:
    assert "\n      body\n" in github._PR_SUMMARY_GRAPHQL_QUERY


@pytest.mark.asyncio
async def test_get_pr_discussion_fast_builds_threads_from_rest_comments() -> None:
    issue_comments = [
        {
            "id": 100,
            "body": "issue comment",
            "user": {"login": "alice"},
            "created_at": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
        }
    ]
    reviews = [
        {
            "id": 200,
            "body": "review body",
            "state": "COMMENTED",
            "user": {"login": "bob"},
            "submitted_at": "2026-06-01T00:01:00Z",
        }
    ]
    review_comments = [
        {
            "id": 300,
            "body": "root",
            "user": {"login": "coderabbitai[bot]"},
            "path": "app.py",
            "line": 12,
            "side": "RIGHT",
            "pull_request_review_id": 200,
            "created_at": "2026-06-01T00:02:00Z",
            "updated_at": "2026-06-01T00:02:00Z",
        },
        {
            "id": 301,
            "body": "reply",
            "user": {"login": "dave"},
            "path": "app.py",
            "line": 12,
            "side": "RIGHT",
            "in_reply_to_id": 300,
            "pull_request_review_id": 200,
            "created_at": "2026-06-01T00:03:00Z",
            "updated_at": "2026-06-01T00:03:00Z",
        },
    ]
    service = CaptureGitHubService(
        outputs=[
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "body": "PR body",
                                "reviews": {"nodes": reviews},
                                "comments": {"nodes": issue_comments},
                            }
                        }
                    }
                }
            ),
            json.dumps(review_comments),
        ]
    )

    discussion = await service.get_pr_discussion_fast(123)

    assert discussion.body == "PR body"
    assert discussion.issue_comments[0].id == 100
    assert discussion.reviews[0].id == 200
    assert len(discussion.review_threads) == 1
    thread = discussion.review_threads[0]
    assert thread.path == "app.py"
    assert thread.root_comment_id == 300
    assert [comment.id for comment in thread.comments] == [300, 301]
    assert thread.comments[0].user is not None
    assert thread.comments[0].user.login == "coderabbitai"
    assert [call[0][1] for call in service.calls] == [
        "graphql",
        "/repos/owner/repo/pulls/123/comments?per_page=100",
    ]


@pytest.mark.asyncio
async def test_get_pr_files_fetches_remaining_pages_concurrently() -> None:
    first_page = [
        {"filename": f"file-{index}.py", "status": "modified", "patch": "@@ -1 +1 @@"}
        for index in range(100)
    ]
    second_page = [{"filename": "file-100.py", "status": "added", "patch": "@@ -0,0 +1 @@"}]
    service = CaptureGitHubService(
        outputs=[json.dumps(first_page), json.dumps(second_page)]
    )

    files = await service.get_pr_files(123, total_count=101)

    assert len(files) == 101
    assert files[0].filename == "file-0.py"
    assert files[-1].filename == "file-100.py"
    assert [call[0] for call in service.calls] == [
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=1"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=2"],
    ]


@pytest.mark.asyncio
async def test_request_reviewers_posts_user_and_team_payload() -> None:
    service = CaptureGitHubService()

    await service.request_reviewers(
        123,
        reviewers=["alice"],
        team_reviewers=["backend"],
    )

    args, input_text = service.calls[0]
    assert args[:3] == ["api", "--method", "POST"]
    assert args[3] == "/repos/owner/repo/pulls/123/requested_reviewers"
    assert input_text is not None
    assert json.loads(input_text) == {
        "reviewers": ["alice"],
        "team_reviewers": ["backend"],
    }


@pytest.mark.asyncio
async def test_remove_assignees_uses_issue_assignee_endpoint() -> None:
    service = CaptureGitHubService()

    await service.remove_assignees(123, ["alice"])

    args, input_text = service.calls[0]
    assert args[:3] == ["api", "--method", "DELETE"]
    assert args[3] == "/repos/owner/repo/issues/123/assignees"
    assert input_text is not None
    assert json.loads(input_text) == {"assignees": ["alice"]}


@pytest.mark.asyncio
async def test_create_review_comment_posts_submitted_review_payload() -> None:
    service = CaptureGitHubService(
        outputs=[
            json.dumps({"id": 80, "state": "COMMENTED", "body": ""}),
            json.dumps(
                [
                    {
                        "id": 300,
                        "pull_request_review_id": 80,
                        "body": "ship it",
                        "path": "app.py",
                        "line": 42,
                        "side": "RIGHT",
                        "user": {"login": "alice"},
                    }
                ]
            ),
        ]
    )

    comment = await service.create_review_comment(
        123,
        body="ship it",
        commit_id="deadbeef",
        path="app.py",
        line=42,
        side="RIGHT",
    )

    assert comment.id == 300
    args, input_text = service.calls[0]
    assert args == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/reviews",
        "--input",
        "-",
    ]
    assert input_text is not None
    assert json.loads(input_text) == {
        "event": "COMMENT",
        "commit_id": "deadbeef",
        "comments": [
            {
                "path": "app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ],
    }
    assert service.calls[1][0] == [
        "api",
        "/repos/owner/repo/pulls/123/reviews/80/comments",
        "--paginate",
    ]


@pytest.mark.asyncio
async def test_team_reviewer_candidates_treat_repo_teams_404_as_empty() -> None:
    service = CaptureGitHubService(outputs=[GitHubError("gh: Not Found (HTTP 404)")])

    teams = await service.get_reviewer_team_candidates()

    assert teams == []
    assert service.calls[0][0][1] == "/repos/owner/repo/teams?per_page=100"
