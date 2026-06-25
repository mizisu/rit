from __future__ import annotations

from collections.abc import Iterable, Sequence
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
    comments: Iterable[PRComment],
) -> list[TimelineItem]:
    """Return visible timeline items sorted by timeline time."""
    threads_by_review: dict[int, list[CommentThread]] = {}
    orphan_threads: list[CommentThread] = []

    comment_threads: Iterable[CommentThread] = (
        () if isinstance(comments, Sequence) and not comments
        else group_comments_into_threads(comments)
    )
    for thread in comment_threads:
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

    if len(items) > 1:
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

    if isinstance(threads, Sequence):
        thread_count = len(threads)
        if thread_count == 0:
            return datetime_min_utc()
        if thread_count == 1:
            created_at = threads[0].created_at
            return created_at if not is_min_datetime(created_at) else datetime_min_utc()

    earliest = datetime_min_utc()
    for thread in threads:
        created_at = thread.created_at
        if is_min_datetime(created_at):
            continue
        sort_time = datetime_sort_key(created_at)
        if is_min_datetime(earliest) or sort_time < earliest:
            earliest = sort_time
    return earliest


def _has_body(body: str) -> bool:
    return bool(body) and not body.isspace()
