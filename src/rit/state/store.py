from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal

from textual.message import Message

from rit.core.types import FileDiff
from rit.services import GitHubError, GitHubService
from rit.state.discussion_projection import (
    PRDiscussion,
    RecentDiscussion,
    forget_review_comment,
    project_discussion_state,
    remember_submitted_comment,
    remember_submitted_review,
    remember_updated_comment,
    replace_review_comment,
    update_thread_resolution,
)
from rit.state.file_collection import (
    apply_file_view_state,
    apply_file_view_states,
    cache_file_diff,
    find_file,
    load_file_diff,
    sync_file_comments,
)
from rit.state.file_collection import (
    select_file as project_file_selection,
)
from rit.state.file_content import load_cached_file_content
from rit.state.file_ingest import append_file_summaries
from rit.state.file_projection import (
    diff_from_file_patch,
    parse_file_patch_summaries,
)
from rit.state.file_workspace import load_file_workspace
from rit.state.issue_comments import (
    apply_submitted_issue_comment,
    normalize_issue_comment_body,
)
from rit.state.models import (
    PR,
    FileViewedState,
    LoadingState,
    PendingReviewComment,
    PRComment,
    PRFile,
    PRIssueComment,
    PRReview,
    PRTeam,
    PRUser,
    ReviewState,
    ReviewThread,
    ReviewThreadInfo,
)
from rit.state.pending_review import (
    UnsupportedInlineCommentTarget,
    plan_inline_comment_submission,
)
from rit.state.pending_review import (
    get_pending_inline_comment as find_pending_inline_comment,
)
from rit.state.pending_review import (
    is_inline_comment_diff_line as is_pending_inline_comment_diff_line,
)
from rit.state.pending_review_visibility import (
    pending_draft_matches_review_comment,
    pending_review_hidden_ids,
    review_thread_is_pending_draft,
    visible_timeline_comments,
    visible_timeline_reviews,
)
from rit.state.pending_review_workspace import PendingReviewWorkspace
from rit.state.pr_management import plan_assignee_selection, plan_reviewer_selection
from rit.state.pr_merge import merge_pr_discussion, merge_pr_summary
from rit.state.review_annotations import ReviewAnnotationIndex

__all__ = (
    "GitHubError",
    "PRStore",
    "PRStoreState",
    "UnsupportedInlineCommentTarget",
)


@dataclass
class PRStoreState:
    pr_loading: LoadingState = LoadingState.IDLE
    files_loading: LoadingState = LoadingState.IDLE

    pr: PR | None = None
    files: list[PRFile] = field(default_factory=list)
    files_by_filename: dict[str, PRFile] = field(default_factory=dict)
    comments: list[PRComment] = field(default_factory=list)
    reviews: list[PRReview] = field(default_factory=list)
    issue_comments: list[PRIssueComment] = field(default_factory=list)
    review_threads: list[ReviewThread] = field(default_factory=list)
    pending_review: PendingReviewWorkspace = field(
        default_factory=PendingReviewWorkspace
    )

    file_diffs: dict[str, FileDiff] = field(default_factory=dict)
    comments_by_file: dict[str, list[PRComment]] = field(default_factory=dict)
    thread_info_cache: dict[int, ReviewThreadInfo] = field(default_factory=dict)
    thread_cache: dict[int, ReviewThread] = field(default_factory=dict)

    file_contents: dict[str, str] = field(default_factory=dict)

    selected_file: str | None = None
    files_loaded_count: int = 0
    files_total_count: int = 0

    error: str | None = None


class PRStore:
    """Central store for PR data with reactive updates via Textual Messages."""

    @dataclass
    class PRLoaded(Message):
        pr: PR

    @dataclass
    class FilesLoaded(Message):
        files: list[PRFile]
        loaded_count: int = 0
        total_count: int = 0

    @dataclass
    class FileSelected(Message):
        filename: str
        diff: FileDiff | None = None

    @dataclass
    class CommentsLoaded(Message):
        comments: list[PRComment]

    @dataclass
    class LoadingProgress(Message):
        current: int
        total: int
        description: str = "Loading files"

    @dataclass
    class ReviewsLoaded(Message):
        reviews: list[PRReview]

    @dataclass
    class IssueCommentsLoaded(Message):
        comments: list[PRIssueComment]

    @dataclass
    class ThreadsLoaded(Message):
        threads: dict[int, ReviewThreadInfo]

    @dataclass
    class ThreadResolved(Message):
        thread_id: str
        root_comment_id: int
        is_resolved: bool

    @dataclass
    class PRDiscussionLoaded(Message):
        pr: PR

    @dataclass
    class PRDiscussionMetadataLoaded(Message):
        pr: PR

    @dataclass
    class ErrorOccurred(Message):
        error: str
        source: str = "unknown"

    def __init__(
        self,
        owner: str | None = None,
        repo: str | None = None,
        pr_number: int = 0,
    ) -> None:
        self.pr_number = pr_number
        self._service = GitHubService(owner=owner, repo=repo)
        self._state = PRStoreState()
        self.viewed_files = ViewedFiles(self._state, self._persist_file_viewed)
        self._message_sink: Callable[[Message], None] | None = None
        self._recent_discussion = RecentDiscussion()

    @property
    def state(self) -> PRStoreState:
        return self._state

    def set_message_sink(self, sink: Callable[[Message], None]) -> None:
        self._message_sink = sink

    def _post_message(self, message: Message) -> None:
        if self._message_sink is not None:
            self._message_sink(message)

    async def load_overview(self) -> None:
        """Load PR summary and discussion without fetching file patches."""
        await asyncio.gather(
            self.load_pr_summary(),
            self.load_pr_discussion(),
            return_exceptions=True,
        )

    async def load_all(self) -> None:
        """Load PR metadata and files concurrently."""
        await asyncio.gather(
            self.load_overview(),
            self.load_files(),
            return_exceptions=True,
        )

    async def load_pr_summary(self) -> None:
        self._state.pr_loading = LoadingState.LOADING
        try:
            summary = await self._service.get_pr_summary(self.pr_number)
            pr = self._merge_pr_summary(summary)
            self._state.pr = pr
            self._state.pr_loading = LoadingState.LOADED
            self._state.files_total_count = pr.changed_files
            self._post_message(self.PRLoaded(pr=pr))
        except RuntimeError as e:
            self._state.pr_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(
                self.ErrorOccurred(error=str(e), source="load_pr_summary")
            )

    async def load_pr_discussion(self) -> None:
        async def load_discussion() -> PR:
            discussion = await self._service.get_pr_discussion(self.pr_number)
            pr = self._merge_pr_discussion(discussion)
            self._apply_discussion_state(pr)
            self._post_discussion_messages(pr)
            return pr

        try:
            pr = await self._state.pending_review.refresh(
                load_discussion,
                adapter=self._service,
                pr_number=self.pr_number,
            )
            self._post_discussion_metadata_messages(pr)
        except RuntimeError as e:
            self._state.error = str(e)
            self._post_message(
                self.ErrorOccurred(error=str(e), source="load_pr_discussion")
            )

    def _post_discussion_messages(self, pr: PR) -> None:
        self._post_message(self.PRDiscussionLoaded(pr=pr))
        self._post_discussion_detail_messages()

    def _post_discussion_detail_messages(self) -> None:
        self._post_message(self.CommentsLoaded(comments=self._state.comments))
        self._post_message(self.ReviewsLoaded(reviews=self._state.reviews))
        self._post_message(
            self.IssueCommentsLoaded(comments=self._state.issue_comments)
        )
        self._post_message(self.ThreadsLoaded(threads=self._state.thread_info_cache))

    def _post_discussion_metadata_messages(self, pr: PR) -> None:
        self._post_message(self.PRDiscussionMetadataLoaded(pr=pr))
        self._post_message(self.ThreadsLoaded(threads=self._state.thread_info_cache))

    async def _load_pr_data(self) -> None:
        self._state.pr_loading = LoadingState.LOADING

        async def load_discussion() -> PR:
            pr = await self._service.get_pr_all(self.pr_number)
            self._state.pr = pr
            self._state.files_total_count = pr.changed_files
            self._apply_discussion_state(pr)
            self._state.pr_loading = LoadingState.LOADED
            return pr

        try:
            pr = await self._state.pending_review.refresh(
                load_discussion,
                adapter=self._service,
                pr_number=self.pr_number,
            )

            self._post_message(self.PRLoaded(pr=pr))
            self._post_discussion_detail_messages()

        except RuntimeError as e:
            self._state.pr_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(self.ErrorOccurred(error=str(e), source="load_pr_data"))

    async def load_files(self) -> None:
        """Load changed files quickly, falling back to raw diff streaming."""
        error = await load_file_workspace(
            self._state,
            pr_number=self.pr_number,
            source=self._service,
            parse_summaries=self._append_raw_diff_section_summaries,
            on_progress=self._post_files_loaded,
        )
        if error is not None:
            self._mark_files_load_error(error)

    def _mark_files_load_error(self, error: str) -> None:
        self._state.files_loading = LoadingState.ERROR
        self._state.error = error
        self._post_message(self.ErrorOccurred(error=error, source="load_files"))

    async def _append_raw_diff_section_summaries(self, sections: list[str]) -> int:
        summaries = await asyncio.to_thread(parse_file_patch_summaries, sections)
        return append_file_summaries(self._state, summaries)

    def _post_files_loaded(self) -> None:
        self._post_message(
            self.LoadingProgress(
                current=self._state.files_loaded_count,
                total=self._state.files_total_count,
            )
        )
        self._post_message(
            self.FilesLoaded(
                files=list(self._state.files),
                loaded_count=self._state.files_loaded_count,
                total_count=self._state.files_total_count,
            )
        )

    def _merge_pr_summary(self, summary: PR) -> PR:
        return merge_pr_summary(
            summary,
            existing=self._state.pr,
            reviews=self._state.reviews,
            issue_comments=self._state.issue_comments,
            review_threads=self._state.review_threads,
        )

    def _merge_pr_discussion(self, discussion: PRDiscussion) -> PR:
        merged_pr = merge_pr_discussion(
            existing=self._state.pr,
            pr_number=self.pr_number,
            body=discussion.body,
            reviews=discussion.reviews,
            issue_comments=discussion.issue_comments,
            review_threads=discussion.review_threads,
        )
        self._state.pr = merged_pr
        return merged_pr

    def _apply_discussion_state(self, pr: PR) -> None:
        projection = project_discussion_state(
            pr,
            recent=self._recent_discussion,
        )
        self._state.pr = projection.pr
        self._state.reviews = projection.reviews
        self._state.issue_comments = projection.issue_comments
        self._state.review_threads = projection.review_threads
        self._state.comments = projection.comments
        self._state.comments_by_file = projection.comments_by_file

        sync_file_comments(self._state.files, projection.comments_by_file)
        self._state.thread_info_cache = projection.thread_info_cache
        self._state.thread_cache = projection.thread_cache
        self._state.pending_review.prune_obsolete_review_ids(
            self._state.comments,
            self._state.review_threads,
        )

    def select_file(self, filename: str) -> None:
        selection = project_file_selection(
            filename,
            files=self._state.files,
            files_by_filename=self._state.files_by_filename,
            file_diffs=self._state.file_diffs,
        )
        if selection is None:
            return

        self._state.selected_file = selection.filename
        self._post_message(
            self.FileSelected(
                filename=selection.filename,
                diff=selection.diff,
            )
        )

    def get_file_diff(self, filename: str) -> FileDiff | None:
        return load_file_diff(
            filename,
            files=self._state.files,
            files_by_filename=self._state.files_by_filename,
            file_diffs=self._state.file_diffs,
            parse=diff_from_file_patch,
        )

    async def get_file_diff_async(self, filename: str) -> FileDiff | None:
        cached = self._state.file_diffs.get(filename)
        if cached is not None:
            return cached

        file = self._get_file(filename)
        if file is None:
            return None

        diff = await asyncio.to_thread(diff_from_file_patch, file)
        return cache_file_diff(filename, self._state.file_diffs, diff)

    def _get_file(self, filename: str) -> PRFile | None:
        return find_file(
            filename,
            self._state.files,
            self._state.files_by_filename,
        )

    async def get_file_content(self, filename: str) -> str | None:
        """Fetch full file content at the PR's head ref. Cached after first call."""
        pr = self._state.pr
        head_sha = pr.head_sha if pr is not None else ""
        return await load_cached_file_content(
            self._state.file_contents,
            filename=filename,
            head_sha=head_sha,
            fetch=getattr(self._service, "get_file_content", None),
        )

    async def get_reviewer_candidates(self) -> tuple[list[PRUser], list[PRTeam]]:
        """Fetch user and team candidates for review requests."""
        return await self._service.get_reviewer_candidates()

    async def get_assignee_candidates(self) -> list[PRUser]:
        """Fetch users that can be assigned to this PR."""
        return await self._service.get_assignee_candidates()

    async def set_requested_reviewers(
        self,
        *,
        users: Iterable[str],
        teams: Iterable[str],
    ) -> bool:
        """Set requested reviewers to the provided user and team selections."""
        pr = self._state.pr
        if pr is None:
            raise ValueError("PR not loaded")

        plan = plan_reviewer_selection(pr, users=users, teams=teams)
        if not plan.has_changes:
            return False

        if plan.remove_users or plan.remove_teams:
            await self._service.remove_requested_reviewers(
                self.pr_number,
                reviewers=list(plan.remove_users),
                team_reviewers=list(plan.remove_teams),
            )
        if plan.add_users or plan.add_teams:
            await self._service.request_reviewers(
                self.pr_number,
                reviewers=list(plan.add_users),
                team_reviewers=list(plan.add_teams),
            )

        await self.load_pr_summary()
        return True

    async def set_assignees(self, logins: Iterable[str]) -> bool:
        """Set PR assignees to the provided user logins."""
        pr = self._state.pr
        if pr is None:
            raise ValueError("PR not loaded")

        plan = plan_assignee_selection(pr, logins)
        if not plan.has_changes:
            return False

        if plan.remove_logins:
            await self._service.remove_assignees(
                self.pr_number,
                list(plan.remove_logins),
            )
        if plan.add_logins:
            await self._service.add_assignees(self.pr_number, list(plan.add_logins))

        await self.load_pr_summary()
        return True

    async def submit_issue_comment(self, body: str) -> PRIssueComment:
        """Submit a PR-level comment and update local state."""
        normalized = normalize_issue_comment_body(body)
        comment = await self._service.create_issue_comment(self.pr_number, normalized)
        projection = apply_submitted_issue_comment(
            pr=self._state.pr,
            comments=self._state.issue_comments,
            comment=comment,
        )
        self._state.pr = projection.pr
        self._state.issue_comments = projection.issue_comments
        self._post_message(
            self.IssueCommentsLoaded(comments=self._state.issue_comments)
        )
        return comment

    async def submit_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: str,
        start_line: int | None = None,
        start_side: Literal["LEFT", "RIGHT"] | None = None,
    ) -> PRComment:
        """Submit a single inline comment on the current diff line."""
        pr = self._state.pr
        plan = plan_inline_comment_submission(
            body,
            head_sha=pr.head_sha if pr is not None else "",
            diff=self._state.file_diffs.get(path),
            path=path,
            line=line,
            side=side,
            start_line=start_line,
            start_side=start_side,
        )

        if plan.start_line is not None:
            comment = await self._service.create_review_comment(
                self.pr_number,
                body=plan.body,
                commit_id=plan.commit_id,
                path=path,
                line=line,
                side=plan.side,
                start_line=plan.start_line,
                start_side=plan.start_side or plan.side,
            )
        else:
            comment = await self._service.create_review_comment(
                self.pr_number,
                body=plan.body,
                commit_id=plan.commit_id,
                path=path,
                line=line,
                side=plan.side,
            )
        self._remember_submitted_comment(comment)
        return comment

    async def submit_file_comment(self, body: str, *, path: str) -> PRComment:
        """Submit a review comment targeting an entire changed file."""
        normalized = normalize_issue_comment_body(body)
        if not path:
            raise ValueError("Comment file path is unavailable")

        pr = self._state.pr
        commit_id = pr.head_sha if pr is not None else ""
        if not commit_id:
            raise ValueError("PR head SHA is unavailable")

        comment = await self._service.create_file_comment(
            self.pr_number,
            body=normalized,
            commit_id=commit_id,
            path=path,
        )
        self._remember_submitted_comment(comment)
        return comment

    def save_pending_file_comment(
        self,
        body: str,
        *,
        path: str,
    ) -> PendingReviewComment:
        return self._state.pending_review.save_file_comment(body, path=path)

    def save_pending_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        start_line: int | None = None,
        start_side: Literal["LEFT", "RIGHT"] | None = None,
        draft_index: int | None = None,
    ) -> PendingReviewComment:
        return self._state.pending_review.save_inline_comment(
            body,
            path=path,
            line=line,
            side=side,
            diff=self._state.file_diffs.get(path),
            start_line=start_line,
            start_side=start_side,
            draft_index=draft_index,
        )

    def review_annotations(self) -> ReviewAnnotationIndex:
        return ReviewAnnotationIndex.from_parts(
            pending_comments=self._state.pending_review.comments,
            review_threads=self._state.review_threads,
        )

    def get_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> PendingReviewComment | None:
        return find_pending_inline_comment(
            self._state.pending_review.comments,
            path=path,
            line=line,
            side=side,
        )

    def get_pending_file_comments(self, filename: str) -> list[PendingReviewComment]:
        return self.review_annotations().pending_for_file(filename)

    def pending_review_hidden_ids(self) -> tuple[int, ...]:
        """Return pending review ids whose raw threads are local draft mirrors."""
        local_workspace_active = (
            self._state.pending_review.drafts_are_canonical
            or bool(self._state.pending_review.comments)
        )
        return pending_review_hidden_ids(
            pending_review_id=(
                self._state.pending_review.review_id if local_workspace_active else None
            ),
            reviews=self._state.reviews if local_workspace_active else (),
            obsolete_pending_review_ids=self._state.pending_review.obsolete_review_ids,
        )

    def visible_review_threads_for_paths(
        self,
        file_paths: Iterable[str],
    ) -> list[ReviewThread]:
        """Return non-pending raw review threads for the provided file paths."""
        paths = set(file_paths)
        if not paths:
            return []

        hidden_ids = self.pending_review_hidden_ids()
        return [
            thread
            for thread in self._state.review_threads
            if thread.path in paths
            and not review_thread_is_pending_draft(
                thread,
                drafts=self._state.pending_review.comments,
                hidden_review_ids=hidden_ids,
                reviews=self._state.reviews,
            )
        ]

    def visible_timeline_reviews(self) -> list[PRReview]:
        """Return timeline reviews using pending review local state as canonical."""
        return visible_timeline_reviews(
            self._state.reviews,
            pending_review_id=self._state.pending_review.review_id,
            pending_review_body=self._state.pending_review.body,
        )

    def visible_timeline_comments(self) -> list[PRComment]:
        """Return timeline comments with pending drafts rendered exactly once."""
        reviews = self.visible_timeline_reviews()
        return visible_timeline_comments(
            self._state.comments,
            drafts=self._state.pending_review.comments,
            pending_review_id=self._state.pending_review.review_id,
            hidden_review_ids=self.pending_review_hidden_ids(),
            reviews=reviews,
        )

    def count_pending_file_comments(self, filename: str) -> int:
        return self.review_annotations().count_pending_for_file(filename)

    def is_inline_comment_diff_line(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        start_line: int | None = None,
        start_side: Literal["LEFT", "RIGHT"] | None = None,
    ) -> bool:
        return is_pending_inline_comment_diff_line(
            self._state.file_diffs.get(path),
            line=line,
            side=side,
            start_line=start_line,
            start_side=start_side,
        )

    def _pending_review_head_sha(self) -> str:
        """Read the head at synchronization time, after any optimistic UI callback."""
        pr = self._state.pr
        return pr.head_sha if pr is not None else ""

    def _remember_pending_review_sync_review(
        self,
        previous_review_id: int | None,
        review: PRReview | None,
    ) -> None:
        if review is None:
            if previous_review_id:
                self._remove_pending_review(previous_review_id)
            return
        self._upsert_pending_review(review, previous_review_id=previous_review_id)

    def _upsert_pending_review(
        self,
        review: PRReview,
        *,
        previous_review_id: int | None,
    ) -> None:
        insert_at: int | None = None
        reviews: list[PRReview] = []
        for existing in self._state.reviews:
            should_replace = existing.id == review.id or (
                previous_review_id is not None
                and existing.id == previous_review_id
                and existing.state == ReviewState.PENDING
            )
            should_drop_stale_pending = (
                existing.state == ReviewState.PENDING and existing.id != review.id
            )
            if should_replace or should_drop_stale_pending:
                if insert_at is None:
                    insert_at = len(reviews)
                continue
            reviews.append(existing)

        if insert_at is None or insert_at >= len(reviews):
            reviews.append(review)
        else:
            reviews.insert(insert_at, review)
        self._state.reviews = reviews

    def _remove_pending_review(self, review_id: int) -> None:
        self._state.reviews = [
            review
            for review in self._state.reviews
            if not (review.id == review_id and review.state == ReviewState.PENDING)
        ]

    def delete_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        draft_index: int | None = None,
    ) -> bool:
        return self._state.pending_review.delete_inline_comment(
            path=path,
            line=line,
            side=side,
            draft_index=draft_index,
        )

    async def queue_pending_file_comment(
        self,
        body: str,
        *,
        path: str,
        after_local_save: Callable[[], Awaitable[None]] | None = None,
    ) -> PendingReviewComment:
        return await self._state.pending_review.queue_file_comment(
            body,
            path=path,
            after_local_save=after_local_save,
            adapter=self._service,
            pr_number=self.pr_number,
            head_sha=self._pending_review_head_sha,
            on_sync=self._remember_pending_review_sync_review,
        )

    async def queue_pending_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        start_line: int | None = None,
        start_side: Literal["LEFT", "RIGHT"] | None = None,
        draft_index: int | None = None,
        after_local_save: Callable[[], Awaitable[None]] | None = None,
    ) -> PendingReviewComment:
        return await self._state.pending_review.queue_inline_comment(
            body,
            path=path,
            line=line,
            side=side,
            diff=self._state.file_diffs.get(path),
            start_line=start_line,
            start_side=start_side,
            draft_index=draft_index,
            after_local_save=after_local_save,
            adapter=self._service,
            pr_number=self.pr_number,
            head_sha=self._pending_review_head_sha,
            on_sync=self._remember_pending_review_sync_review,
        )

    async def upsert_pending_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        start_line: int | None = None,
        start_side: Literal["LEFT", "RIGHT"] | None = None,
    ) -> PendingReviewComment:
        return await self.queue_pending_inline_comment(
            body,
            path=path,
            line=line,
            side=side,
            start_line=start_line,
            start_side=start_side,
            draft_index=self._pending_review_comment_index(path, line, side),
        )

    async def post_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        start_line: int | None = None,
        start_side: Literal["LEFT", "RIGHT"] | None = None,
        draft_index: int | None = None,
    ) -> PRComment:
        comment = await self.submit_inline_comment(
            body,
            path=path,
            line=line,
            side=side,
            start_line=start_line,
            start_side=start_side,
        )
        if draft_index is not None:
            await self.remove_pending_inline_comment(
                path=path,
                line=line,
                side=side,
                draft_index=draft_index,
            )
        return comment

    async def sync_pending_review(self) -> PRReview | None:
        return await self._state.pending_review.sync(
            adapter=self._service,
            pr_number=self.pr_number,
            head_sha=self._pending_review_head_sha,
            on_sync=self._remember_pending_review_sync_review,
        )

    async def remove_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        draft_index: int | None = None,
        after_local_delete: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        return await self._state.pending_review.remove_inline_comment(
            path=path,
            line=line,
            side=side,
            draft_index=draft_index,
            after_local_delete=after_local_delete,
            adapter=self._service,
            pr_number=self.pr_number,
            head_sha=self._pending_review_head_sha,
            on_sync=self._remember_pending_review_sync_review,
        )

    def pending_review_comment_index_for(
        self,
        comment: PRComment,
    ) -> int | None:
        """Return the canonical pending draft index represented by a comment."""
        drafts = self._state.pending_review.comments
        for index, draft in enumerate(drafts):
            if draft.review_comment_id and draft.review_comment_id == comment.id:
                return index

        hidden_review_ids = pending_review_hidden_ids(
            pending_review_id=self._state.pending_review.review_id,
            reviews=self._state.reviews,
            obsolete_pending_review_ids=self._state.pending_review.obsolete_review_ids,
        )
        if comment.pull_request_review_id not in hidden_review_ids:
            return None
        return next(
            (
                index
                for index, draft in enumerate(drafts)
                if pending_draft_matches_review_comment(draft, comment)
            ),
            None,
        )

    async def remove_pending_review_comment_at(
        self,
        draft_index: int,
        *,
        after_local_delete: Callable[[], Awaitable[None]] | None = None,
    ) -> bool:
        """Delete one canonical pending draft selected outside the diff."""
        return await self._state.pending_review.remove_comment_at(
            draft_index,
            after_local_delete=after_local_delete,
            adapter=self._service,
            pr_number=self.pr_number,
            head_sha=self._pending_review_head_sha,
            on_sync=self._remember_pending_review_sync_review,
        )

    def _pending_review_comment_index(
        self,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> int | None:
        return self.review_annotations().pending_index(
            path=path,
            line=line,
            side=side,
        )

    async def submit_review(
        self,
        event: Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"],
        body: str = "",
    ) -> None:
        """Submit a top-level review and refresh local review state."""
        await self._state.pending_review.submit(
            event,
            body,
            adapter=self._service,
            pr_number=self.pr_number,
            remember_submitted=self._remember_submitted_review,
        )

    async def _remember_submitted_review(self, review: PRReview | None) -> None:
        if review is None or not review.id:
            return

        comments: list[PRComment] = []
        with suppress(Exception):
            comments = await self._service.list_review_comments(
                self.pr_number,
                review.id,
            )

        self._recent_discussion = remember_submitted_review(
            self._recent_discussion,
            review,
            comments,
        )
        if self._state.pr is not None:
            self._apply_discussion_state(self._state.pr)

    def _remember_submitted_comment(self, comment: PRComment) -> None:
        self._recent_discussion = remember_submitted_comment(
            self._recent_discussion,
            comment,
        )
        if self._state.pr is not None:
            self._apply_discussion_state(self._state.pr)

    async def update_review_comment(
        self,
        comment: PRComment,
        body: str,
    ) -> PRComment:
        """Update a submitted review comment and project its new body locally."""
        if not comment.node_id:
            raise ValueError("Review comment node ID is unavailable")
        pr = self._state.pr
        if pr is None:
            raise ValueError("PR not loaded")

        normalized = normalize_issue_comment_body(body)
        replacement = comment.model_copy(update={"body": normalized})
        updated_pr = replace_review_comment(pr, comment, replacement)
        if updated_pr is None:
            raise ValueError("Selected review comment no longer exists")

        response = await self._service.update_review_comment(
            comment.node_id,
            normalized,
        )
        if response.body:
            replacement = comment.model_copy(update={"body": response.body})
            updated_pr = replace_review_comment(pr, comment, replacement)
            assert updated_pr is not None

        self._recent_discussion = remember_updated_comment(
            self._recent_discussion,
            comment,
            replacement,
        )
        self._apply_discussion_state(updated_pr)
        self._post_discussion_detail_messages()
        return replacement

    async def delete_review_comment(self, comment: PRComment) -> None:
        """Delete a submitted pull request review comment."""
        if not comment.node_id:
            raise ValueError("Review comment node ID is unavailable")
        await self._service.delete_review_comment(comment.node_id)
        self._recent_discussion = forget_review_comment(
            self._recent_discussion,
            comment,
        )

    async def refresh_review_data(self) -> None:
        """Refresh comments, reviews, and review threads without reloading file diffs."""
        await self._load_pr_data()
        sync_file_comments(self._state.files, self._state.comments_by_file)

    def get_file_comments(self, filename: str) -> list[PRComment]:
        return self._state.comments_by_file.get(filename, [])

    def get_thread_info(self, root_comment_id: int) -> ReviewThreadInfo | None:
        return self._state.thread_info_cache.get(root_comment_id)

    def get_review_thread(self, root_comment_id: int) -> ReviewThread | None:
        return self._state.thread_cache.get(root_comment_id)

    async def resolve_thread(self, thread_id: str, root_comment_id: int) -> bool:
        result = await self._service.resolve_thread(thread_id)
        if result:
            self._update_thread_resolved_state(root_comment_id, is_resolved=True)
            self._post_message(
                self.ThreadResolved(
                    thread_id=thread_id,
                    root_comment_id=root_comment_id,
                    is_resolved=True,
                )
            )
        return result

    async def unresolve_thread(self, thread_id: str, root_comment_id: int) -> bool:
        result = await self._service.unresolve_thread(thread_id)
        if result:
            self._update_thread_resolved_state(root_comment_id, is_resolved=False)
            self._post_message(
                self.ThreadResolved(
                    thread_id=thread_id,
                    root_comment_id=root_comment_id,
                    is_resolved=False,
                )
            )
        return result

    async def load_file_view_states(self) -> None:
        """Load viewed states from GitHub. Non-critical — failures are silently ignored."""
        try:
            states = await self._service.get_pr_file_view_states(self.pr_number)
        except RuntimeError:
            return
        self.viewed_files.apply_loaded(states)

    async def set_file_viewed(self, filename: str, *, viewed: bool) -> None:
        """Sync viewed state to GitHub."""
        await self.viewed_files.set(filename, viewed=viewed)

    async def _persist_file_viewed(self, filename: str, viewed: bool) -> bool:
        pr = self._state.pr
        if pr is None:
            return False
        if viewed:
            await self._service.mark_file_as_viewed(pr.node_id, filename)
        else:
            await self._service.unmark_file_as_viewed(pr.node_id, filename)
        return True

    def _update_thread_resolved_state(
        self, root_comment_id: int, *, is_resolved: bool
    ) -> None:
        updated = update_thread_resolution(
            review_threads=self._state.review_threads,
            thread_info_cache=self._state.thread_info_cache,
            thread_cache=self._state.thread_cache,
            root_comment_id=root_comment_id,
            is_resolved=is_resolved,
        )
        self._state.review_threads = updated.review_threads
        self._state.thread_info_cache = updated.thread_info_cache
        self._state.thread_cache = updated.thread_cache


@dataclass
class _FileViewedSyncState:
    confirmed: FileViewedState
    desired: FileViewedState
    revision: int = 1
    worker_active: bool = False
    force_sync: bool = False


class ViewedFiles:
    """Own optimistic viewed state and per-file reconciliation."""

    def __init__(
        self,
        state: PRStoreState,
        persist: Callable[[str, bool], Awaitable[bool]],
    ) -> None:
        self._state = state
        self._persist = persist
        self._pending: dict[str, _FileViewedSyncState] = {}

    def apply_loaded(self, states: dict[str, str]) -> None:
        """Apply remotely loaded viewed states."""
        apply_file_view_states(self._state.files, states)

    async def set(self, filename: str, *, viewed: bool) -> None:
        """Publish a viewed state after GitHub confirms the write."""
        if await self._persist(filename, viewed):
            apply_file_view_state(
                self._state.files,
                self._state.files_by_filename,
                filename,
                FileViewedState.VIEWED if viewed else FileViewedState.UNVIEWED,
            )

    def toggle(
        self,
        filename: str,
        on_change: Callable[[str, FileViewedState], None] | None = None,
    ) -> bool:
        """Toggle locally and report whether a synchronization worker is needed."""
        file = self._file(filename)
        if file is None:
            return False
        old_state = file.viewer_viewed_state
        desired = (
            FileViewedState.UNVIEWED
            if old_state == FileViewedState.VIEWED
            else FileViewedState.VIEWED
        )
        pending = self._pending.get(filename)
        if pending is None:
            pending = _FileViewedSyncState(old_state, desired)
            self._pending[filename] = pending
        else:
            pending.desired = desired
            pending.revision += 1
        file.viewer_viewed_state = desired
        if on_change is not None:
            on_change(filename, desired)
        start_sync = not pending.worker_active
        pending.worker_active = True
        return start_sync

    async def sync(
        self,
        filename: str,
        on_change: Callable[[str], None],
    ) -> FileViewedState | None:
        """Reconcile queued intent, rolling back only the current failed request."""
        pending = self._pending.get(filename)
        if pending is None:
            return None
        try:
            while True:
                requested = pending.desired
                revision = pending.revision
                if requested == pending.confirmed and not pending.force_sync:
                    return None
                try:
                    await self.set(filename, viewed=requested == FileViewedState.VIEWED)
                except GitHubError:
                    if pending.revision != revision:
                        pending.force_sync = True
                        continue
                    pending.desired = pending.confirmed
                    pending.force_sync = False
                    file = self._file(filename)
                    if file is not None:
                        file.viewer_viewed_state = pending.confirmed
                    on_change(filename)
                    raise

                pending.confirmed = requested
                pending.force_sync = False
                file = self._file(filename)
                if file is not None and file.viewer_viewed_state != pending.desired:
                    file.viewer_viewed_state = pending.desired
                    on_change(filename)
                if pending.desired == pending.confirmed:
                    return pending.confirmed
        finally:
            pending.worker_active = False
            if (
                self._pending.get(filename) is pending
                and pending.desired == pending.confirmed
                and not pending.force_sync
            ):
                self._pending.pop(filename, None)

    def _file(self, filename: str) -> PRFile | None:
        return next(
            (file for file in self._state.files if file.filename == filename), None
        )
