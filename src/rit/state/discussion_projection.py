from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from rit.state.models import (
    NodeList,
    PR,
    PRComment,
    PRIssueComment,
    PRReview,
    ReviewThreadInfo,
    ReviewThread,
)


__all__ = (
    "DiscussionProjection",
    "RecentDiscussion",
    "ThreadResolutionProjection",
    "merge_recent_submitted_discussion",
    "project_discussion_state",
    "remember_submitted_comment",
    "remember_submitted_review",
    "thread_from_submitted_comment",
    "update_thread_resolution",
)


@dataclass(frozen=True)
class RecentDiscussion:
    """Recently submitted discussion objects that may precede GitHub refresh data."""

    reviews: Mapping[int, PRReview] = field(default_factory=dict)
    review_comments: Mapping[int, Sequence[PRComment]] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscussionProjection:
    """Derived discussion state ready to install into the store."""

    pr: PR
    reviews: list[PRReview]
    issue_comments: list[PRIssueComment]
    review_threads: list[ReviewThread]
    comments: list[PRComment]
    comments_by_file: dict[str, list[PRComment]]
    thread_info_cache: dict[int, ReviewThreadInfo]
    thread_cache: dict[int, ReviewThread]


@dataclass(frozen=True)
class ThreadResolutionProjection:
    """Updated thread resolution slices for store state."""

    review_threads: list[ReviewThread]
    thread_info_cache: dict[int, ReviewThreadInfo]
    thread_cache: dict[int, ReviewThread]


def project_discussion_state(
    pr: PR,
    *,
    recent: RecentDiscussion | None = None,
) -> DiscussionProjection:
    """Return store-ready discussion state for a PR."""
    reviews = list(pr.reviews)
    review_threads = list(pr.review_threads)
    if recent is not None:
        reviews, review_threads = merge_recent_submitted_discussion(
            reviews,
            review_threads,
            recent=recent,
        )

    comments = [
        comment
        for thread in review_threads
        for comment in thread.comments
    ]
    comments_by_file = _comments_by_file(comments)

    return DiscussionProjection(
        pr=pr.model_copy(
            update={
                "reviews_connection": NodeList.from_nodes(reviews),
                "review_threads_connection": NodeList.from_nodes(review_threads),
            }
        ),
        reviews=reviews,
        issue_comments=pr.issue_comments,
        review_threads=review_threads,
        comments=comments,
        comments_by_file=comments_by_file,
        thread_info_cache=_thread_info_cache(review_threads),
        thread_cache=_thread_cache(review_threads),
    )


def merge_recent_submitted_discussion(
    reviews: list[PRReview],
    review_threads: list[ReviewThread],
    *,
    recent: RecentDiscussion,
) -> tuple[list[PRReview], list[ReviewThread]]:
    """Return discussion lists with optimistic submitted objects included."""
    if not recent.reviews and not recent.review_comments:
        return reviews, review_threads

    review_ids = {review.id for review in reviews if review.id}
    for review_id, review in recent.reviews.items():
        if review_id not in review_ids:
            reviews.append(review)
            review_ids.add(review_id)

    comment_ids = {
        comment.id
        for thread in review_threads
        for comment in thread.comments
        if comment.id
    }
    for comments in recent.review_comments.values():
        for comment in comments:
            if comment.id and comment.id in comment_ids:
                continue
            review_threads.append(thread_from_submitted_comment(comment))
            if comment.id:
                comment_ids.add(comment.id)

    return reviews, review_threads


def remember_submitted_review(
    recent: RecentDiscussion,
    review: PRReview | None,
    comments: Sequence[PRComment],
) -> RecentDiscussion:
    """Return recent discussion state with one submitted review recorded."""
    if review is None or not review.id:
        return recent

    reviews = dict(recent.reviews)
    review_comments = _mutable_review_comments(recent)
    reviews[review.id] = review
    review_comments[review.id] = [
        _comment_with_review_id(comment, review.id) for comment in comments
    ]
    return RecentDiscussion(reviews=reviews, review_comments=review_comments)


def remember_submitted_comment(
    recent: RecentDiscussion,
    comment: PRComment,
) -> RecentDiscussion:
    """Return recent discussion state with one submitted inline comment recorded."""
    review_id = comment.pull_request_review_id
    if not review_id:
        return recent

    review_comments = _mutable_review_comments(recent)
    comments = review_comments.setdefault(review_id, [])
    if not comment.id or all(existing.id != comment.id for existing in comments):
        comments.append(comment)
    return RecentDiscussion(reviews=dict(recent.reviews), review_comments=review_comments)


def thread_from_submitted_comment(comment: PRComment) -> ReviewThread:
    anchor_side = comment.anchor_side
    anchor_line = comment.anchor_line
    line = anchor_line if anchor_side == "new" else None
    original_line = anchor_line if anchor_side == "old" else None
    return ReviewThread.model_validate(
        {
            "id": "",
            "isResolved": False,
            "path": comment.path,
            "line": line,
            "originalLine": original_line,
            "diffSide": comment.side,
            "comments": {"nodes": [comment]},
        }
    )


def update_thread_resolution(
    *,
    review_threads: Sequence[ReviewThread],
    thread_info_cache: Mapping[int, ReviewThreadInfo],
    thread_cache: Mapping[int, ReviewThread],
    root_comment_id: int,
    is_resolved: bool,
) -> ThreadResolutionProjection:
    """Return discussion thread state with one root comment resolution updated."""
    updated_threads = list(review_threads)
    updated_info = dict(thread_info_cache)
    updated_cache = dict(thread_cache)

    if root_comment_id in updated_info:
        updated_info[root_comment_id] = replace(
            updated_info[root_comment_id],
            is_resolved=is_resolved,
        )

    for index, thread in enumerate(updated_threads):
        if thread.root_comment_id == root_comment_id:
            updated_thread = thread.model_copy(update={"is_resolved": is_resolved})
            updated_threads[index] = updated_thread
            updated_cache[root_comment_id] = updated_thread
            break

    return ThreadResolutionProjection(
        review_threads=updated_threads,
        thread_info_cache=updated_info,
        thread_cache=updated_cache,
    )


def _comments_by_file(comments: Sequence[PRComment]) -> dict[str, list[PRComment]]:
    by_file: dict[str, list[PRComment]] = {}
    for comment in comments:
        by_file.setdefault(comment.path, []).append(comment)
    return by_file


def _thread_info_cache(
    review_threads: Sequence[ReviewThread],
) -> dict[int, ReviewThreadInfo]:
    return {
        thread.root_comment_id: ReviewThreadInfo(
            thread_id=thread.id,
            is_resolved=thread.is_resolved,
            path=thread.path,
            line=thread.anchor_line,
            root_comment_id=thread.root_comment_id,
        )
        for thread in review_threads
        if thread.id and thread.root_comment_id
    }


def _thread_cache(review_threads: Sequence[ReviewThread]) -> dict[int, ReviewThread]:
    return {
        thread.root_comment_id: thread
        for thread in review_threads
        if thread.root_comment_id
    }


def _mutable_review_comments(
    recent: RecentDiscussion,
) -> dict[int, list[PRComment]]:
    return {
        review_id: list(comments)
        for review_id, comments in recent.review_comments.items()
    }


def _comment_with_review_id(comment: PRComment, review_id: int) -> PRComment:
    if comment.pull_request_review_id:
        return comment
    return comment.model_copy(update={"pull_request_review_id": review_id})
