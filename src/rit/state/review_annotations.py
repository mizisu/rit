from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rit.state.models import PendingReviewComment, ReviewThread

__all__ = (
    "PendingDraftRef",
    "ReviewAnnotationIndex",
)

PendingSide = Literal["LEFT", "RIGHT"]


@dataclass(frozen=True)
class PendingDraftRef:
    """Indexed pending draft used by review annotation readers."""

    index: int
    comment: PendingReviewComment


@dataclass(frozen=True)
class ReviewAnnotationIndex:
    """Read model for review threads and pending review drafts."""

    pending_drafts: tuple[PendingDraftRef, ...]
    review_threads: tuple[ReviewThread, ...] = ()

    @classmethod
    def from_parts(
        cls,
        *,
        pending_comments: Sequence[PendingReviewComment],
        review_threads: Sequence[ReviewThread] = (),
    ) -> ReviewAnnotationIndex:
        return cls(
            pending_drafts=tuple(
                PendingDraftRef(index, comment)
                for index, comment in enumerate(pending_comments)
            ),
            review_threads=tuple(review_threads),
        )

    def pending_for_file(self, filename: str) -> list[PendingReviewComment]:
        return [
            ref.comment for ref in self.pending_drafts if ref.comment.path == filename
        ]

    def count_pending_for_file(self, filename: str) -> int:
        return sum(1 for ref in self.pending_drafts if ref.comment.path == filename)

    def pending_index(
        self,
        *,
        path: str,
        line: int,
        side: PendingSide,
    ) -> int | None:
        ref = self.pending_ref(path=path, line=line, side=side)
        return ref.index if ref is not None else None

    def index_for_comment(self, draft: PendingReviewComment | None) -> int | None:
        if draft is None:
            return None
        for ref in self.pending_drafts:
            if ref.comment is draft:
                return ref.index
        for ref in self.pending_drafts:
            if ref.comment == draft:
                return ref.index
        return None

    def pending_for_sync(
        self,
        *,
        path: str,
        line: int,
        side: PendingSide,
        draft_index: int | None,
    ) -> PendingReviewComment | None:
        if draft_index is not None:
            ref = self._pending_ref_at(draft_index)
            if ref is not None and _same_anchor(
                ref.comment, path=path, line=line, side=side
            ):
                return ref.comment
        ref = self.pending_ref(path=path, line=line, side=side)
        return ref.comment if ref is not None else None

    def pending_ref(
        self,
        *,
        path: str,
        line: int,
        side: PendingSide,
    ) -> PendingDraftRef | None:
        return next(
            (
                ref
                for ref in self.pending_drafts
                if _same_anchor(ref.comment, path=path, line=line, side=side)
            ),
            None,
        )

    def _pending_ref_at(self, draft_index: int) -> PendingDraftRef | None:
        if draft_index < 0:
            return None
        for ref in self.pending_drafts:
            if ref.index == draft_index:
                return ref
        return None


def _same_anchor(
    comment: PendingReviewComment,
    *,
    path: str,
    line: int,
    side: PendingSide,
) -> bool:
    return comment.path == path and comment.line == line and comment.side == side
