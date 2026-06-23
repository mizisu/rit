from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rit.state.models import PRComment, PendingReviewComment


ReviewCommentSide = Literal["LEFT", "RIGHT"]

__all__ = (
    "ReviewCommentSide",
    "ReviewCommentTarget",
    "review_comment_target",
    "select_created_review_comment",
)


@dataclass(frozen=True)
class ReviewCommentTarget:
    """Inline review comment target submitted through a GitHub review."""

    body: str
    path: str
    line: int
    side: ReviewCommentSide

    def pending_comment(self) -> PendingReviewComment:
        return PendingReviewComment(
            body=self.body,
            path=self.path,
            line=self.line,
            side=self.side,
        )

    def synthetic_comment(self, *, review_id: int | None) -> PRComment:
        return PRComment(
            body=self.body,
            path=self.path,
            line=self.line,
            side=self.side,
            pullRequestReview=review_id,
        )


def review_comment_target(
    *,
    body: str,
    path: str,
    line: int,
    side: str,
) -> ReviewCommentTarget:
    """Return a validated inline review comment target."""
    if side == "LEFT":
        return ReviewCommentTarget(body=body, path=path, line=line, side="LEFT")
    if side == "RIGHT":
        return ReviewCommentTarget(body=body, path=path, line=line, side="RIGHT")
    raise ValueError("Inline comment side must be LEFT or RIGHT")


def select_created_review_comment(
    comments: Sequence[PRComment],
    target: ReviewCommentTarget,
    *,
    review_id: int | None,
) -> PRComment:
    """Return the created comment matching a submitted review target."""
    for comment in comments:
        if _matches_target(comment, target):
            return comment
    if comments:
        return comments[-1]
    return target.synthetic_comment(review_id=review_id)


def _matches_target(comment: PRComment, target: ReviewCommentTarget) -> bool:
    return (
        comment.path == target.path
        and comment.line == target.line
        and comment.side == target.side
        and comment.body == target.body
    )
