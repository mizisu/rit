from __future__ import annotations

import json

from rit.services.gh_request import GitHubInputRunner, run_request
from rit.state.models import PRIssueComment

__all__ = (
    "create_issue_comment",
    "create_issue_comment_request",
    "parse_issue_comment_response",
)


def create_issue_comment_request(
    repo_full_name: str,
    pr_number: int,
    *,
    body: str,
) -> tuple[str, ...]:
    """Build a gh api request for creating a PR-level issue comment."""
    return (
        "api",
        "--method",
        "POST",
        f"/repos/{repo_full_name}/issues/{pr_number}/comments",
        "-f",
        f"body={body}",
    )


def parse_issue_comment_response(result: str) -> PRIssueComment:
    """Parse a PR-level issue comment response."""
    return PRIssueComment.model_validate(json.loads(result))


async def create_issue_comment(
    repo_full_name: str,
    pr_number: int,
    *,
    body: str,
    runner: GitHubInputRunner,
) -> PRIssueComment:
    """Create a PR-level issue comment via the REST API."""
    return parse_issue_comment_response(
        await run_request(
            create_issue_comment_request(repo_full_name, pr_number, body=body),
            runner,
        )
    )
