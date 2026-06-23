from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal

from textual.message import Message

from rit.core.types import FileDiff
from rit.services.github import (
    GitHubService,
    PRDiscussion,
)
from rit.services.pr_file_pagination import (
    PR_FILES_PER_PAGE,
)
from rit.state.discussion_projection import (
    RecentDiscussion,
    project_discussion_state,
    remember_submitted_comment,
    remember_submitted_review,
    update_thread_resolution,
)
from rit.state.discussion_signature import discussion_render_signature
from rit.state.file_collection import (
    apply_file_view_state,
    apply_file_view_states,
    cache_file_diff,
    find_file,
    load_file_diff,
    select_file as project_file_selection,
    sync_file_comments,
)
from rit.state.file_content import load_cached_file_content
from rit.state.file_ingest import (
    append_file_summaries,
    begin_file_ingest,
    load_raw_diff_text,
    load_rest_file_pages,
    load_streamed_diff_summaries,
)
from rit.state.file_projection import (
    diff_from_file_patch,
    parse_file_patch_summaries,
)
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
    ReviewThreadInfo,
    ReviewThread,
)
from rit.state.pending_review import (
    PendingReviewSnapshot,
    UnsupportedInlineCommentTarget,
    apply_pending_review_projection,
    apply_pending_review_sync_result,
    clear_pending_review,
    delete_pending_comment,
    get_pending_file_comments as collect_pending_file_comments,
    get_pending_inline_comment as find_pending_inline_comment,
    is_inline_comment_diff_line as is_pending_inline_comment_diff_line,
    load_pending_review_projection,
    plan_inline_comment_submission,
    plan_pending_review_sync,
    plan_review_submission,
    project_pending_review_sync_result,
    restore_pending_review_snapshot as build_pending_review_restoration,
    save_pending_comment,
    should_restore_pending_review_snapshot,
    snapshot_pending_review as build_pending_review_snapshot,
)
from rit.state.pr_merge import merge_pr_discussion, merge_pr_summary
from rit.state.pr_management import plan_assignee_selection, plan_reviewer_selection


__all__ = (
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
    pending_review_id: int | None = None
    pending_review_body: str = ""
    pending_review_comments: list[PendingReviewComment] = field(default_factory=list)

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
        self._message_sink: Callable[[Message], None] | None = None
        self._pending_review_sync_lock = asyncio.Lock()
        self._pending_review_local_version = 0
        self._recent_discussion = RecentDiscussion()

    @property
    def state(self) -> PRStoreState:
        return self._state

    def set_message_sink(self, sink: Callable[[Message], None]) -> None:
        self._message_sink = sink

    def _post_message(self, message: Message) -> None:
        if self._message_sink is not None:
            self._message_sink(message)

    async def load_all(self) -> None:
        """Load PR metadata and files concurrently for early diff rendering."""
        await asyncio.gather(
            self.load_pr_summary(),
            self.load_pr_discussion(),
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
        full_discussion_task = asyncio.create_task(
            self._service.get_pr_discussion(self.pr_number)
        )
        try:
            fast_loaded = await self._load_fast_pr_discussion()
            previous_signature = (
                discussion_render_signature(self._state.pr)
                if fast_loaded and self._state.pr is not None
                else None
            )
            discussion = await full_discussion_task
            pr = self._merge_pr_discussion(discussion)
            self._apply_discussion_state(pr)
            await self._load_pending_review(pr)
            if (
                previous_signature is not None
                and previous_signature == discussion_render_signature(pr)
            ):
                self._post_discussion_metadata_messages(pr)
            else:
                self._post_discussion_messages(pr)
        except RuntimeError as e:
            if not full_discussion_task.done():
                full_discussion_task.cancel()
                with suppress(asyncio.CancelledError):
                    await full_discussion_task
            self._state.error = str(e)
            self._post_message(
                self.ErrorOccurred(error=str(e), source="load_pr_discussion")
            )

    async def _load_fast_pr_discussion(self) -> bool:
        get_fast_discussion = getattr(self._service, "get_pr_discussion_fast", None)
        if get_fast_discussion is None:
            return False

        try:
            discussion = await get_fast_discussion(self.pr_number)
        except RuntimeError:
            return False

        pr = self._merge_pr_discussion(discussion)
        self._apply_discussion_state(pr)
        self._post_discussion_messages(pr)
        return True

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
        try:
            pr = await self._service.get_pr_all(self.pr_number)
            self._state.pr = pr
            self._state.files_total_count = pr.changed_files
            self._apply_discussion_state(pr)
            self._state.pr_loading = LoadingState.LOADED
            await self._load_pending_review(pr)

            self._post_message(self.PRLoaded(pr=pr))
            self._post_discussion_detail_messages()

        except RuntimeError as e:
            self._state.pr_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(self.ErrorOccurred(error=str(e), source="load_pr_data"))

    async def load_files(self) -> None:
        """Load changed files quickly, falling back to raw diff streaming."""
        begin_file_ingest(self._state)

        try:
            if await self._load_files_from_rest_pages():
                return
            if await self._load_files_from_streamed_raw_diff():
                return
            if await self._load_files_from_raw_diff():
                return
        except RuntimeError as e:
            self._mark_files_load_error(str(e))
            return

        self._mark_files_load_error("No changed files could be loaded")

    def _mark_files_load_error(self, error: str) -> None:
        self._state.files_loading = LoadingState.ERROR
        self._state.error = error
        self._post_message(self.ErrorOccurred(error=error, source="load_files"))

    async def _load_files_from_streamed_raw_diff(self) -> bool:
        stream_sections = getattr(self._service, "iter_pr_diff_sections", None)
        if stream_sections is None:
            return False

        return await load_streamed_diff_summaries(
            self._state,
            pr_number=self.pr_number,
            stream_sections=stream_sections,
            parse_summaries=self._append_raw_diff_section_summaries,
            on_progress=self._post_files_loaded,
        )

    async def _load_files_from_raw_diff(self) -> bool:
        get_diff_text = getattr(self._service, "get_pr_diff_text", None)
        if get_diff_text is None:
            return False

        return await load_raw_diff_text(
            self._state,
            pr_number=self.pr_number,
            get_diff_text=get_diff_text,
            on_progress=self._post_files_loaded,
        )

    async def _append_raw_diff_section_summaries(self, sections: list[str]) -> int:
        summaries = await asyncio.to_thread(parse_file_patch_summaries, sections)
        return append_file_summaries(self._state, summaries)

    async def _load_files_from_rest_pages(self) -> bool:
        get_page = getattr(self._service, "get_pr_files_page", None)
        get_pages = getattr(self._service, "get_pr_file_pages", None)
        if get_page is None or get_pages is None:
            return False

        return await load_rest_file_pages(
            self._state,
            pr_number=self.pr_number,
            get_page=get_page,
            get_pages=get_pages,
            on_progress=self._post_files_loaded,
            per_page=PR_FILES_PER_PAGE,
        )

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
        )

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

    def save_pending_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> PendingReviewComment:
        result = save_pending_comment(
            self._state.pending_review_comments,
            body=body,
            path=path,
            line=line,
            side=side,
            is_diff_line=self.is_inline_comment_diff_line(
                path=path,
                line=line,
                side=side,
            ),
            current_version=self._pending_review_local_version,
        )
        self._state.pending_review_comments = result.comments
        self._pending_review_local_version = result.version
        return result.draft

    @property
    def pending_review_version(self) -> int:
        return self._pending_review_local_version

    def snapshot_pending_review(self) -> PendingReviewSnapshot:
        return build_pending_review_snapshot(
            pending_review_id=self._state.pending_review_id,
            pending_review_body=self._state.pending_review_body,
            pending_review_comments=self._state.pending_review_comments,
            version=self._pending_review_local_version,
        )

    def restore_pending_review_snapshot(
        self,
        snapshot: PendingReviewSnapshot,
    ) -> None:
        restored = build_pending_review_restoration(
            snapshot,
            current_version=self._pending_review_local_version,
        )
        self._state.pending_review_id = restored.pending_review_id
        self._state.pending_review_body = restored.pending_review_body
        self._state.pending_review_comments = restored.pending_review_comments
        self._pending_review_local_version = restored.version

    def get_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> PendingReviewComment | None:
        return find_pending_inline_comment(
            self._state.pending_review_comments,
            path=path,
            line=line,
            side=side,
        )

    def get_pending_file_comments(self, filename: str) -> list[PendingReviewComment]:
        return collect_pending_file_comments(
            self._state.pending_review_comments,
            filename,
        )

    def is_inline_comment_diff_line(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> bool:
        return is_pending_inline_comment_diff_line(
            self._state.file_diffs.get(path),
            line=line,
            side=side,
        )

    async def _replace_pending_review(
        self,
        comments: list[PendingReviewComment],
    ) -> PRReview | None:
        pr = self._state.pr
        plan = plan_pending_review_sync(
            comments,
            pending_review_id=self._state.pending_review_id,
            pending_review_body=self._state.pending_review_body,
            head_sha=pr.head_sha if pr is not None else "",
        )
        if plan.delete_review_id is not None:
            await self._service.delete_pending_review(
                self.pr_number,
                plan.delete_review_id,
            )
        if not plan.should_create:
            return None

        return await self._service.create_pending_review(
            self.pr_number,
            comments=plan.comments,
            body=plan.body,
            commit_id=plan.commit_id,
        )

    async def _load_pending_review(self, pr: PR) -> None:
        projection = await load_pending_review_projection(
            pr.reviews,
            pr_number=self.pr_number,
            list_review_comments=getattr(self._service, "list_review_comments", None),
        )
        applied = apply_pending_review_projection(
            projection,
            current_version=self._pending_review_local_version,
        )
        self._state.pending_review_id = applied.review_id
        self._state.pending_review_body = applied.body
        self._state.pending_review_comments = applied.comments
        self._pending_review_local_version = applied.version

    def delete_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> bool:
        result = delete_pending_comment(
            self._state.pending_review_comments,
            path=path,
            line=line,
            side=side,
            current_version=self._pending_review_local_version,
        )
        if result.deleted:
            self._state.pending_review_comments = result.comments
            self._pending_review_local_version = result.version
        return result.deleted

    async def upsert_pending_inline_comment(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> PendingReviewComment:
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")

        snapshot = self.snapshot_pending_review()
        draft = self.save_pending_inline_comment(
            normalized,
            path=path,
            line=line,
            side=side,
        )
        await self.sync_pending_review(
            rollback_to=snapshot,
            rollback_if_version=self._pending_review_local_version,
        )
        return draft

    async def sync_pending_review(
        self,
        *,
        rollback_to: PendingReviewSnapshot | None = None,
        rollback_if_version: int | None = None,
    ) -> PRReview | None:
        try:
            async with self._pending_review_sync_lock:
                comments = list(self._state.pending_review_comments)
                review = await self._replace_pending_review(comments)
                result = project_pending_review_sync_result(
                    review,
                    current_body=self._state.pending_review_body,
                )
                applied = apply_pending_review_sync_result(
                    result,
                    current_version=self._pending_review_local_version,
                )
                self._state.pending_review_id = applied.pending_review_id
                self._state.pending_review_body = applied.pending_review_body
                self._pending_review_local_version = applied.version
                return review
        except Exception:
            if should_restore_pending_review_snapshot(
                rollback_to,
                rollback_if_version=rollback_if_version,
                current_version=self._pending_review_local_version,
            ):
                assert rollback_to is not None
                self.restore_pending_review_snapshot(rollback_to)
            raise

    async def remove_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> bool:
        snapshot = self.snapshot_pending_review()
        deleted = self.delete_pending_inline_comment(
            path=path,
            line=line,
            side=side,
        )
        if not deleted:
            return False

        await self.sync_pending_review(
            rollback_to=snapshot,
            rollback_if_version=self._pending_review_local_version,
        )
        return True

    async def submit_review(
        self,
        event: Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"],
        body: str = "",
    ) -> None:
        """Submit a top-level review and refresh local review state."""
        plan = plan_review_submission(
            event,
            body,
            self._state.pending_review_comments,
            pending_review_id=self._state.pending_review_id,
        )
        if plan.uses_pending_review:
            assert plan.pending_review_id is not None
            submitted_review = await self._service.submit_pending_review(
                self.pr_number,
                plan.pending_review_id,
                event=plan.event,
                body=plan.body,
            )
        else:
            submitted_review = await self._service.submit_review(
                self.pr_number,
                event=plan.event,
                body=plan.body,
                comments=plan.comments,
            )
        await self._remember_submitted_review(submitted_review)

        cleared = clear_pending_review(
            current_version=self._pending_review_local_version,
        )
        self._state.pending_review_id = cleared.pending_review_id
        self._state.pending_review_body = cleared.pending_review_body
        self._state.pending_review_comments = cleared.pending_review_comments
        self._pending_review_local_version = cleared.version

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
        apply_file_view_states(self._state.files, states)

    async def set_file_viewed(self, filename: str, *, viewed: bool) -> None:
        """Sync viewed state to GitHub."""
        pr = self._state.pr
        if pr is None:
            return
        state = FileViewedState.VIEWED if viewed else FileViewedState.UNVIEWED
        if viewed:
            await self._service.mark_file_as_viewed(pr.node_id, filename)
        else:
            await self._service.unmark_file_as_viewed(pr.node_id, filename)
        apply_file_view_state(
            self._state.files,
            self._state.files_by_filename,
            filename,
            state,
        )

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
