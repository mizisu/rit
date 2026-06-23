from __future__ import annotations

import json
from dataclasses import dataclass

from rit.services.gh_request import GitHubInputRunner, run_request

__all__ = (
    "GitHubRepo",
    "fetch_repo_view",
    "parse_repo_view_response",
    "repo_view_request",
)


@dataclass
class GitHubRepo:
    """GitHub repository identity."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def repo_view_request() -> tuple[str, ...]:
    """Build gh args for detecting the current repository."""
    return ("repo", "view", "--json", "owner,name")


def parse_repo_view_response(result: str) -> GitHubRepo:
    """Parse gh repo view JSON into a repository identity."""
    data = json.loads(result)
    return GitHubRepo(
        owner=data["owner"]["login"],
        name=data["name"],
    )


async def fetch_repo_view(runner: GitHubInputRunner) -> GitHubRepo:
    """Fetch the current repository identity via gh."""
    return parse_repo_view_response(await run_request(repo_view_request(), runner))
