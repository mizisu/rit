from __future__ import annotations

import json

from rit.services.gh_request import (
    GitHubInputRequest,
    GitHubInputRunner,
    run_input_request,
)
from rit.state.models import PRComment


def create_file_comment_request(
    repo_full_name: str,
    pr_number: int,
    *,
    body: str,
    commit_id: str,
    path: str,
) -> GitHubInputRequest:
    """Build a REST request for a file-level PR review comment."""
    return GitHubInputRequest(
        args=(
            "api",
            "--method",
            "POST",
            f"/repos/{repo_full_name}/pulls/{pr_number}/comments",
            "--input",
            "-",
        ),
        input_text=json.dumps(
            {
                "body": body,
                "commit_id": commit_id,
                "path": path,
                "subject_type": "file",
            }
        ),
    )


def parse_file_comment_response(result: str) -> PRComment:
    """Parse a file-level review comment response."""
    comment = PRComment.model_validate(json.loads(result))
    if comment.subject_type.lower() == "file":
        return comment
    return comment.model_copy(update={"subject_type": "file"})


async def create_file_comment(
    repo_full_name: str,
    pr_number: int,
    *,
    body: str,
    commit_id: str,
    path: str,
    runner: GitHubInputRunner,
) -> PRComment:
    """Create a file-level PR review comment through the REST API."""
    request = create_file_comment_request(
        repo_full_name,
        pr_number,
        body=body,
        commit_id=commit_id,
        path=path,
    )
    return parse_file_comment_response(await run_input_request(request, runner))
