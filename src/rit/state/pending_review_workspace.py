from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rit.state.models import PendingReviewComment, PRComment, PRReview
from rit.state.pending_review import (
    merge_pending_review_comments,
    plan_pending_review_sync,
)

__all__ = (
    "PendingReviewAdapter",
    "PendingReviewReplacement",
    "replace_pending_review",
)


class PendingReviewAdapter(Protocol):
    async def list_review_comments(
        self,
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]: ...

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None: ...

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments: list[PendingReviewComment],
        body: str | None = None,
        commit_id: str | None = None,
    ) -> PRReview: ...


@dataclass(frozen=True)
class PendingReviewReplacement:
    """Result of replacing GitHub's pending review draft."""

    review: PRReview | None
    comments: list[PendingReviewComment]


async def replace_pending_review(
    *,
    adapter: PendingReviewAdapter,
    pr_number: int,
    comments: Sequence[PendingReviewComment],
    pending_review_id: int | None,
    pending_review_body: str,
    head_sha: str,
    removed_comment: PendingReviewComment | None = None,
) -> PendingReviewReplacement:
    """Replace GitHub's pending review without dropping unverified server drafts."""
    replacement_comments = list(comments)
    if pending_review_id is not None:
        server_comments = await adapter.list_review_comments(
            pr_number, pending_review_id
        )
        if (
            not server_comments
            and replacement_comments
            and not any(comment.review_comment_id for comment in replacement_comments)
        ):
            raise RuntimeError(
                "Could not verify existing pending review comments; not replacing pending review"
            )
        replacement_comments = merge_pending_review_comments(
            replacement_comments,
            server_comments,
            removed_comment=removed_comment,
        )

    plan = plan_pending_review_sync(
        replacement_comments,
        pending_review_id=pending_review_id,
        pending_review_body=pending_review_body,
        head_sha=head_sha,
    )
    if plan.delete_review_id is not None:
        await adapter.delete_pending_review(pr_number, plan.delete_review_id)
    if not plan.should_create:
        return PendingReviewReplacement(review=None, comments=replacement_comments)

    review = await adapter.create_pending_review(
        pr_number,
        comments=plan.comments,
        body=plan.body,
        commit_id=plan.commit_id,
    )
    return PendingReviewReplacement(review=review, comments=replacement_comments)
