from __future__ import annotations

from collections.abc import Sequence

from rit.state.models import (
    PR,
    PRComment,
    PRIssueComment,
    PRReview,
    PRUser,
    ReviewThread,
)


__all__ = (
    "discussion_render_signature",
    "normalized_author_login",
    "thread_render_signature",
)


_EMPTY_SIGNATURE_PART: tuple[object, ...] = ()


def discussion_render_signature(pr: PR | None) -> tuple[object, ...]:
    """Return the rendered discussion content identity for update decisions."""
    if pr is None:
        return ()

    return (
        pr.body,
        _review_signatures(pr.reviews),
        _issue_comment_signatures(pr.issue_comments),
        _thread_signatures(pr.review_threads),
    )


def thread_render_signature(thread: ReviewThread) -> tuple[object, ...]:
    return (
        thread.path,
        thread.anchor_line,
        _thread_comment_signatures(thread.comments),
    )


def _review_signatures(reviews: Sequence[PRReview]) -> tuple[object, ...]:
    if not reviews:
        return _EMPTY_SIGNATURE_PART
    if len(reviews) == 1:
        review = reviews[0]
        return (
            (
                review.id,
                normalized_author_login(review.user),
                review.state.name,
                review.body,
                review.created_at,
                review.submitted_at,
            ),
        )
    return tuple(
        (
            review.id,
            normalized_author_login(review.user),
            review.state.name,
            review.body,
            review.created_at,
            review.submitted_at,
        )
        for review in reviews
    )


def _issue_comment_signatures(
    comments: Sequence[PRIssueComment],
) -> tuple[object, ...]:
    if not comments:
        return _EMPTY_SIGNATURE_PART
    if len(comments) == 1:
        comment = comments[0]
        return (
            (
                comment.id,
                normalized_author_login(comment.user),
                comment.body,
                comment.created_at,
                comment.updated_at,
            ),
        )
    return tuple(
        (
            comment.id,
            normalized_author_login(comment.user),
            comment.body,
            comment.created_at,
            comment.updated_at,
        )
        for comment in comments
    )


def _thread_signatures(threads: Sequence[ReviewThread]) -> tuple[object, ...]:
    if not threads:
        return _EMPTY_SIGNATURE_PART
    if len(threads) == 1:
        return (thread_render_signature(threads[0]),)
    return tuple(thread_render_signature(thread) for thread in threads)


def _thread_comment_signatures(comments: Sequence[PRComment]) -> tuple[object, ...]:
    if not comments:
        return _EMPTY_SIGNATURE_PART
    if len(comments) == 1:
        comment = comments[0]
        return (
            (
                comment.id,
                normalized_author_login(comment.user),
                comment.body,
                comment.path,
                comment.anchor_line,
                comment.created_at,
                comment.updated_at,
                comment.in_reply_to_id,
                comment.pull_request_review_id,
                comment.diff_hunk,
            ),
        )
    return tuple(
        (
            comment.id,
            normalized_author_login(comment.user),
            comment.body,
            comment.path,
            comment.anchor_line,
            comment.created_at,
            comment.updated_at,
            comment.in_reply_to_id,
            comment.pull_request_review_id,
            comment.diff_hunk,
        )
        for comment in comments
    )


def normalized_author_login(user: PRUser | None) -> str:
    if user is None:
        return ""
    login = user.login
    if login.endswith("[bot]"):
        return login[: -len("[bot]")]
    return login
