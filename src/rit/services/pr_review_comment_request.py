from __future__ import annotations

import json

from rit.services.gh_request import (
    GitHubInputRequest,
    GitHubInputRunner,
    run_input_request,
)
from rit.state.models import PRComment

__all__ = (
    "parse_review_comment_response",
    "update_review_comment",
    "update_review_comment_request",
)


def update_review_comment_request(
    repo_full_name: str,
    comment_id: int,
    *,
    body: str,
) -> GitHubInputRequest:
    """Build a REST request for updating a PR review comment."""
    return GitHubInputRequest(
        args=(
            "api",
            "--method",
            "PATCH",
            f"/repos/{repo_full_name}/pulls/comments/{comment_id}",
            "--input",
            "-",
        ),
        input_text=json.dumps({"body": body}),
    )


def parse_review_comment_response(result: str) -> PRComment:
    """Parse an updated PR review comment response."""
    return PRComment.model_validate(json.loads(result))


async def update_review_comment(
    repo_full_name: str,
    comment_id: int,
    *,
    body: str,
    runner: GitHubInputRunner,
) -> PRComment:
    """Update a PR review comment through the REST API."""
    request = update_review_comment_request(
        repo_full_name,
        comment_id,
        body=body,
    )
    return parse_review_comment_response(await run_input_request(request, runner))
