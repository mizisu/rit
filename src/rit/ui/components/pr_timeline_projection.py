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
    PRTimelineEvent,
    ReviewState,
    group_comments_into_threads,
)

__all__ = (
    "TimelineItem",
    "TimelineItemKind",
    "build_timeline_items",
    "review_timeline_time",
)


TimelineItemKind = Literal["issue_comment", "review", "thread", "event"]


@dataclass(frozen=True)
class TimelineItem:
    """Mount-ready PR timeline item."""

    when: datetime
    kind: TimelineItemKind
    issue_comment: PRIssueComment | None = None
    review: PRReview | None = None
    thread: CommentThread | None = None
    threads: list[CommentThread] = field(default_factory=list)
    events: list[PRTimelineEvent] = field(default_factory=list)


def review_has_summary(review: PRReview) -> bool:
    """Keep review decisions visible even without a written summary."""
    return _has_body(review.body) or review.state in {
        ReviewState.APPROVED,
        ReviewState.CHANGES_REQUESTED,
        ReviewState.DISMISSED,
    }


def build_timeline_items(
    *,
    issue_comments: Sequence[PRIssueComment],
    reviews: Sequence[PRReview],
    comments: Iterable[PRComment],
    events: Sequence[PRTimelineEvent] = (),
) -> list[TimelineItem]:
    """Preserve GitHub timeline order; append local-only items by time."""
    threads_by_review: dict[int, list[CommentThread]] = {}
    orphan_threads: list[CommentThread] = []

    comment_threads: Iterable[CommentThread] = (
        ()
        if isinstance(comments, Sequence) and not comments
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
        if review_has_summary(review) or review_threads:
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

    # ponytail: same-second merge/close pairing; use explicit links if GitHub exposes them.
    merged_times = {
        datetime_sort_key(event.created_at)
        for event in events
        if event.kind == "MergedEvent"
    }
    timeline_order: dict[tuple[TimelineItemKind, int | str], int] = {}
    for index, event in enumerate(events):
        if event.kind in {"IssueComment", "PullRequestReview"}:
            if event.database_id is not None:
                kind: TimelineItemKind = (
                    "issue_comment" if event.kind == "IssueComment" else "review"
                )
                timeline_order[kind, event.database_id] = index
            continue
        if (
            event.kind == "ClosedEvent"
            and datetime_sort_key(event.created_at) in merged_times
        ):
            continue
        timeline_order["event", event.id] = index
        items.append(TimelineItem(when=event.created_at, kind="event", events=[event]))

    if len(items) < 2:
        return items

    def sort_key(item: TimelineItem) -> tuple[int, datetime]:
        identifier: int | str = 0
        if item.issue_comment is not None:
            identifier = item.issue_comment.id
        elif item.review is not None:
            identifier = item.review.id
        elif item.events:
            identifier = item.events[0].id
        return (
            timeline_order.get((item.kind, identifier), len(events)),
            datetime_sort_key(item.when),
        )

    items.sort(key=sort_key)

    grouped: list[TimelineItem] = []
    for item in items:
        if (
            grouped
            and item.events
            and item.events[0].kind == "PullRequestCommit"
            and grouped[-1].events
            and grouped[-1].events[-1].kind == "PullRequestCommit"
            and grouped[-1].events[-1].actor == item.events[0].actor
        ):
            grouped[-1].events.extend(item.events)
        else:
            grouped.append(item)
    return grouped


def review_timeline_time(
    review: PRReview,
    threads: Sequence[CommentThread],
) -> datetime:
    """Return the timeline sort time for a review and its threads."""
    if review.submitted_at is not None:
        return review.submitted_at
    if review.state == ReviewState.PENDING:
        latest_thread_time = _latest_thread_time(threads)
        if latest_thread_time is not None:
            return latest_thread_time
    if not is_min_datetime(review.created_at):
        return review.created_at

    earliest_thread_time = _earliest_thread_time(threads)
    return earliest_thread_time or datetime_min_utc()


def _latest_thread_time(threads: Sequence[CommentThread]) -> datetime | None:
    thread_count = len(threads)
    if thread_count == 0:
        return None
    if thread_count == 1:
        created_at = threads[0].created_at
        return None if is_min_datetime(created_at) else created_at

    latest: datetime | None = None
    for thread in threads:
        created_at = thread.created_at
        if is_min_datetime(created_at):
            continue
        sort_time = datetime_sort_key(created_at)
        if latest is None or sort_time > latest:
            latest = sort_time
    return latest


def _earliest_thread_time(threads: Sequence[CommentThread]) -> datetime | None:
    thread_count = len(threads)
    if thread_count == 0:
        return None
    if thread_count == 1:
        created_at = threads[0].created_at
        return None if is_min_datetime(created_at) else created_at

    earliest: datetime | None = None
    for thread in threads:
        created_at = thread.created_at
        if is_min_datetime(created_at):
            continue
        sort_time = datetime_sort_key(created_at)
        if earliest is None or sort_time < earliest:
            earliest = sort_time
    return earliest


def _has_body(body: str) -> bool:
    return bool(body) and not body.isspace()
