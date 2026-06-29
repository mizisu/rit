import json

import pytest

from rit.services.github import (
    GitHubError,
    GitHubRepo,
    GitHubService,
    translate_pull_request_graphql_errors,
)
from rit.services.pr_graphql_queries import PullRequestGraphQLView, pull_request_query
from rit.services.pr_graphql_response import (
    PullRequestGraphQLError,
    PullRequestNotFound,
)


class CaptureGitHubService(GitHubService):
    def __init__(self, outputs: list[str | Exception] | None = None) -> None:
        super().__init__(owner="owner", repo="repo")
        self.calls: list[tuple[list[str], str | None]] = []
        self.repo_calls = 0
        self.outputs = outputs or []

    async def get_repo(self) -> GitHubRepo:
        self.repo_calls += 1
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
    assert "\n      body\n" in pull_request_query(PullRequestGraphQLView.SUMMARY)


def test_translate_pull_request_graphql_errors_wraps_graphql_errors() -> None:
    with pytest.raises(GitHubError, match=r"GraphQL error: \['boom'\]") as exc_info:
        with translate_pull_request_graphql_errors():
            raise PullRequestGraphQLError("['boom']")

    assert isinstance(exc_info.value.__cause__, PullRequestGraphQLError)


def test_translate_pull_request_graphql_errors_wraps_not_found_errors() -> None:
    with pytest.raises(GitHubError, match="PR #123 not found") as exc_info:
        with translate_pull_request_graphql_errors():
            raise PullRequestNotFound("PR #123 not found")

    assert isinstance(exc_info.value.__cause__, PullRequestNotFound)


@pytest.mark.asyncio
async def test_get_pr_discussion_fast_uses_graphql_review_threads() -> None:
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
    service = CaptureGitHubService(
        outputs=[
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "body": "PR body",
                                "reviews": {"nodes": reviews},
                                "reviewThreads": {
                                    "nodes": [
                                        {
                                            "id": "thread-node",
                                            "path": "app.py",
                                            "line": 12,
                                            "diffSide": "RIGHT",
                                            "comments": {
                                                "nodes": [
                                                    {
                                                        "databaseId": 300,
                                                        "body": "root",
                                                        "author": {
                                                            "login": "coderabbitai[bot]"
                                                        },
                                                        "path": "app.py",
                                                        "line": 12,
                                                        "pullRequestReview": {
                                                            "databaseId": 200
                                                        },
                                                    },
                                                    {
                                                        "databaseId": 301,
                                                        "body": "reply",
                                                        "author": {"login": "dave"},
                                                        "path": "app.py",
                                                        "line": 12,
                                                        "replyTo": {"databaseId": 300},
                                                        "pullRequestReview": {
                                                            "databaseId": 200
                                                        },
                                                    },
                                                ]
                                            },
                                        }
                                    ]
                                },
                                "comments": {"nodes": issue_comments},
                            }
                        }
                    }
                }
            ),
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
    assert thread.comments[0].user.login == "coderabbitai[bot]"
    assert [call[0][1] for call in service.calls] == ["graphql"]


@pytest.mark.asyncio
async def test_get_pr_files_fetches_remaining_pages_concurrently() -> None:
    first_page = [
        {"filename": f"file-{index}.py", "status": "modified", "patch": "@@ -1 +1 @@"}
        for index in range(100)
    ]
    second_page = [
        {"filename": "file-100.py", "status": "added", "patch": "@@ -0,0 +1 @@"}
    ]
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
async def test_get_pr_files_chunks_unknown_total_rest_pages() -> None:
    first_page = [
        {"filename": f"file-{index}.py", "status": "modified", "patch": "@@ -1 +1 @@"}
        for index in range(100)
    ]
    service = CaptureGitHubService(
        outputs=[json.dumps(first_page), *(json.dumps([]) for _ in range(29))]
    )

    files = await service.get_pr_files(123)

    assert len(files) == 100
    assert [call[0] for call in service.calls] == [
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=1"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=2"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=3"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=4"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=5"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=6"],
        ["api", "/repos/owner/repo/pulls/123/files?per_page=100&page=7"],
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
async def test_empty_participant_changes_skip_repo_lookup_and_gh_calls() -> None:
    service = CaptureGitHubService()

    await service.request_reviewers(123, reviewers=[], team_reviewers=None)
    await service.add_assignees(123, [])

    assert service.repo_calls == 0
    assert service.calls == []


@pytest.mark.asyncio
async def test_create_review_comment_posts_submitted_review_graphql_payload() -> None:
    service = CaptureGitHubService(
        outputs=[
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_node",
                                "reviews": {"nodes": []},
                            }
                        }
                    }
                }
            ),
            json.dumps(
                {
                    "data": {
                        "addPullRequestReview": {
                            "pullRequestReview": {
                                "nodeId": "review_node",
                                "databaseId": 80,
                                "state": "COMMENTED",
                                "body": "",
                                "comments": {"nodes": []},
                            }
                        }
                    }
                }
            ),
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "nodes": [
                                        {
                                            "id": "thread-node",
                                            "path": "app.py",
                                            "line": 42,
                                            "diffSide": "RIGHT",
                                            "comments": {
                                                "nodes": [
                                                    {
                                                        "databaseId": 300,
                                                        "pullRequestReview": {
                                                            "databaseId": 80
                                                        },
                                                        "body": "ship it",
                                                        "path": "app.py",
                                                        "line": 42,
                                                        "author": {"login": "alice"},
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
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
    assert comment.side == "RIGHT"
    assert len(service.calls) == 3
    assert all(call[0] == ["api", "graphql", "--input", "-"] for call in service.calls)
    assert service.calls[1][1] is not None
    mutation_input = json.loads(service.calls[1][1])["variables"]["input"]
    assert mutation_input == {
        "pullRequestId": "PR_node",
        "event": "COMMENT",
        "commitOID": "deadbeef",
        "threads": [
            {
                "path": "app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ],
    }


@pytest.mark.asyncio
async def test_list_review_comments_reads_graphql_threads() -> None:
    service = CaptureGitHubService(
        outputs=[
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "reviewThreads": {
                                    "nodes": [
                                        {
                                            "id": "thread-one",
                                            "path": "app.py",
                                            "line": 42,
                                            "diffSide": "RIGHT",
                                            "comments": {
                                                "nodes": [
                                                    {
                                                        "databaseId": 300,
                                                        "pullRequestReview": {
                                                            "databaseId": 80
                                                        },
                                                        "body": "first",
                                                        "path": "app.py",
                                                        "line": 42,
                                                    }
                                                ]
                                            },
                                        },
                                        {
                                            "id": "thread-two",
                                            "path": "app.py",
                                            "line": 43,
                                            "diffSide": "RIGHT",
                                            "comments": {
                                                "nodes": [
                                                    {
                                                        "databaseId": 301,
                                                        "pullRequestReview": {
                                                            "databaseId": 80
                                                        },
                                                        "body": "second",
                                                        "path": "app.py",
                                                        "line": 43,
                                                    }
                                                ]
                                            },
                                        },
                                    ]
                                }
                            }
                        }
                    }
                }
            )
        ]
    )

    comments = await service.list_review_comments(123, 80)

    assert [comment.id for comment in comments] == [300, 301]
    assert [comment.side for comment in comments] == ["RIGHT", "RIGHT"]
    assert service.calls[0][0] == ["api", "graphql", "--input", "-"]


@pytest.mark.asyncio
async def test_team_reviewer_candidates_treat_repo_teams_404_as_empty() -> None:
    service = CaptureGitHubService(outputs=[GitHubError("gh: Not Found (HTTP 404)")])

    teams = await service.get_reviewer_team_candidates()

    assert teams == []
    assert service.calls[0][0][1] == "/repos/owner/repo/teams?per_page=100"


@pytest.mark.asyncio
async def test_get_pr_file_view_states_paginates_graphql_pages() -> None:
    service = CaptureGitHubService(
        outputs=[
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "files": {
                                    "nodes": [
                                        {
                                            "path": "src/app.py",
                                            "viewerViewedState": "VIEWED",
                                        }
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": True,
                                        "endCursor": "cursor-2",
                                    },
                                }
                            }
                        }
                    }
                }
            ),
            json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "files": {
                                    "nodes": [
                                        {
                                            "path": "src/lib.py",
                                            "viewerViewedState": "UNVIEWED",
                                        }
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                }
                            }
                        }
                    }
                }
            ),
        ]
    )

    states = await service.get_pr_file_view_states(123)

    assert states == {"src/app.py": "VIEWED", "src/lib.py": "UNVIEWED"}
    assert not any("after=cursor-2" in arg for arg in service.calls[0][0])
    assert any("after=cursor-2" in arg for arg in service.calls[1][0])


@pytest.mark.asyncio
async def test_get_pr_file_view_states_wraps_graphql_errors() -> None:
    service = CaptureGitHubService(
        outputs=[json.dumps({"errors": [{"message": "viewer state failed"}]})]
    )

    with pytest.raises(GitHubError) as exc_info:
        await service.get_pr_file_view_states(123)

    assert "viewer state failed" in str(exc_info.value)
