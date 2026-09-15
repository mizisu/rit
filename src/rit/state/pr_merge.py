from __future__ import annotations

from collections.abc import Sequence

from rit.state.models import (
    PR,
    NodeList,
    PRIssueComment,
    PRReview,
    PRTimelineEvent,
    ReviewThread,
)

__all__ = (
    "merge_pr_discussion",
    "merge_pr_summary",
)


def merge_pr_summary(
    summary: PR,
    *,
    existing: PR | None,
    reviews: Sequence[PRReview],
    issue_comments: Sequence[PRIssueComment],
    review_threads: Sequence[ReviewThread],
) -> PR:
    """Return summary data while preserving loaded discussion slices."""
    if existing is None:
        return summary

    return summary.model_copy(
        update={
            "body": existing.body,
            "timeline_events_connection": existing.timeline_events_connection,
            "reviews_connection": NodeList.from_nodes(reviews),
            "issue_comments_connection": NodeList.from_nodes(issue_comments),
            "review_threads_connection": NodeList.from_nodes(review_threads),
        }
    )


def merge_pr_discussion(
    *,
    existing: PR | None,
    pr_number: int,
    body: str,
    reviews: Sequence[PRReview],
    issue_comments: Sequence[PRIssueComment],
    review_threads: Sequence[ReviewThread],
    timeline_events: Sequence[PRTimelineEvent] = (),
) -> PR:
    """Return PR data with discussion slices applied."""
    pr = existing or PR(number=pr_number)
    merged_body = body or pr.body
    return pr.model_copy(
        update={
            "body": merged_body,
            "timeline_events_connection": NodeList.from_nodes(timeline_events),
            "reviews_connection": NodeList.from_nodes(reviews),
            "issue_comments_connection": NodeList.from_nodes(issue_comments),
            "review_threads_connection": NodeList.from_nodes(review_threads),
        }
    )
