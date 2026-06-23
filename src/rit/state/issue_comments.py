from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rit.core.datetime_utils import datetime_sort_key
from rit.state.models import NodeList, PR, PRIssueComment


__all__ = (
    "IssueCommentSubmissionProjection",
    "apply_submitted_issue_comment",
    "insert_issue_comment",
    "normalize_issue_comment_body",
)


@dataclass(frozen=True)
class IssueCommentSubmissionProjection:
    """Store-ready state after submitting a PR-level comment."""

    pr: PR | None
    issue_comments: list[PRIssueComment]


def normalize_issue_comment_body(body: str) -> str:
    """Return trimmed PR-level comment text or raise when empty."""
    normalized = body.strip()
    if not normalized:
        raise ValueError("Comment cannot be empty")
    return normalized


def insert_issue_comment(
    comments: Sequence[PRIssueComment],
    comment: PRIssueComment,
) -> list[PRIssueComment]:
    """Return issue comments with a new comment inserted in created order."""
    updated = [*comments, comment]
    updated.sort(key=lambda item: datetime_sort_key(item.created_at))
    return updated


def apply_submitted_issue_comment(
    *,
    pr: PR | None,
    comments: Sequence[PRIssueComment],
    comment: PRIssueComment,
) -> IssueCommentSubmissionProjection:
    """Return store-ready issue comment state after submitting a comment."""
    issue_comments = insert_issue_comment(comments, comment)
    if pr is None:
        return IssueCommentSubmissionProjection(
            pr=None,
            issue_comments=issue_comments,
        )

    return IssueCommentSubmissionProjection(
        pr=pr.model_copy(
            update={
                "issue_comments_connection": NodeList.from_nodes(issue_comments),
            }
        ),
        issue_comments=issue_comments,
    )
