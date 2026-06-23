from __future__ import annotations

from rit.state.models import PR, PRUser, ReviewThread


__all__ = (
    "discussion_render_signature",
    "normalized_author_login",
    "thread_render_signature",
)


def discussion_render_signature(pr: PR | None) -> tuple[object, ...]:
    """Return the rendered discussion content identity for update decisions."""
    if pr is None:
        return ()

    return (
        pr.body,
        tuple(
            (
                review.id,
                normalized_author_login(review.user),
                review.state.name,
                review.body,
                review.created_at,
                review.submitted_at,
            )
            for review in pr.reviews
        ),
        tuple(
            (
                comment.id,
                normalized_author_login(comment.user),
                comment.body,
                comment.created_at,
                comment.updated_at,
            )
            for comment in pr.issue_comments
        ),
        tuple(
            thread_render_signature(thread)
            for thread in pr.review_threads
        ),
    )


def thread_render_signature(thread: ReviewThread) -> tuple[object, ...]:
    return (
        thread.path,
        thread.anchor_line,
        tuple(
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
            for comment in thread.comments
        ),
    )


def normalized_author_login(user: PRUser | None) -> str:
    if user is None:
        return ""
    login = user.login
    if login.endswith("[bot]"):
        return login[: -len("[bot]")]
    return login
