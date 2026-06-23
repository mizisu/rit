from __future__ import annotations

from collections.abc import Awaitable, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from rit.core.types import FileDiff
from rit.state.models import PendingReviewComment, PRComment, PRReview, ReviewState


PendingCommentSide = Literal["LEFT", "RIGHT"]
ReviewSubmissionEvent = Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"]


__all__ = (
    "InlineCommentSubmissionPlan",
    "PendingCommentDeleteResult",
    "PendingCommentSaveResult",
    "PendingCommentSide",
    "PendingReviewApplication",
    "PendingReviewClearResult",
    "PendingReviewProjection",
    "PendingReviewRestoration",
    "PendingReviewSnapshot",
    "PendingReviewSyncApplication",
    "PendingReviewSyncPlan",
    "PendingReviewSyncResult",
    "ReviewCommentsLoader",
    "ReviewSubmissionEvent",
    "ReviewSubmissionPlan",
    "UnsupportedInlineCommentTarget",
    "apply_pending_review_projection",
    "apply_pending_review_sync_result",
    "clear_pending_review",
    "delete_pending_comment",
    "first_unsupported_comment",
    "get_pending_file_comments",
    "get_pending_inline_comment",
    "is_inline_comment_diff_line",
    "load_pending_review_projection",
    "plan_inline_comment_submission",
    "plan_pending_review_sync",
    "plan_review_submission",
    "project_pending_review",
    "project_pending_review_sync_result",
    "remove_pending_comment",
    "require_inline_comment_diff_line",
    "restore_pending_review_snapshot",
    "save_pending_comment",
    "select_pending_review",
    "should_restore_pending_review_snapshot",
    "snapshot_pending_review",
    "syncable_comments",
    "upsert_pending_comment",
)


class ReviewCommentsLoader(Protocol):
    def __call__(
        self,
        pr_number: int,
        review_id: int,
    ) -> Awaitable[Sequence[PRComment]]: ...


@dataclass(frozen=True)
class ReviewSubmissionPlan:
    """Validated review submission inputs for the GitHub adapter."""

    event: ReviewSubmissionEvent
    body: str | None
    comments: list[PendingReviewComment]
    pending_review_id: int | None

    @property
    def uses_pending_review(self) -> bool:
        return self.pending_review_id is not None


@dataclass(frozen=True)
class InlineCommentSubmissionPlan:
    """Validated single inline comment inputs for the GitHub adapter."""

    body: str
    commit_id: str
    side: PendingCommentSide


@dataclass(frozen=True)
class PendingReviewSyncPlan:
    """Validated pending-review replacement inputs for the GitHub adapter."""

    delete_review_id: int | None
    comments: list[PendingReviewComment]
    body: str | None
    commit_id: str | None

    @property
    def should_create(self) -> bool:
        return bool(self.comments)


@dataclass(frozen=True)
class PendingReviewProjection:
    """Store-ready local state for a GitHub pending review."""

    review_id: int | None
    body: str
    comments: list[PendingReviewComment]


@dataclass(frozen=True)
class PendingReviewApplication:
    """Store-ready state after applying a pending-review projection."""

    review_id: int | None
    body: str
    comments: list[PendingReviewComment]
    version: int


@dataclass(frozen=True)
class PendingReviewSnapshot:
    """Rollback snapshot for optimistic pending-review edits."""

    pending_review_id: int | None
    pending_review_body: str
    pending_review_comments: tuple[PendingReviewComment, ...]
    version: int


@dataclass(frozen=True)
class PendingReviewRestoration:
    """Store-ready state restored from a pending-review snapshot."""

    pending_review_id: int | None
    pending_review_body: str
    pending_review_comments: list[PendingReviewComment]
    version: int


@dataclass(frozen=True)
class PendingReviewSyncResult:
    """Store-ready pending-review identity after a sync attempt."""

    pending_review_id: int | None
    pending_review_body: str


@dataclass(frozen=True)
class PendingReviewSyncApplication:
    """Store-ready state after applying a pending-review sync result."""

    pending_review_id: int | None
    pending_review_body: str
    version: int


@dataclass(frozen=True)
class PendingCommentSaveResult:
    """Store-ready state after saving a local pending inline comment."""

    comments: list[PendingReviewComment]
    draft: PendingReviewComment
    version: int


@dataclass(frozen=True)
class PendingCommentDeleteResult:
    """Store-ready state after deleting a local pending inline comment."""

    comments: list[PendingReviewComment]
    deleted: bool
    version: int


@dataclass(frozen=True)
class PendingReviewClearResult:
    """Store-ready state after clearing local pending-review drafts."""

    pending_review_id: int | None
    pending_review_body: str
    pending_review_comments: list[PendingReviewComment]
    version: int


class UnsupportedInlineCommentTarget(ValueError):
    """Raised when GitHub cannot create a true line comment for a target."""

    def __init__(self, *, path: str, line: int, side: PendingCommentSide):
        super().__init__(
            f"GitHub cannot create a line comment on {path}:{line} "
            f"({side}) because it is outside the PR diff."
        )

    @classmethod
    def from_comment(
        cls,
        comment: PendingReviewComment,
    ) -> UnsupportedInlineCommentTarget:
        return cls(path=comment.path, line=comment.line, side=comment.side)


def upsert_pending_comment(
    comments: Sequence[PendingReviewComment],
    *,
    body: str,
    path: str,
    line: int,
    side: PendingCommentSide,
    is_diff_line: bool,
) -> tuple[list[PendingReviewComment], PendingReviewComment]:
    """Return comments with one draft added or replaced for an anchor."""
    draft = PendingReviewComment(
        body=body,
        path=path,
        line=line,
        side=side,
        is_diff_line=is_diff_line,
    )
    updated = list(comments)
    for index, existing in enumerate(updated):
        if _same_anchor(existing, path=path, line=line, side=side):
            updated[index] = draft
            break
    else:
        updated.append(draft)
    updated.sort(key=_sort_key)
    return updated, draft


def remove_pending_comment(
    comments: Sequence[PendingReviewComment],
    *,
    path: str,
    line: int,
    side: PendingCommentSide,
) -> tuple[list[PendingReviewComment], bool]:
    """Return comments with a matching draft removed."""
    updated = list(comments)
    for index, draft in enumerate(updated):
        if _same_anchor(draft, path=path, line=line, side=side):
            del updated[index]
            return updated, True
    return updated, False


def get_pending_inline_comment(
    comments: Iterable[PendingReviewComment],
    *,
    path: str,
    line: int,
    side: PendingCommentSide,
) -> PendingReviewComment | None:
    for draft in comments:
        if _same_anchor(draft, path=path, line=line, side=side):
            return draft
    return None


def get_pending_file_comments(
    comments: Iterable[PendingReviewComment],
    filename: str,
) -> list[PendingReviewComment]:
    return [draft for draft in comments if draft.path == filename]


def syncable_comments(
    comments: Iterable[PendingReviewComment],
) -> list[PendingReviewComment]:
    return [comment for comment in comments if comment.is_diff_line]


def first_unsupported_comment(
    comments: Iterable[PendingReviewComment],
) -> PendingReviewComment | None:
    return next((comment for comment in comments if not comment.is_diff_line), None)


def save_pending_comment(
    comments: Sequence[PendingReviewComment],
    *,
    body: str,
    path: str,
    line: int,
    side: PendingCommentSide,
    is_diff_line: bool,
    current_version: int,
) -> PendingCommentSaveResult:
    """Return local draft state after saving one pending inline comment."""
    normalized = body.strip()
    if not normalized:
        raise ValueError("Comment cannot be empty")

    updated, draft = upsert_pending_comment(
        comments,
        body=normalized,
        path=path,
        line=line,
        side=side,
        is_diff_line=is_diff_line,
    )
    return PendingCommentSaveResult(
        comments=updated,
        draft=draft,
        version=current_version + 1,
    )


def delete_pending_comment(
    comments: Sequence[PendingReviewComment],
    *,
    path: str,
    line: int,
    side: PendingCommentSide,
    current_version: int,
) -> PendingCommentDeleteResult:
    """Return local draft state after deleting one pending inline comment."""
    updated, deleted = remove_pending_comment(
        comments,
        path=path,
        line=line,
        side=side,
    )
    return PendingCommentDeleteResult(
        comments=updated,
        deleted=deleted,
        version=current_version + 1 if deleted else current_version,
    )


def snapshot_pending_review(
    *,
    pending_review_id: int | None,
    pending_review_body: str,
    pending_review_comments: Sequence[PendingReviewComment],
    version: int,
) -> PendingReviewSnapshot:
    """Return an immutable rollback snapshot for pending-review state."""
    return PendingReviewSnapshot(
        pending_review_id=pending_review_id,
        pending_review_body=pending_review_body,
        pending_review_comments=tuple(pending_review_comments),
        version=version,
    )


def restore_pending_review_snapshot(
    snapshot: PendingReviewSnapshot,
    *,
    current_version: int,
) -> PendingReviewRestoration:
    """Return mutable pending-review state restored from a snapshot."""
    return PendingReviewRestoration(
        pending_review_id=snapshot.pending_review_id,
        pending_review_body=snapshot.pending_review_body,
        pending_review_comments=list(snapshot.pending_review_comments),
        version=current_version + 1,
    )


def should_restore_pending_review_snapshot(
    snapshot: PendingReviewSnapshot | None,
    *,
    rollback_if_version: int | None,
    current_version: int,
) -> bool:
    """Return whether a failed sync may restore an optimistic snapshot."""
    return (
        snapshot is not None
        and rollback_if_version is not None
        and current_version == rollback_if_version
    )


def project_pending_review_sync_result(
    review: PRReview | None,
    *,
    current_body: str,
) -> PendingReviewSyncResult:
    """Return local pending-review state after replacing a draft review."""
    if review is None:
        return PendingReviewSyncResult(
            pending_review_id=None,
            pending_review_body="",
        )

    return PendingReviewSyncResult(
        pending_review_id=review.id if review.id else None,
        pending_review_body=review.body or current_body,
    )


def apply_pending_review_sync_result(
    result: PendingReviewSyncResult,
    *,
    current_version: int,
) -> PendingReviewSyncApplication:
    """Return local pending-review identity after installing a sync result."""
    return PendingReviewSyncApplication(
        pending_review_id=result.pending_review_id,
        pending_review_body=result.pending_review_body,
        version=current_version + 1,
    )


def clear_pending_review(*, current_version: int) -> PendingReviewClearResult:
    """Return empty pending-review draft state after a submit."""
    return PendingReviewClearResult(
        pending_review_id=None,
        pending_review_body="",
        pending_review_comments=[],
        version=current_version + 1,
    )


def plan_review_submission(
    event: ReviewSubmissionEvent,
    body: str,
    comments: Sequence[PendingReviewComment],
    *,
    pending_review_id: int | None,
) -> ReviewSubmissionPlan:
    """Return validated review submission data."""
    normalized_body = body.strip()
    pending_comments = list(comments)
    unsupported_comment = first_unsupported_comment(pending_comments)
    if unsupported_comment is not None:
        raise UnsupportedInlineCommentTarget.from_comment(unsupported_comment)

    if event == "REQUEST_CHANGES" and not normalized_body:
        raise ValueError("Review body cannot be empty")
    if event == "COMMENT" and not normalized_body and not pending_comments:
        raise ValueError("Review body cannot be empty")

    return ReviewSubmissionPlan(
        event=event,
        body=normalized_body or None,
        comments=pending_comments,
        pending_review_id=pending_review_id,
    )


def select_pending_review(reviews: Sequence[PRReview]) -> PRReview | None:
    """Return the newest pending review from a PR review list."""
    return next(
        (
            review
            for review in reversed(reviews)
            if review.state == ReviewState.PENDING
        ),
        None,
    )


def project_pending_review(
    review: PRReview | None,
    review_comments: Iterable[PRComment] = (),
) -> PendingReviewProjection:
    """Return local pending-review draft state from GitHub review data."""
    if review is None:
        return PendingReviewProjection(review_id=None, body="", comments=[])

    return PendingReviewProjection(
        review_id=review.id or None,
        body=review.body,
        comments=_pending_comments_from_review_comments(review_comments),
    )


async def load_pending_review_projection(
    reviews: Sequence[PRReview],
    *,
    pr_number: int,
    list_review_comments: ReviewCommentsLoader | None = None,
) -> PendingReviewProjection:
    """Return pending-review state after loading any review comments."""
    review = select_pending_review(reviews)
    review_comments: Sequence[PRComment] = ()

    if (
        review is not None
        and review.id is not None
        and list_review_comments is not None
    ):
        try:
            review_comments = await list_review_comments(pr_number, review.id)
        except RuntimeError:
            review_comments = ()

    return project_pending_review(review, review_comments)


def apply_pending_review_projection(
    projection: PendingReviewProjection,
    *,
    current_version: int,
) -> PendingReviewApplication:
    """Return local pending-review state after installing a server projection."""
    return PendingReviewApplication(
        review_id=projection.review_id,
        body=projection.body,
        comments=list(projection.comments),
        version=current_version + 1,
    )


def plan_pending_review_sync(
    comments: Sequence[PendingReviewComment],
    *,
    pending_review_id: int | None,
    pending_review_body: str,
    head_sha: str,
) -> PendingReviewSyncPlan:
    """Return pending-review replacement data for the GitHub adapter."""
    return PendingReviewSyncPlan(
        delete_review_id=pending_review_id,
        comments=syncable_comments(comments),
        body=pending_review_body or None,
        commit_id=head_sha or None,
    )


def plan_inline_comment_submission(
    body: str,
    *,
    head_sha: str,
    diff: FileDiff | None,
    path: str,
    line: int,
    side: str,
) -> InlineCommentSubmissionPlan:
    """Return validated single inline comment submission data."""
    normalized_body = body.strip()
    if not normalized_body:
        raise ValueError("Comment cannot be empty")
    if not head_sha:
        raise ValueError("PR head SHA is unavailable")

    target_side = _pending_comment_side(side)
    if target_side is None:
        raise ValueError(f"Unsupported inline comment side: {side}")

    require_inline_comment_diff_line(
        diff,
        path=path,
        line=line,
        side=target_side,
    )
    return InlineCommentSubmissionPlan(
        body=normalized_body,
        commit_id=head_sha,
        side=target_side,
    )


def is_inline_comment_diff_line(
    diff: FileDiff | None,
    *,
    line: int,
    side: PendingCommentSide,
) -> bool:
    """Return whether a target can be sent as a GitHub inline diff comment."""
    if diff is None:
        return True

    for hunk in diff.hunks:
        for diff_line in hunk.lines:
            if side == "RIGHT" and diff_line.new_line_no == line:
                return True
            if side == "LEFT" and diff_line.old_line_no == line:
                return True
    return False


def require_inline_comment_diff_line(
    diff: FileDiff | None,
    *,
    path: str,
    line: int,
    side: PendingCommentSide,
) -> None:
    """Raise when GitHub cannot create a line comment at the target."""
    if is_inline_comment_diff_line(diff, line=line, side=side):
        return
    raise UnsupportedInlineCommentTarget(path=path, line=line, side=side)


def _same_anchor(
    comment: PendingReviewComment,
    *,
    path: str,
    line: int,
    side: PendingCommentSide,
) -> bool:
    return comment.path == path and comment.line == line and comment.side == side


def _sort_key(comment: PendingReviewComment) -> tuple[str, int, str]:
    return (comment.path, comment.line, comment.side)


def _pending_comments_from_review_comments(
    review_comments: Iterable[PRComment],
) -> list[PendingReviewComment]:
    pending_comments: list[PendingReviewComment] = []
    for comment in review_comments:
        side = _pending_comment_side(comment.side)
        anchor_line = comment.anchor_line
        if side is None or not comment.path or anchor_line is None:
            continue
        pending_comments.append(
            PendingReviewComment(
                body=comment.body,
                path=comment.path,
                line=anchor_line,
                side=side,
            )
        )
    pending_comments.sort(key=_sort_key)
    return pending_comments


def _pending_comment_side(side: str) -> PendingCommentSide | None:
    if side == "LEFT":
        return "LEFT"
    if side == "RIGHT":
        return "RIGHT"
    return None
