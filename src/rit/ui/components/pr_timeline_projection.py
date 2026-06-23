from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from rit.core.datetime_utils import (
    datetime_min_utc,
    datetime_sort_key,
    is_min_datetime,
)
from rit.state.models import (
    CommentThread,
    PRComment,
    PRIssueComment,
    PRReview,
    group_comments_into_threads,
)

__all__ = (
    "TimelineItem",
    "TimelineItemKind",
    "build_timeline_items",
    "review_timeline_time",
)


TimelineItemKind = Literal["issue_comment", "review", "thread"]


@dataclass(frozen=True)
class TimelineItem:
    """Mount-ready PR timeline item."""

    when: datetime
    kind: TimelineItemKind
    issue_comment: PRIssueComment | None = None
    review: PRReview | None = None
    thread: CommentThread | None = None
    threads: list[CommentThread] = field(default_factory=list)


def build_timeline_items(
    *,
    issue_comments: Sequence[PRIssueComment],
    reviews: Sequence[PRReview],
    comments: Sequence[PRComment],
) -> list[TimelineItem]:
    """Return visible timeline items sorted by timeline time."""
    threads_by_review: dict[int, list[CommentThread]] = {}
    orphan_threads: list[CommentThread] = []

    for thread in group_comments_into_threads(list(comments)):
        if not _has_body(thread.root_comment.body):
            continue
        review_id = thread.root_comment.pull_request_review_id
        if review_id:
            threads_by_review.setdefault(review_id, []).append(thread)
        else:
            orphan_threads.append(thread)

    items: list[TimelineItem] = []

    for comment in issue_comments:
        if _has_body(comment.body):
            items.append(
                TimelineItem(
                    when=comment.created_at,
                    kind="issue_comment",
                    issue_comment=comment,
                )
            )

    for review in reviews:
        review_threads = threads_by_review.get(review.id, [])
        if _has_body(review.body) or review_threads:
            items.append(
                TimelineItem(
                    when=review_timeline_time(review, review_threads),
                    kind="review",
                    review=review,
                    threads=review_threads,
                )
            )

    for thread in orphan_threads:
        items.append(TimelineItem(when=thread.created_at, kind="thread", thread=thread))

    items.sort(key=lambda item: datetime_sort_key(item.when))
    return items


def review_timeline_time(
    review: PRReview,
    threads: Sequence[CommentThread],
) -> datetime:
    """Return the timeline sort time for a review and its threads."""
    if review.submitted_at is not None:
        return review.submitted_at
    if not is_min_datetime(review.created_at):
        return review.created_at

    thread_times = [
        datetime_sort_key(thread.created_at)
        for thread in threads
        if not is_min_datetime(thread.created_at)
    ]
    if thread_times:
        return min(thread_times)
    return datetime_min_utc()


def _has_body(body: str) -> bool:
    return bool(body and body.strip())
