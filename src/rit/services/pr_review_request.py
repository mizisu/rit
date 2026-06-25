from __future__ import annotations

from collections.abc import Sequence
import json

from pydantic import TypeAdapter

from rit.services.gh_paginated_json import parse_paginated_items
from rit.services.gh_request import (
    GitHubInputRequest,
    GitHubInputRunner,
    run_input_request,
    run_request,
)
from rit.services.pr_review_payload import (
    pending_review_payload,
    review_event_payload,
    submit_review_payload,
)
from rit.services.pr_review_comment_selection import (
    review_comment_target,
    select_created_review_comment,
)
from rit.state.models import PRComment, PRIssueComment, PRReview, PendingReviewComment


__all__ = (
    "create_issue_comment",
    "create_issue_comment_request",
    "create_pending_review",
    "create_pending_review_request",
    "create_review_comment",
    "delete_pending_review",
    "delete_pending_review_request",
    "list_review_comments",
    "list_review_comments_request",
    "parse_issue_comment_response",
    "parse_review_comments_response",
    "parse_review_response",
    "pull_request_review_comments_request",
    "run_review_request",
    "submit_pending_review",
    "submit_pending_review_request",
    "submit_review",
    "submit_review_request",
)


_PRCommentListAdapter: TypeAdapter[list[PRComment]] = TypeAdapter(list[PRComment])


def create_pending_review_request(
    repo_full_name: str,
    pr_number: int,
    *,
    payload: dict[str, object],
) -> GitHubInputRequest:
    """Build a gh api request for creating a pending PR review."""
    return _review_input_request(repo_full_name, pr_number, payload=payload)


def submit_review_request(
    repo_full_name: str,
    pr_number: int,
    *,
    payload: dict[str, object],
) -> GitHubInputRequest:
    """Build a gh api request for submitting a PR review."""
    return _review_input_request(repo_full_name, pr_number, payload=payload)


def submit_pending_review_request(
    repo_full_name: str,
    pr_number: int,
    *,
    review_id: int,
    payload: dict[str, object],
) -> GitHubInputRequest:
    """Build a gh api request for submitting an existing pending review."""
    return GitHubInputRequest(
        args=(
            "api",
            "--method",
            "POST",
            f"/repos/{repo_full_name}/pulls/{pr_number}/reviews/{review_id}/events",
            "--input",
            "-",
        ),
        input_text=json.dumps(payload),
    )


def delete_pending_review_request(
    repo_full_name: str,
    pr_number: int,
    *,
    review_id: int,
) -> tuple[str, ...]:
    """Build a gh api request for deleting a pending review."""
    return (
        "api",
        "--method",
        "DELETE",
        f"/repos/{repo_full_name}/pulls/{pr_number}/reviews/{review_id}",
    )


def pull_request_review_comments_request(
    repo_full_name: str,
    pr_number: int,
) -> tuple[str, ...]:
    """Build a gh api request for all PR review comments."""
    return (
        "api",
        f"/repos/{repo_full_name}/pulls/{pr_number}/comments?per_page=100",
    )


def list_review_comments_request(
    repo_full_name: str,
    pr_number: int,
    *,
    review_id: int,
) -> tuple[str, ...]:
    """Build a gh api request for one review's comments."""
    return (
        "api",
        f"/repos/{repo_full_name}/pulls/{pr_number}/reviews/{review_id}/comments",
        "--paginate",
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


def parse_review_response(result: str) -> PRReview:
    """Parse a PR review REST response."""
    return PRReview.model_validate(json.loads(result))


def parse_issue_comment_response(result: str) -> PRIssueComment:
    """Parse a PR-level issue comment REST response."""
    return PRIssueComment.model_validate(json.loads(result))


def parse_review_comments_response(result: str) -> list[PRComment]:
    """Parse paginated PR review comment REST output."""
    items = parse_paginated_items(result)
    if not items:
        return []
    if len(items) == 1:
        return [PRComment.model_validate(items[0])]
    return _PRCommentListAdapter.validate_python(items)


async def run_review_request(
    request: GitHubInputRequest,
    runner: GitHubInputRunner,
) -> PRReview:
    """Run a PR review request and parse the review response."""
    return parse_review_response(await run_input_request(request, runner))


async def create_pending_review(
    repo_full_name: str,
    pr_number: int,
    *,
    comments: Sequence[PendingReviewComment],
    body: str | None = None,
    commit_id: str | None = None,
    runner: GitHubInputRunner,
) -> PRReview:
    """Create a pending PR review via the REST API."""
    payload = pending_review_payload(
        comments=comments,
        body=body,
        commit_id=commit_id,
    )
    review = await run_review_request(
        create_pending_review_request(repo_full_name, pr_number, payload=payload),
        runner,
    )
    if body and not review.body:
        return review.model_copy(update={"body": body})
    return review


async def submit_pending_review(
    repo_full_name: str,
    pr_number: int,
    *,
    review_id: int,
    event: str,
    body: str | None = None,
    runner: GitHubInputRunner,
) -> PRReview:
    """Submit an existing pending PR review via the REST API."""
    payload = review_event_payload(event=event, body=body)
    return await run_review_request(
        submit_pending_review_request(
            repo_full_name,
            pr_number,
            review_id=review_id,
            payload=payload,
        ),
        runner,
    )


async def submit_review(
    repo_full_name: str,
    pr_number: int,
    *,
    event: str,
    body: str | None = None,
    comments: Sequence[PendingReviewComment] | None = None,
    commit_id: str | None = None,
    runner: GitHubInputRunner,
) -> PRReview:
    """Submit a PR review via the REST API."""
    payload = submit_review_payload(
        event=event,
        body=body,
        comments=comments,
        commit_id=commit_id,
    )
    return await run_review_request(
        submit_review_request(repo_full_name, pr_number, payload=payload),
        runner,
    )


async def create_review_comment(
    repo_full_name: str,
    pr_number: int,
    *,
    body: str,
    commit_id: str,
    path: str,
    line: int,
    side: str,
    runner: GitHubInputRunner,
) -> PRComment:
    """Create an inline review comment via a submitted GitHub review."""
    target = review_comment_target(
        body=body,
        path=path,
        line=line,
        side=side,
    )
    review = await submit_review(
        repo_full_name,
        pr_number,
        event="COMMENT",
        commit_id=commit_id,
        comments=[target.pending_comment()],
        runner=runner,
    )
    if review.id:
        comments = await list_review_comments(
            repo_full_name,
            pr_number,
            review_id=review.id,
            runner=runner,
        )
        return select_created_review_comment(
            comments,
            target,
            review_id=review.id,
        )
    return select_created_review_comment([], target, review_id=None)


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


async def list_review_comments(
    repo_full_name: str,
    pr_number: int,
    *,
    review_id: int,
    runner: GitHubInputRunner,
) -> list[PRComment]:
    """Fetch comments for one PR review."""
    return parse_review_comments_response(
        await run_request(
            list_review_comments_request(
                repo_full_name,
                pr_number,
                review_id=review_id,
            ),
            runner,
        )
    )


async def delete_pending_review(
    repo_full_name: str,
    pr_number: int,
    *,
    review_id: int,
    runner: GitHubInputRunner,
) -> None:
    """Delete a pending PR review."""
    await run_request(
        delete_pending_review_request(
            repo_full_name,
            pr_number,
            review_id=review_id,
        ),
        runner,
    )


def _review_input_request(
    repo_full_name: str,
    pr_number: int,
    *,
    payload: dict[str, object],
) -> GitHubInputRequest:
    return GitHubInputRequest(
        args=(
            "api",
            "--method",
            "POST",
            f"/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            "--input",
            "-",
        ),
        input_text=json.dumps(payload),
    )
