from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Literal, cast

from textual.message import Message

from rit.core.diff import parse_patch
from rit.core.types import FileDiff
from rit.services.github import GitHubService, PRDiscussion, ReviewThreadInfo
from rit.state.models import (
    PR,
    FileViewedState,
    LoadingState,
    NodeList,
    PendingReviewComment,
    PRComment,
    PRFile,
    PRIssueComment,
    PRReview,
    ReviewThread,
)


@dataclass
class PRStoreState:
    pr_loading: LoadingState = LoadingState.IDLE
    files_loading: LoadingState = LoadingState.IDLE

    pr: PR | None = None
    files: list[PRFile] = field(default_factory=list)
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
        diff: FileDiff

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

    @property
    def state(self) -> PRStoreState:
        return self._state

    def set_message_sink(self, sink: Callable[[Message], None]) -> None:
        self._message_sink = sink

    def _post_message(self, message: Message) -> None:
        if self._message_sink is not None:
            self._message_sink(message)

    async def load_all(self) -> None:
        """Load PR summary first, then discussion and files in parallel."""
        await self.load_pr_summary()
        if self._state.pr is None:
            return

        await asyncio.gather(
            self.load_pr_discussion(),
            self.load_files(),
            return_exceptions=True,
        )

    async def load_pr_summary(self) -> None:
        self._state.pr_loading = LoadingState.LOADING
        try:
            pr = await self._service.get_pr_summary(self.pr_number)
            self._state.pr = pr
            self._state.pr_loading = LoadingState.LOADED
            self._state.files_total_count = pr.changed_files
            self._post_message(self.PRLoaded(pr=pr))
        except Exception as e:
            self._state.pr_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(
                self.ErrorOccurred(error=str(e), source="load_pr_summary")
            )

    async def load_pr_discussion(self) -> None:
        try:
            discussion = await self._service.get_pr_discussion(self.pr_number)
            pr = self._merge_pr_discussion(discussion)
            self._apply_discussion_state(pr)
            await self._load_pending_review(pr)

            self._post_message(self.PRDiscussionLoaded(pr=pr))
            self._post_message(self.CommentsLoaded(comments=self._state.comments))
            self._post_message(self.ReviewsLoaded(reviews=pr.reviews))
            self._post_message(self.IssueCommentsLoaded(comments=pr.issue_comments))
            self._post_message(
                self.ThreadsLoaded(threads=self._state.thread_info_cache)
            )
        except Exception as e:
            self._state.error = str(e)
            self._post_message(
                self.ErrorOccurred(error=str(e), source="load_pr_discussion")
            )

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
            self._post_message(self.CommentsLoaded(comments=self._state.comments))
            self._post_message(self.ReviewsLoaded(reviews=pr.reviews))
            self._post_message(self.IssueCommentsLoaded(comments=pr.issue_comments))
            self._post_message(
                self.ThreadsLoaded(threads=self._state.thread_info_cache)
            )

        except Exception as e:
            self._state.pr_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(self.ErrorOccurred(error=str(e), source="load_pr_data"))

    async def load_files(self) -> None:
        """Load files incrementally via paginated REST API."""
        self._state.files_loading = LoadingState.LOADING
        self._state.files = []
        self._state.file_diffs = {}
        self._state.files_loaded_count = 0
        if self._state.pr is None:
            self._state.files_total_count = 0

        page = 1
        per_page = 100

        try:
            while True:
                batch = await self._service.get_pr_files_page(
                    self.pr_number,
                    page=page,
                    per_page=per_page,
                )
                if not batch:
                    break

                for file in batch:
                    file.comments = self._state.comments_by_file.get(file.filename, [])

                self._state.files.extend(batch)
                self._state.file_diffs.update(
                    await asyncio.to_thread(self._parse_all_patches, batch)
                )
                self._state.files_loaded_count = len(self._state.files)
                if self._state.files_total_count < self._state.files_loaded_count:
                    self._state.files_total_count = self._state.files_loaded_count

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

                if len(batch) < per_page:
                    break
                page += 1

            self._state.files_loading = LoadingState.LOADED
            if not self._state.files:
                self._post_message(
                    self.FilesLoaded(
                        files=[],
                        loaded_count=0,
                        total_count=self._state.files_total_count,
                    )
                )
        except Exception as e:
            self._state.files_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(self.ErrorOccurred(error=str(e), source="load_files"))

    def _parse_all_patches(self, files: list[PRFile]) -> dict[str, FileDiff]:
        return {f.filename: parse_patch(f.patch, f.filename) for f in files}

    def _merge_pr_discussion(self, discussion: PRDiscussion) -> PR:
        pr = self._state.pr or PR(number=self.pr_number)
        merged_pr = pr.model_copy(
            update={
                "body": discussion.body,
                "reviews_connection": NodeList(nodes=discussion.reviews),
                "issue_comments_connection": NodeList(nodes=discussion.issue_comments),
                "review_threads_connection": NodeList(nodes=discussion.review_threads),
            }
        )
        self._state.pr = merged_pr
        return merged_pr

    def _apply_discussion_state(self, pr: PR) -> None:
        self._state.reviews = pr.reviews
        self._state.issue_comments = pr.issue_comments
        self._state.review_threads = pr.review_threads

        all_comments: list[PRComment] = []
        for thread in pr.review_threads:
            all_comments.extend(thread.comments)
        self._state.comments = all_comments

        comments_by_file: dict[str, list[PRComment]] = {}
        for comment in all_comments:
            comments_by_file.setdefault(comment.path, []).append(comment)
        self._state.comments_by_file = comments_by_file

        for file in self._state.files:
            file.comments = comments_by_file.get(file.filename, [])

        self._state.thread_info_cache = {
            thread.root_comment_id: ReviewThreadInfo(
                thread_id=thread.id,
                is_resolved=thread.is_resolved,
                path=thread.path,
                line=thread.anchor_line,
                root_comment_id=thread.root_comment_id,
            )
            for thread in pr.review_threads
            if thread.root_comment_id
        }
        self._state.thread_cache = {
            thread.root_comment_id: thread
            for thread in pr.review_threads
            if thread.root_comment_id
        }

    def select_file(self, filename: str) -> None:
        if filename in self._state.file_diffs:
            self._state.selected_file = filename
            diff = self._state.file_diffs[filename]
            self._post_message(self.FileSelected(filename=filename, diff=diff))

    def get_file_diff(self, filename: str) -> FileDiff | None:
        return self._state.file_diffs.get(filename)

    async def get_file_content(self, filename: str) -> str | None:
        """Fetch full file content at the PR's head ref. Cached after first call."""
        cached = self._state.file_contents.get(filename)
        if cached is not None:
            return cached

        pr = self._state.pr
        if pr is None or not pr.head_sha:
            return None

        try:
            content = await self._service.get_file_content(filename, pr.head_sha)
            self._state.file_contents[filename] = content
            return content
        except Exception:
            return None

    async def submit_issue_comment(self, body: str) -> PRIssueComment:
        """Submit a PR-level comment and update local state."""
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")

        comment = await self._service.create_issue_comment(self.pr_number, normalized)
        self._state.issue_comments.append(comment)
        self._state.issue_comments.sort(key=lambda item: item.created_at)
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
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")

        pr = self._state.pr
        if pr is None or not pr.head_sha:
            raise ValueError("PR head SHA is unavailable")

        return await self._service.create_review_comment(
            self.pr_number,
            body=normalized,
            commit_id=pr.head_sha,
            path=path,
            line=line,
            side=side,
        )

    def save_pending_inline_comment(
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

        comments, draft = self._with_pending_comment_upserted(
            body=normalized,
            path=path,
            line=line,
            side=side,
        )
        self._state.pending_review_comments = comments
        return draft

    def get_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> PendingReviewComment | None:
        for draft in self._state.pending_review_comments:
            if draft.path == path and draft.line == line and draft.side == side:
                return draft
        return None

    def get_pending_file_comments(self, filename: str) -> list[PendingReviewComment]:
        return [
            draft
            for draft in self._state.pending_review_comments
            if draft.path == filename
        ]

    def _with_pending_comment_upserted(
        self,
        *,
        body: str,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> tuple[list[PendingReviewComment], PendingReviewComment]:
        draft = PendingReviewComment(body=body, path=path, line=line, side=side)
        comments = list(self._state.pending_review_comments)
        for index, existing in enumerate(comments):
            if (
                existing.path == path
                and existing.line == line
                and existing.side == side
            ):
                comments[index] = draft
                break
        else:
            comments.append(draft)
        comments.sort(key=lambda item: (item.path, item.line, item.side))
        return comments, draft

    def _with_pending_comment_removed(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> tuple[list[PendingReviewComment], bool]:
        comments = list(self._state.pending_review_comments)
        for index, draft in enumerate(comments):
            if draft.path == path and draft.line == line and draft.side == side:
                del comments[index]
                return comments, True
        return comments, False

    async def _replace_pending_review(
        self,
        comments: list[PendingReviewComment],
    ) -> PRReview | None:
        pending_review_id = self._state.pending_review_id
        if pending_review_id is not None:
            await self._service.delete_pending_review(self.pr_number, pending_review_id)

        if not comments:
            return None

        pr = self._state.pr
        commit_id = pr.head_sha if pr is not None and pr.head_sha else None
        return await self._service.create_pending_review(
            self.pr_number,
            comments=comments,
            body=self._state.pending_review_body or None,
            commit_id=commit_id,
        )

    async def _load_pending_review(self, pr: PR) -> None:
        pending_review = next(
            (
                review
                for review in reversed(pr.reviews)
                if review.state.name == "PENDING"
            ),
            None,
        )
        if pending_review is None:
            self._state.pending_review_id = None
            self._state.pending_review_body = ""
            self._state.pending_review_comments = []
            return

        self._state.pending_review_id = pending_review.id or None
        self._state.pending_review_body = pending_review.body

        try:
            review_comments = await self._service.list_review_comments(
                self.pr_number,
                pending_review.id,
            )
        except Exception:
            self._state.pending_review_comments = []
            return

        pending_comments: list[PendingReviewComment] = []
        for comment in review_comments:
            anchor_line = comment.anchor_line
            if not comment.path or anchor_line is None:
                continue
            if comment.side not in {"LEFT", "RIGHT"}:
                continue
            pending_comments.append(
                PendingReviewComment(
                    body=comment.body,
                    path=comment.path,
                    line=anchor_line,
                    side=cast(Literal["LEFT", "RIGHT"], comment.side),
                )
            )

        pending_comments.sort(key=lambda item: (item.path, item.line, item.side))
        self._state.pending_review_comments = pending_comments

    def delete_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> bool:
        comments, deleted = self._with_pending_comment_removed(
            path=path,
            line=line,
            side=side,
        )
        if deleted:
            self._state.pending_review_comments = comments
        return deleted

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

        comments, draft = self._with_pending_comment_upserted(
            body=normalized,
            path=path,
            line=line,
            side=side,
        )
        review = await self._replace_pending_review(comments)
        self._state.pending_review_comments = comments
        self._state.pending_review_id = review.id if review is not None else None
        if review is None:
            self._state.pending_review_body = ""
        elif review.body:
            self._state.pending_review_body = review.body
        return draft

    async def remove_pending_inline_comment(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> bool:
        comments, deleted = self._with_pending_comment_removed(
            path=path,
            line=line,
            side=side,
        )
        if not deleted:
            return False

        review = await self._replace_pending_review(comments)
        self._state.pending_review_comments = comments
        self._state.pending_review_id = review.id if review is not None else None
        if review is None:
            self._state.pending_review_body = ""
        elif review.body:
            self._state.pending_review_body = review.body
        return True

    async def submit_review(
        self,
        event: Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"],
        body: str = "",
    ) -> None:
        """Submit a top-level review and refresh local review state."""
        normalized = body.strip()
        pending_comments = list(self._state.pending_review_comments)
        if event == "REQUEST_CHANGES" and not normalized:
            raise ValueError("Review body cannot be empty")
        if event == "COMMENT" and not normalized and not pending_comments:
            raise ValueError("Review body cannot be empty")

        pending_review_id = self._state.pending_review_id
        if pending_review_id is not None:
            await self._service.submit_pending_review(
                self.pr_number,
                pending_review_id,
                event=event,
                body=normalized if normalized else None,
            )
        else:
            await self._service.submit_review(
                self.pr_number,
                event=event,
                body=normalized if normalized else None,
                comments=pending_comments,
            )

        self._state.pending_review_id = None
        self._state.pending_review_body = ""
        self._state.pending_review_comments.clear()

    async def refresh_review_data(self) -> None:
        """Refresh comments, reviews, and review threads without reloading file diffs."""
        await self._load_pr_data()
        for file in self._state.files:
            file.comments = self._state.comments_by_file.get(file.filename, [])

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
            for file in self._state.files:
                raw = states.get(file.filename)
                if raw:
                    try:
                        file.viewer_viewed_state = FileViewedState(raw)
                    except ValueError:
                        pass
        except Exception:
            pass

    async def set_file_viewed(self, filename: str, *, viewed: bool) -> None:
        """Sync viewed state to GitHub."""
        pr = self._state.pr
        if pr is None:
            return
        if viewed:
            await self._service.mark_file_as_viewed(pr.node_id, filename)
        else:
            await self._service.unmark_file_as_viewed(pr.node_id, filename)

    def _update_thread_resolved_state(
        self, root_comment_id: int, *, is_resolved: bool
    ) -> None:
        if root_comment_id in self._state.thread_info_cache:
            old_info = self._state.thread_info_cache[root_comment_id]
            self._state.thread_info_cache[root_comment_id] = ReviewThreadInfo(
                thread_id=old_info.thread_id,
                is_resolved=is_resolved,
                path=old_info.path,
                line=old_info.line,
                root_comment_id=old_info.root_comment_id,
            )

        for i, thread in enumerate(self._state.review_threads):
            if thread.root_comment_id == root_comment_id:
                new_thread = ReviewThread.model_validate(
                    {
                        "id": thread.id,
                        "isResolved": is_resolved,
                        "path": thread.path,
                        "line": thread.line,
                        "comments": {"nodes": thread.comments},
                    }
                )
                self._state.review_threads[i] = new_thread
                self._state.thread_cache[root_comment_id] = new_thread
                break
