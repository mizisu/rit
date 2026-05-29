import json

import pytest

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
async def test_team_reviewer_candidates_treat_repo_teams_404_as_empty() -> None:
    service = CaptureGitHubService(outputs=[GitHubError("gh: Not Found (HTTP 404)")])

    teams = await service.get_reviewer_team_candidates()

    assert teams == []
    assert service.calls[0][0][1] == "/repos/owner/repo/teams?per_page=100"
