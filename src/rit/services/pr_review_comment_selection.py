from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rit.state.models import PendingReviewComment, PRComment

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
    start_line: int | None = None
    start_side: ReviewCommentSide | None = None

    def pending_comment(self) -> PendingReviewComment:
        return PendingReviewComment(
            body=self.body,
            path=self.path,
            line=self.line,
            side=self.side,
            start_line=self.start_line,
            start_side=self.start_side,
        )

    def synthetic_comment(self, *, review_id: int | None) -> PRComment:
        return PRComment(
            body=self.body,
            path=self.path,
            line=self.line,
            side=self.side,
            start_line=self.start_line,
            start_side=self.start_side or "",
            pull_request_review_id=review_id,
        )


def review_comment_target(
    *,
    body: str,
    path: str,
    line: int,
    side: str,
    start_line: int | None = None,
    start_side: str | None = None,
) -> ReviewCommentTarget:
    """Return a validated inline review comment target."""
    target_side = _review_comment_side(side)
    if target_side is None:
        raise ValueError("Inline comment side must be LEFT or RIGHT")

    target_start_side = None
    if start_line is not None:
        target_start_side = _review_comment_side(start_side or side)
        if target_start_side is None:
            raise ValueError("Inline comment start side must be LEFT or RIGHT")

    return ReviewCommentTarget(
        body=body,
        path=path,
        line=line,
        side=target_side,
        start_line=start_line,
        start_side=target_start_side,
    )


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
        and comment.start_line == target.start_line
        and _comment_start_side(comment) == target.start_side
        and comment.body == target.body
    )


def _review_comment_side(side: str | None) -> ReviewCommentSide | None:
    if side == "LEFT":
        return "LEFT"
    if side == "RIGHT":
        return "RIGHT"
    return None


def _comment_start_side(comment: PRComment) -> ReviewCommentSide | None:
    if comment.start_line is None:
        return None
    return _review_comment_side(comment.start_side or comment.side)
