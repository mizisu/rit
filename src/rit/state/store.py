from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from textual.message import Message

from rit.core.diff import parse_patch
from rit.core.types import FileDiff
from rit.services.github import GitHubService, ReviewThreadInfo
from rit.state.models import (
    PR,
    LoadingState,
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

    @property
    def state(self) -> PRStoreState:
        return self._state

    def _post_message(self, message: Message) -> None:
        pass

    async def load_all(self) -> None:
        """Load all PR data (GraphQL + REST in parallel)."""
        await asyncio.gather(
            self._load_pr_data(),
            self.load_files(),
            return_exceptions=True,
        )

    async def _load_pr_data(self) -> None:
        self._state.pr_loading = LoadingState.LOADING
        try:
            pr = await self._service.get_pr_all(self.pr_number)
            self._state.pr = pr
            self._state.reviews = pr.reviews
            self._state.issue_comments = pr.issue_comments
            self._state.review_threads = pr.review_threads

            all_comments: list[PRComment] = []
            for thread in pr.review_threads:
                all_comments.extend(thread.comments)
            self._state.comments = all_comments
            self._state.comments_by_file = {}
            for comment in all_comments:
                if comment.path not in self._state.comments_by_file:
                    self._state.comments_by_file[comment.path] = []
                self._state.comments_by_file[comment.path].append(comment)

            self._state.pr_loading = LoadingState.LOADED

            self._state.thread_info_cache = {
                thread.root_comment_id: ReviewThreadInfo(
                    thread_id=thread.id,
                    is_resolved=thread.is_resolved,
                    path=thread.path,
                    line=thread.line,
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

            self._post_message(self.PRLoaded(pr=pr))
            self._post_message(self.CommentsLoaded(comments=all_comments))
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
        """Load files via REST API (GraphQL doesn't provide patch data)."""
        self._state.files_loading = LoadingState.LOADING
        try:
            files = await self._service.get_pr_files(self.pr_number)
            self._state.files = files
            self._state.files_total_count = len(files)

            self._state.file_diffs = await asyncio.to_thread(
                self._parse_all_patches, files
            )
            self._state.files_loaded_count = len(files)

            for file in files:
                file.comments = self._state.comments_by_file.get(file.filename, [])

            self._state.files_loading = LoadingState.LOADED
            self._post_message(self.FilesLoaded(files=files))

        except Exception as e:
            self._state.files_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(self.ErrorOccurred(error=str(e), source="load_files"))

    def _parse_all_patches(self, files: list[PRFile]) -> dict[str, FileDiff]:
        return {f.filename: parse_patch(f.patch, f.filename) for f in files}

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
