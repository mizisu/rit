from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from rit.core.types import FileDiff
from rit.state.models import PR, PendingReviewComment, PRComment, PRReview, ReviewThread
from rit.state.pending_review import (
    PendingCommentSide,
    ReviewSubmissionEvent,
    _sort_key,
    is_inline_comment_diff_line,
    load_pending_review_projection,
    merge_pending_review_comments,
    merge_pending_review_drafts,
    plan_pending_review_sync,
    plan_review_submission,
    remove_pending_comment,
    upsert_pending_comment,
)
from rit.state.review_annotations import ReviewAnnotationIndex


class PendingReviewAdapter(Protocol):
    async def list_review_comments(
        self, pr_number: int, review_id: int
    ) -> list[PRComment]: ...

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None: ...

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments: list[PendingReviewComment],
        body: str | None = None,
        commit_id: str | None = None,
    ) -> PRReview: ...

    async def update_review_comment(
        self, comment_node_id: str, body: str
    ) -> PRComment: ...

    async def submit_pending_review(
        self,
        pr_number: int,
        review_id: int,
        *,
        event: ReviewSubmissionEvent,
        body: str | None = None,
    ) -> PRReview | None: ...

    async def submit_review(
        self,
        pr_number: int,
        *,
        event: ReviewSubmissionEvent,
        body: str | None = None,
        comments: list[PendingReviewComment] | None = None,
    ) -> PRReview | None: ...


type ReviewSyncObserver = Callable[[int | None, PRReview | None], None]


@dataclass(frozen=True)
class _PendingReviewSnapshot:
    review_id: int | None
    body: str
    comments: tuple[PendingReviewComment, ...]


@dataclass
class PendingReviewWorkspace:
    """Own pending review state through edits, sync, rollback, refresh, and submission."""

    review_id: int | None = None
    body: str = ""
    comments: list[PendingReviewComment] = field(default_factory=list)
    drafts_are_canonical: bool = False
    obsolete_review_ids: set[int] = field(default_factory=set)
    _revision: int = field(default=0, init=False, repr=False)
    _sync_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False
    )

    @property
    def revision(self) -> int:
        return self._revision

    def save_file_comment(self, body: str, *, path: str) -> PendingReviewComment:
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")
        if not path:
            raise ValueError("Comment file path is unavailable")
        draft = PendingReviewComment(
            body=normalized,
            path=path,
            line=0,
            side="RIGHT",
            is_diff_line=True,
            subject_type="file",
        )
        self.comments = [*self.comments, draft]
        if len(self.comments) > 1:
            self.comments.sort(key=_sort_key)
        self.drafts_are_canonical = True
        self._revision += 1
        return draft

    def save_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: PendingCommentSide,
        diff: FileDiff | None = None,
        start_line: int | None = None,
        start_side: PendingCommentSide | None = None,
        draft_index: int | None = None,
    ) -> PendingReviewComment:
        is_diff_line = is_inline_comment_diff_line(
            diff,
            line=line,
            side=side,
            start_line=start_line,
            start_side=start_side,
        )
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")
        self.comments, draft = upsert_pending_comment(
            self.comments,
            body=normalized,
            path=path,
            line=line,
            side=side,
            is_diff_line=is_diff_line,
            start_line=start_line,
            start_side=start_side,
            replace_existing=False,
            draft_index=draft_index,
        )
        self.drafts_are_canonical = True
        self._revision += 1
        return draft

    def delete_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: PendingCommentSide,
        draft_index: int | None = None,
    ) -> bool:
        comments, deleted = remove_pending_comment(
            self.comments,
            path=path,
            line=line,
            side=side,
            draft_index=draft_index,
        )
        if deleted:
            self.comments = comments
            self.drafts_are_canonical = True
            self._revision += 1
        return deleted

    def _snapshot(self) -> _PendingReviewSnapshot:
        return _PendingReviewSnapshot(
            review_id=self.review_id,
            body=self.body,
            comments=tuple(self.comments),
        )

    def _restore_if_current(
        self, snapshot: _PendingReviewSnapshot | None, rollback_if_version: int | None
    ) -> None:
        if snapshot is None or rollback_if_version != self._revision:
            return
        self.review_id = snapshot.review_id
        self.body = snapshot.body
        self.comments = list(snapshot.comments)
        self.drafts_are_canonical = bool(self.comments)
        self._revision += 1

    async def refresh(
        self,
        load_discussion: Callable[[], Awaitable[PR]],
        *,
        adapter: PendingReviewAdapter,
        pr_number: int,
    ) -> PR:
        """Load discussion without allowing a stale fetch to erase local edits."""
        expected_revision = self._revision
        pr = await load_discussion()
        projection = await load_pending_review_projection(
            pr.reviews,
            pr_number=pr_number,
            review_threads=pr.review_threads,
            list_review_comments=getattr(adapter, "list_review_comments", None),
        )
        if expected_revision != self._revision:
            return pr
        if self.comments:
            comments = merge_pending_review_drafts(self.comments, projection.comments)
            review_id = projection.review_id or self.review_id
            body = projection.body or self.body
            if (
                review_id == self.review_id
                and body == self.body
                and comments == self.comments
            ):
                return pr
        else:
            comments = list(projection.comments)
            review_id = projection.review_id
            body = projection.body
        self.review_id = review_id
        self.body = body
        self.comments = comments
        self.drafts_are_canonical = bool(comments)
        self._revision += 1
        return pr

    def prune_obsolete_review_ids(
        self, comments: Sequence[PRComment], threads: Sequence[ReviewThread]
    ) -> None:
        if not self.obsolete_review_ids:
            return
        present_ids = {
            comment.pull_request_review_id
            for comment in comments
            if comment.pull_request_review_id
        }
        present_ids.update(
            comment.pull_request_review_id
            for thread in threads
            for comment in thread.comments
            if comment.pull_request_review_id
        )
        self.obsolete_review_ids.intersection_update(present_ids)

    async def queue_file_comment(
        self,
        body: str,
        *,
        path: str,
        adapter: PendingReviewAdapter,
        pr_number: int,
        head_sha: Callable[[], str],
        on_sync: ReviewSyncObserver,
        after_local_save: Callable[[], Awaitable[None]] | None = None,
    ) -> PendingReviewComment:
        snapshot = self._snapshot()
        draft = self.save_file_comment(body, path=path)
        if after_local_save is not None:
            await after_local_save()
        await self._sync(
            adapter=adapter,
            pr_number=pr_number,
            head_sha=head_sha,
            on_sync=on_sync,
            rollback_to=snapshot,
            rollback_if_version=self._revision,
        )
        return draft

    async def queue_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: PendingCommentSide,
        adapter: PendingReviewAdapter,
        pr_number: int,
        head_sha: Callable[[], str],
        on_sync: ReviewSyncObserver,
        diff: FileDiff | None = None,
        start_line: int | None = None,
        start_side: PendingCommentSide | None = None,
        draft_index: int | None = None,
        after_local_save: Callable[[], Awaitable[None]] | None = None,
    ) -> PendingReviewComment:
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")
        removed_comment = (
            self._comment_for_sync(path, line, side, draft_index)
            if draft_index is not None
            else None
        )
        snapshot = self._snapshot()
        draft = self.save_inline_comment(
            normalized,
            path=path,
            line=line,
            side=side,
            diff=diff,
            start_line=start_line,
            start_side=start_side,
            draft_index=draft_index,
        )
        saved_version = self._revision
        if after_local_save is not None:
            await after_local_save()
        if removed_comment is not None and removed_comment.review_comment_node_id:
            try:
                async with self._sync_lock:
                    await adapter.update_review_comment(
                        removed_comment.review_comment_node_id,
                        normalized,
                    )
            except Exception:
                self._restore_if_current(snapshot, saved_version)
                raise
            return draft
        await self._sync(
            adapter=adapter,
            pr_number=pr_number,
            head_sha=head_sha,
            on_sync=on_sync,
            rollback_to=snapshot,
            rollback_if_version=self._revision,
            removed_comment=removed_comment,
        )
        return draft

    async def sync(
        self,
        *,
        adapter: PendingReviewAdapter,
        pr_number: int,
        head_sha: Callable[[], str],
        on_sync: ReviewSyncObserver,
    ) -> PRReview | None:
        """Reconcile local drafts with the verified server workspace."""
        return await self._sync(
            adapter=adapter,
            pr_number=pr_number,
            head_sha=head_sha,
            on_sync=on_sync,
        )

    async def _sync(
        self,
        *,
        adapter: PendingReviewAdapter,
        pr_number: int,
        head_sha: Callable[[], str],
        on_sync: ReviewSyncObserver,
        rollback_to: _PendingReviewSnapshot | None = None,
        rollback_if_version: int | None = None,
        removed_comment: PendingReviewComment | None = None,
    ) -> PRReview | None:
        try:
            async with self._sync_lock:
                previous_review_id = self.review_id
                replacement = await _replace_pending_review(
                    adapter=adapter,
                    pr_number=pr_number,
                    comments=list(self.comments),
                    pending_review_id=self.review_id,
                    pending_review_body=self.body,
                    head_sha=head_sha(),
                    removed_comment=removed_comment,
                )
                self.comments = replacement.comments
                self.drafts_are_canonical = bool(replacement.comments)
                review = replacement.review
                self.review_id = (review.id or None) if review is not None else None
                self.body = (review.body or self.body) if review is not None else ""
                self._revision += 1
                if previous_review_id and previous_review_id != self.review_id:
                    self.obsolete_review_ids.add(previous_review_id)
                if self.review_id:
                    self.obsolete_review_ids.discard(self.review_id)
                on_sync(previous_review_id, review)
                return review
        except Exception:
            self._restore_if_current(rollback_to, rollback_if_version)
            raise

    async def remove_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: PendingCommentSide,
        adapter: PendingReviewAdapter,
        pr_number: int,
        head_sha: Callable[[], str],
        on_sync: ReviewSyncObserver,
        draft_index: int | None = None,
        after_local_delete: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        snapshot = self._snapshot()
        removed_comment = self._comment_for_sync(path, line, side, draft_index)
        if not self.delete_inline_comment(
            path=path,
            line=line,
            side=side,
            draft_index=draft_index,
        ):
            return False
        if after_local_delete is not None:
            await after_local_delete()
        await self._sync(
            adapter=adapter,
            pr_number=pr_number,
            head_sha=head_sha,
            on_sync=on_sync,
            rollback_to=snapshot,
            rollback_if_version=self._revision,
            removed_comment=removed_comment,
        )
        return True

    async def remove_comment_at(
        self,
        draft_index: int,
        *,
        adapter: PendingReviewAdapter,
        pr_number: int,
        head_sha: Callable[[], str],
        on_sync: ReviewSyncObserver,
        after_local_delete: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        if not 0 <= draft_index < len(self.comments):
            return False
        snapshot = self._snapshot()
        removed_comment = self.comments[draft_index]
        comments = list(self.comments)
        del comments[draft_index]
        self.comments = comments
        self.drafts_are_canonical = True
        self._revision += 1
        if after_local_delete is not None:
            await after_local_delete()
        await self._sync(
            adapter=adapter,
            pr_number=pr_number,
            head_sha=head_sha,
            on_sync=on_sync,
            rollback_to=snapshot,
            rollback_if_version=self._revision,
            removed_comment=removed_comment,
        )
        return True

    def _comment_for_sync(
        self, path: str, line: int, side: PendingCommentSide, draft_index: int | None
    ) -> PendingReviewComment | None:
        return ReviewAnnotationIndex.from_parts(
            pending_comments=self.comments,
        ).pending_for_sync(path=path, line=line, side=side, draft_index=draft_index)

    async def submit(
        self,
        event: ReviewSubmissionEvent,
        body: str,
        *,
        adapter: PendingReviewAdapter,
        pr_number: int,
        remember_submitted: Callable[[PRReview | None], Awaitable[None]],
    ) -> None:
        """Submit and remember discussion before clearing drafts, without the sync lock."""
        plan = plan_review_submission(
            event,
            body,
            self.comments,
            pending_review_id=self.review_id,
        )
        if plan.uses_pending_review:
            assert plan.pending_review_id is not None
            review = await adapter.submit_pending_review(
                pr_number,
                plan.pending_review_id,
                event=plan.event,
                body=plan.body,
            )
        else:
            review = await adapter.submit_review(
                pr_number,
                event=plan.event,
                body=plan.body,
                comments=plan.comments,
            )
        await remember_submitted(review)
        self.review_id = None
        self.body = ""
        self.comments = []
        self.drafts_are_canonical = False
        self._revision += 1


@dataclass(frozen=True)
class _PendingReviewReplacement:
    review: PRReview | None
    comments: list[PendingReviewComment]


async def _replace_pending_review(
    *,
    adapter: PendingReviewAdapter,
    pr_number: int,
    comments: Sequence[PendingReviewComment],
    pending_review_id: int | None,
    pending_review_body: str,
    head_sha: str,
    removed_comment: PendingReviewComment | None = None,
) -> _PendingReviewReplacement:
    """Replace GitHub's pending review without dropping unverified server drafts."""
    replacement_comments = list(comments)
    if pending_review_id is not None:
        server_comments = await adapter.list_review_comments(
            pr_number, pending_review_id
        )
        if (
            not server_comments
            and replacement_comments
            and not any(comment.review_comment_id for comment in replacement_comments)
        ):
            raise RuntimeError(
                "Could not verify existing pending review comments; not replacing pending review"
            )
        replacement_comments = merge_pending_review_comments(
            replacement_comments,
            server_comments,
            removed_comment=removed_comment,
        )

    plan = plan_pending_review_sync(
        replacement_comments,
        pending_review_id=pending_review_id,
        pending_review_body=pending_review_body,
        head_sha=head_sha,
    )
    if plan.delete_review_id is not None:
        await adapter.delete_pending_review(pr_number, plan.delete_review_id)
    if not plan.should_create:
        return _PendingReviewReplacement(review=None, comments=replacement_comments)
    review = await adapter.create_pending_review(
        pr_number,
        comments=plan.comments,
        body=plan.body,
        commit_id=plan.commit_id,
    )
    if review.id and hasattr(adapter, "list_review_comments"):
        created_comments = await adapter.list_review_comments(pr_number, review.id)
        replacement_comments = merge_pending_review_comments(
            replacement_comments,
            created_comments,
        )
    return _PendingReviewReplacement(review=review, comments=replacement_comments)
