from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from rit.state.models import (
    PendingReviewComment,
    PRComment,
    PRReview,
    ReviewState,
    ReviewThread,
)

__all__ = (
    "pending_draft_matches_review_comment",
    "pending_review_hidden_ids",
    "review_thread_is_pending_draft",
    "visible_timeline_comments",
    "visible_timeline_reviews",
)

PendingSide = Literal["LEFT", "RIGHT"]
ThreadSide = Literal["old", "new", "auto"]


def pending_review_hidden_ids(
    *,
    pending_review_id: int | None,
    reviews: Sequence[PRReview],
    obsolete_pending_review_ids: Iterable[int] = (),
) -> tuple[int, ...]:
    """Return review ids whose comments are represented by local pending drafts."""
    ids: list[int] = []
    for review_id in obsolete_pending_review_ids:
        if review_id > 0 and review_id not in ids:
            ids.append(review_id)

    if isinstance(pending_review_id, int) and pending_review_id > 0:
        if pending_review_id not in ids:
            ids.append(pending_review_id)

    for review in reviews:
        if not _is_pending_review(review):
            continue
        review_id = review.id
        if review_id > 0 and review_id not in ids:
            ids.append(review_id)
    return tuple(ids)


def visible_timeline_reviews(
    reviews: Sequence[PRReview],
    *,
    pending_review_id: int | None,
    pending_review_body: str,
) -> list[PRReview]:
    """Return reviews including the local pending review identity when available."""
    visible = list(reviews)
    if not pending_review_id:
        return visible

    for index, review in enumerate(visible):
        if review.id != pending_review_id:
            continue
        if review.state != ReviewState.PENDING or review.body == pending_review_body:
            return visible
        visible[index] = review.model_copy(update={"body": pending_review_body})
        return visible

    visible.append(
        PRReview(
            id=pending_review_id,
            state=ReviewState.PENDING,
            body=pending_review_body,
        )
    )
    return visible


def visible_timeline_comments(
    comments: Sequence[PRComment],
    *,
    drafts: Sequence[PendingReviewComment],
    pending_review_id: int | None,
    hidden_review_ids: Sequence[int],
    reviews: Sequence[PRReview],
) -> list[PRComment]:
    """Return timeline comments with pending review drafts canonicalized."""
    hidden_ids = tuple(hidden_review_ids)
    visible: list[PRComment] = []
    for comment in comments:
        if _comment_is_hidden_pending_draft(
            comment,
            drafts=drafts,
            hidden_review_ids=hidden_ids,
            reviews=reviews,
        ):
            continue
        visible.append(comment)

    if pending_review_id is None or pending_review_id <= 0 or not drafts:
        return visible

    for index, draft in enumerate(drafts):
        if not draft.body:
            continue
        template = _timeline_template_for_draft(
            draft,
            comments,
            hidden_review_ids=hidden_ids,
        )
        visible.append(
            _timeline_comment_from_draft(
                draft,
                index=index,
                pending_review_id=pending_review_id,
                template=template,
            )
        )
    return visible


def review_thread_is_pending_draft(
    thread: ReviewThread,
    *,
    drafts: Sequence[PendingReviewComment],
    hidden_review_ids: Sequence[int],
    reviews: Sequence[PRReview],
) -> bool:
    """Return whether a raw review thread should be replaced by local drafts."""
    draft_ids = {draft.review_comment_id for draft in drafts if draft.review_comment_id}
    for comment in thread.comments:
        review_id = comment.pull_request_review_id
        if review_id in hidden_review_ids:
            return True
        if comment.id in draft_ids:
            return True
        if _is_known_submitted_review_id(reviews, review_id):
            continue
        if review_id:
            continue
        if any(
            pending_draft_matches_review_comment(draft, comment, thread=thread)
            for draft in drafts
        ):
            return True
    return False


def pending_draft_matches_review_comment(
    draft: PendingReviewComment,
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> bool:
    """Return whether a PR review comment represents the same draft."""
    comment_path = comment.path or (thread.path if thread is not None else "")
    if draft.path != comment_path or draft.body != comment.body:
        return False

    target_side = _comment_target_side(comment, thread=thread)
    if draft.anchor_side != target_side:
        return False

    anchor_line = _anchor_line_for_side(comment, target_side, thread=thread)
    if anchor_line != draft.line:
        return False

    comment_start_line = _start_line_for_side(comment, target_side, thread=thread)
    if comment_start_line == anchor_line:
        comment_start_line = None
    if draft.start_line != comment_start_line:
        return False
    if draft.start_line is None:
        return True
    return draft.start_side == _start_side_for_side(comment, target_side, thread=thread)


def _comment_is_hidden_pending_draft(
    comment: PRComment,
    *,
    drafts: Sequence[PendingReviewComment],
    hidden_review_ids: Sequence[int],
    reviews: Sequence[PRReview],
) -> bool:
    review_id = comment.pull_request_review_id
    if review_id in hidden_review_ids:
        return True

    for draft in drafts:
        if draft.review_comment_id and comment.id == draft.review_comment_id:
            return True

    if _is_known_submitted_review_id(reviews, review_id):
        return False
    if review_id:
        return False

    return any(pending_draft_matches_review_comment(draft, comment) for draft in drafts)


def _timeline_template_for_draft(
    draft: PendingReviewComment,
    comments: Sequence[PRComment],
    *,
    hidden_review_ids: Sequence[int],
) -> PRComment | None:
    if draft.review_comment_id:
        for comment in comments:
            if comment.id == draft.review_comment_id:
                return comment

    for comment in comments:
        if comment.pull_request_review_id not in hidden_review_ids:
            continue
        if pending_draft_matches_review_comment(draft, comment):
            return comment
    return None


def _timeline_comment_from_draft(
    draft: PendingReviewComment,
    *,
    index: int,
    pending_review_id: int,
    template: PRComment | None,
) -> PRComment:
    comment_id = draft.review_comment_id if draft.review_comment_id else -(index + 1)
    data: dict[str, object] = {
        "id": comment_id,
        "body": draft.body,
        "path": draft.path,
        "side": draft.side,
        "pull_request_review_id": pending_review_id,
        "start_side": draft.start_side or "",
    }
    if draft.side == "LEFT":
        data["line"] = None
        data["original_line"] = draft.line
    else:
        data["line"] = draft.line
        data["original_line"] = None

    if draft.start_line is not None:
        start_side = draft.start_side or draft.side
        data["start_side"] = start_side
        if start_side == "LEFT":
            data["original_start_line"] = draft.start_line
            data["start_line"] = None
        else:
            data["start_line"] = draft.start_line
            data["original_start_line"] = None

    if template is not None:
        return template.model_copy(update=data)
    return PRComment.model_validate(data)


def _is_pending_review(review: PRReview) -> bool:
    return review.state == ReviewState.PENDING or review.state == "PENDING"


def _is_known_submitted_review_id(
    reviews: Sequence[PRReview],
    review_id: int | None,
) -> bool:
    if not isinstance(review_id, int) or review_id <= 0:
        return False

    for review in reviews:
        if review.id == review_id:
            return not _is_pending_review(review)
    return False


def _comment_target_side(
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> ThreadSide:
    if thread is not None and thread.anchor_side != "auto":
        return thread.anchor_side
    return comment.anchor_side


def _old_anchor_line(
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if thread is not None and thread.original_line is not None:
        return thread.original_line
    return comment.original_line


def _new_anchor_line(
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if thread is not None and thread.line is not None:
        return thread.line
    return comment.line


def _anchor_line_for_side(
    comment: PRComment,
    target_side: ThreadSide,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if target_side == "old":
        old_line = _old_anchor_line(comment, thread=thread)
        if old_line is not None:
            return old_line
        return _new_anchor_line(comment, thread=thread)
    if target_side == "new":
        new_line = _new_anchor_line(comment, thread=thread)
        if new_line is not None:
            return new_line
        return _old_anchor_line(comment, thread=thread)
    if thread is not None and thread.anchor_line is not None:
        return thread.anchor_line
    return comment.anchor_line


def _start_line_for_side(
    comment: PRComment,
    target_side: ThreadSide,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if target_side == "old":
        if thread is not None and thread.original_start_line is not None:
            return thread.original_start_line
        if comment.original_start_line is not None:
            return comment.original_start_line
        if thread is not None and thread.start_line is not None:
            return thread.start_line
        return comment.start_line
    if target_side == "new":
        if thread is not None and thread.start_line is not None:
            return thread.start_line
        if comment.start_line is not None:
            return comment.start_line
        if thread is not None and thread.original_start_line is not None:
            return thread.original_start_line
        return comment.original_start_line
    if thread is not None:
        return thread.start_line or thread.original_start_line
    return comment.start_line or comment.original_start_line


def _start_side_for_side(
    comment: PRComment,
    target_side: ThreadSide,
    *,
    thread: ReviewThread | None = None,
) -> PendingSide | None:
    if thread is not None:
        if thread.start_diff_side == "LEFT":
            return "LEFT"
        if thread.start_diff_side == "RIGHT":
            return "RIGHT"
    if comment.start_side == "LEFT":
        return "LEFT"
    if comment.start_side == "RIGHT":
        return "RIGHT"
    if target_side == "old":
        return "LEFT"
    if target_side == "new":
        return "RIGHT"
    return None
