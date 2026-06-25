from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import cast

from rit.state.models import (
    PR,
    NodeList,
    PRComment,
    PRIssueComment,
    PRReview,
    ReviewThread,
    ReviewThreadInfo,
)

__all__ = (
    "DiscussionProjection",
    "PRDiscussion",
    "PRDiscussionReadModel",
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
class PRDiscussion:
    """Discussion timeline data loaded from a PR source."""

    body: str
    reviews: list[PRReview]
    issue_comments: list[PRIssueComment]
    review_threads: list[ReviewThread]


@dataclass(frozen=True)
class RecentDiscussion:
    """Recently submitted discussion objects that may precede GitHub refresh data."""

    reviews: Mapping[int, PRReview] = field(default_factory=dict)
    review_comments: Mapping[int, Sequence[PRComment]] = field(default_factory=dict)


@dataclass(frozen=True)
class PRDiscussionReadModel:
    """Read model for PR discussion data and derived views."""

    pr: PR
    reviews: list[PRReview]
    issue_comments: list[PRIssueComment]
    review_threads: list[ReviewThread]
    comments: list[PRComment]
    comments_by_file: dict[str, list[PRComment]]
    thread_info_cache: dict[int, ReviewThreadInfo]
    thread_cache: dict[int, ReviewThread]


DiscussionProjection = PRDiscussionReadModel


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
) -> PRDiscussionReadModel:
    """Return store-ready discussion state for a PR."""
    if recent is not None and (recent.reviews or recent.review_comments):
        reviews = list(pr.reviews)
        review_threads = list(pr.review_threads)
        reviews, review_threads = merge_recent_submitted_discussion(
            reviews,
            review_threads,
            recent=recent,
        )
        projected_pr = pr.model_copy(
            update={
                "reviews_connection": NodeList.from_nodes(reviews),
                "review_threads_connection": NodeList.from_nodes(review_threads),
            }
        )
    else:
        reviews = pr.reviews
        review_threads = pr.review_threads
        projected_pr = pr

    if not review_threads:
        comments = []
        comments_by_file = {}
        thread_info_cache = {}
        thread_cache = {}
    else:
        comments = [comment for thread in review_threads for comment in thread.comments]
        if len(comments) == 1:
            comment = comments[0]
            comments_by_file = {comment.path: [comment]}
        else:
            comments_by_file = _comments_by_file(comments)
        thread_info_cache = _thread_info_cache(review_threads)
        thread_cache = _thread_cache(review_threads)

    return PRDiscussionReadModel(
        pr=projected_pr,
        reviews=reviews,
        issue_comments=pr.issue_comments,
        review_threads=review_threads,
        comments=comments,
        comments_by_file=comments_by_file,
        thread_info_cache=thread_info_cache,
        thread_cache=thread_cache,
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

    if recent.reviews:
        if not reviews:
            reviews.extend(recent.reviews.values())
        else:
            review_ids = {review.id for review in reviews if review.id}
            for review_id, review in recent.reviews.items():
                if review_id not in review_ids:
                    reviews.append(review)
                    review_ids.add(review_id)

    if not recent.review_comments:
        return reviews, review_threads

    if not review_threads:
        for comments in recent.review_comments.values():
            for comment in comments:
                review_threads.append(thread_from_submitted_comment(comment))
        return reviews, review_threads

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
    review_comments = dict(recent.review_comments)
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

    existing_comments = recent.review_comments.get(review_id)
    if comment.id and existing_comments is not None:
        if len(existing_comments) == 1:
            if existing_comments[0].id == comment.id:
                return recent
        elif any(existing.id == comment.id for existing in existing_comments):
            return recent

    review_comments = dict(recent.review_comments)
    comments = list(existing_comments) if existing_comments is not None else []
    comments.append(comment)
    review_comments[review_id] = comments
    return RecentDiscussion(
        reviews=dict(recent.reviews), review_comments=review_comments
    )


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
    info = thread_info_cache.get(root_comment_id)
    matching_thread_index = next(
        (
            index
            for index, thread in enumerate(review_threads)
            if thread.root_comment_id == root_comment_id
        ),
        None,
    )
    matching_thread = (
        review_threads[matching_thread_index]
        if matching_thread_index is not None
        else None
    )
    if info is None and matching_thread_index is None:
        return _thread_resolution_projection(
            review_threads,
            thread_info_cache,
            thread_cache,
        )
    if (
        info is not None
        and info.is_resolved == is_resolved
        and matching_thread is not None
        and matching_thread.is_resolved == is_resolved
    ):
        return _thread_resolution_projection(
            review_threads,
            thread_info_cache,
            thread_cache,
        )

    updated_threads = list(review_threads)
    updated_info = dict(thread_info_cache)
    updated_cache = dict(thread_cache)

    if info is not None:
        updated_info[root_comment_id] = replace(
            info,
            is_resolved=is_resolved,
        )

    if matching_thread_index is not None:
        thread = review_threads[matching_thread_index]
        updated_thread = thread.model_copy(update={"is_resolved": is_resolved})
        updated_threads[matching_thread_index] = updated_thread
        updated_cache[root_comment_id] = updated_thread

    return ThreadResolutionProjection(
        review_threads=updated_threads,
        thread_info_cache=updated_info,
        thread_cache=updated_cache,
    )


def _thread_resolution_projection(
    review_threads: Sequence[ReviewThread],
    thread_info_cache: Mapping[int, ReviewThreadInfo],
    thread_cache: Mapping[int, ReviewThread],
) -> ThreadResolutionProjection:
    return ThreadResolutionProjection(
        review_threads=(
            cast(list[ReviewThread], review_threads)
            if isinstance(review_threads, list)
            else list(review_threads)
        ),
        thread_info_cache=(
            cast(dict[int, ReviewThreadInfo], thread_info_cache)
            if isinstance(thread_info_cache, dict)
            else dict(thread_info_cache)
        ),
        thread_cache=(
            cast(dict[int, ReviewThread], thread_cache)
            if isinstance(thread_cache, dict)
            else dict(thread_cache)
        ),
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


def _comment_with_review_id(comment: PRComment, review_id: int) -> PRComment:
    if comment.pull_request_review_id:
        return comment
    return comment.model_copy(update={"pull_request_review_id": review_id})
