from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal, cast

from textual.message import Message

from rit.core.diff import (
    ParsedFilePatch,
    ParsedFilePatchSummary,
    parse_file_patch_summary,
    parse_multi_file_patch,
    parse_patch,
)
from rit.core.datetime_utils import datetime_sort_key
from rit.core.types import FileDiff
from rit.services.github import (
    PR_FILES_MAX_REST_PAGES,
    PR_FILES_PAGE_CONCURRENCY,
    PR_FILES_PER_PAGE,
    GitHubService,
    PRDiscussion,
    ReviewThreadInfo,
)
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
    PRTeam,
    PRUser,
    ReviewRequest,
    ReviewThread,
)


def _parse_file_patch_summaries(
    sections: Iterable[str],
) -> list[ParsedFilePatchSummary]:
    summaries: list[ParsedFilePatchSummary] = []
    for section in sections:
        summary = parse_file_patch_summary(section)
        if summary is not None:
            summaries.append(summary)
    return summaries


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


@dataclass(frozen=True)
class PendingReviewSnapshot:
    """Snapshot used to roll back optimistic pending-review edits."""

    pending_review_id: int | None
    pending_review_body: str
    pending_review_comments: tuple[PendingReviewComment, ...]
    version: int


class UnsupportedInlineCommentTarget(ValueError):
    """Raised when GitHub cannot create a true line comment for a target."""

    def __init__(self, *, path: str, line: int, side: Literal["LEFT", "RIGHT"]):
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
        self._recent_submitted_reviews: dict[int, PRReview] = {}
        self._recent_submitted_review_comments: dict[int, list[PRComment]] = {}

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
        except Exception as e:
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
                self._discussion_render_signature(self._state.pr)
                if fast_loaded and self._state.pr is not None
                else None
            )
            discussion = await full_discussion_task
            pr = self._merge_pr_discussion(discussion)
            self._apply_discussion_state(pr)
            await self._load_pending_review(pr)
            if (
                previous_signature is not None
                and previous_signature == self._discussion_render_signature(pr)
            ):
                self._post_discussion_metadata_messages(pr)
            else:
                self._post_discussion_messages(pr)
        except Exception as e:
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
        except Exception:
            return False

        pr = self._merge_pr_discussion(discussion)
        self._apply_discussion_state(pr)
        self._post_discussion_messages(pr)
        return True

    def _post_discussion_messages(self, pr: PR) -> None:
        self._post_message(self.PRDiscussionLoaded(pr=pr))
        self._post_message(self.CommentsLoaded(comments=self._state.comments))
        self._post_message(self.ReviewsLoaded(reviews=self._state.reviews))
        self._post_message(
            self.IssueCommentsLoaded(comments=self._state.issue_comments)
        )
        self._post_message(self.ThreadsLoaded(threads=self._state.thread_info_cache))

    def _post_discussion_metadata_messages(self, pr: PR) -> None:
        self._post_message(self.PRDiscussionMetadataLoaded(pr=pr))
        self._post_message(self.ThreadsLoaded(threads=self._state.thread_info_cache))

    def _discussion_render_signature(self, pr: PR | None) -> tuple[object, ...]:
        if pr is None:
            return ()

        return (
            pr.body,
            tuple(
                (
                    review.id,
                    self._author_login(review.user),
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
                    self._author_login(comment.user),
                    comment.body,
                    comment.created_at,
                    comment.updated_at,
                )
                for comment in pr.issue_comments
            ),
            tuple(
                self._thread_render_signature(thread)
                for thread in pr.review_threads
            ),
        )

    def _thread_render_signature(self, thread: ReviewThread) -> tuple[object, ...]:
        return (
            thread.path,
            thread.anchor_line,
            tuple(
                (
                    comment.id,
                    self._author_login(comment.user),
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

    @staticmethod
    def _author_login(user: PRUser | None) -> str:
        if user is None:
            return ""
        login = user.login
        if login.endswith("[bot]"):
            return login[: -len("[bot]")]
        return login

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
            self._post_message(self.ReviewsLoaded(reviews=self._state.reviews))
            self._post_message(
                self.IssueCommentsLoaded(comments=self._state.issue_comments)
            )
            self._post_message(
                self.ThreadsLoaded(threads=self._state.thread_info_cache)
            )

        except Exception as e:
            self._state.pr_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(self.ErrorOccurred(error=str(e), source="load_pr_data"))

    async def load_files(self) -> None:
        """Load changed files quickly, falling back to raw diff streaming."""
        self._state.files_loading = LoadingState.LOADING
        self._state.files = []
        self._state.files_by_filename = {}
        self._state.file_diffs = {}
        self._state.files_loaded_count = 0
        if self._state.pr is None:
            self._state.files_total_count = 0

        try:
            if await self._load_files_from_rest_pages():
                return
            if await self._load_files_from_streamed_raw_diff():
                return
            if await self._load_files_from_raw_diff():
                return
        except Exception as e:
            self._state.files_loading = LoadingState.ERROR
            self._state.error = str(e)
            self._post_message(self.ErrorOccurred(error=str(e), source="load_files"))

    async def _load_files_from_streamed_raw_diff(self) -> bool:
        stream_sections = getattr(self._service, "iter_pr_diff_sections", None)
        if stream_sections is None:
            return False

        loaded_any = False
        sections: list[str] = []
        batch_size = 1
        posted_count = 0

        try:
            async for section in stream_sections(self.pr_number):
                sections.append(section)
                if len(sections) < batch_size:
                    continue

                parsed_count = await self._append_raw_diff_section_summaries(sections)
                sections.clear()
                if parsed_count:
                    loaded_any = True
                    posted_count = self._state.files_loaded_count
                    self._post_files_loaded()
                batch_size = 100

            if sections:
                parsed_count = await self._append_raw_diff_section_summaries(sections)
                if parsed_count:
                    loaded_any = True

            if not loaded_any:
                return False

            self._state.files_loading = LoadingState.LOADED
            if self._state.files_loaded_count != posted_count:
                self._post_files_loaded()
            return True
        except Exception:
            if loaded_any:
                raise
            return False

    async def _load_files_from_raw_diff(self) -> bool:
        try:
            raw_diff = await self._service.get_pr_diff_text(self.pr_number)
            parsed_files = await asyncio.to_thread(parse_multi_file_patch, raw_diff)
        except Exception:
            return False

        if not parsed_files:
            return False

        self._append_parsed_files(parsed_files)

        self._state.files_loading = LoadingState.LOADED
        self._post_files_loaded()
        return True

    async def _append_raw_diff_sections(self, sections: list[str]) -> int:
        raw_diff = "\n".join(sections)
        parsed_files = await asyncio.to_thread(parse_multi_file_patch, raw_diff)
        return self._append_parsed_files(parsed_files)

    async def _append_raw_diff_section_summaries(self, sections: list[str]) -> int:
        summaries = await asyncio.to_thread(_parse_file_patch_summaries, sections)
        return self._append_file_summaries(summaries)

    def _append_parsed_files(self, parsed_files: Iterable[ParsedFilePatch]) -> int:
        parsed_count = 0
        for parsed_file in parsed_files:
            diff = parsed_file.diff
            if diff.filename in self._state.file_diffs:
                continue
            file = self._file_from_diff(diff)
            file.patch = parsed_file.patch
            self._state.file_diffs[diff.filename] = diff
            existing = self._state.files_by_filename.get(diff.filename)
            if existing is not None:
                existing.status = file.status
                existing.additions = file.additions
                existing.deletions = file.deletions
                existing.changes = file.changes
                existing.patch = parsed_file.patch
                existing.previous_filename = file.previous_filename
                continue
            self._append_file(file)
            parsed_count += 1
        return parsed_count

    def _append_file_summaries(
        self,
        summaries: Iterable[ParsedFilePatchSummary],
    ) -> int:
        parsed_count = 0
        for summary in summaries:
            if summary.filename in self._state.files_by_filename:
                continue
            file = self._file_from_summary(summary)
            self._append_file(file)
            parsed_count += 1
        return parsed_count

    async def _load_files_from_rest_pages(self) -> bool:
        get_page = getattr(self._service, "get_pr_files_page", None)
        get_pages = getattr(self._service, "get_pr_file_pages", None)
        if get_page is None or get_pages is None:
            return False

        try:
            first_page = await get_page(
                self.pr_number,
                page=1,
                per_page=PR_FILES_PER_PAGE,
            )
        except Exception:
            return False

        if not first_page:
            return False

        self._append_file_batch(first_page)
        self._post_files_loaded()

        if self._known_file_total_exceeds_rest_limit():
            return False

        remaining_pages = self._remaining_rest_file_pages()
        saw_last_page = len(first_page) < PR_FILES_PER_PAGE
        while remaining_pages:
            if self._known_file_total() is not None:
                remaining_pages = [
                    page
                    for page in self._remaining_file_pages()
                    if page >= remaining_pages[0]
                ]
                if not remaining_pages:
                    break
                page_chunk = remaining_pages
                remaining_pages = []
            else:
                page_chunk = remaining_pages[:PR_FILES_PAGE_CONCURRENCY]
                remaining_pages = remaining_pages[PR_FILES_PAGE_CONCURRENCY:]

            try:
                page_batches = await get_pages(
                    self.pr_number,
                    pages=page_chunk,
                    per_page=PR_FILES_PER_PAGE,
                )
            except Exception:
                return False

            for page in page_chunk:
                batch = page_batches.get(page, [])
                if not batch:
                    saw_last_page = True
                    break
                self._append_file_batch(batch)
                if len(batch) < PR_FILES_PER_PAGE:
                    saw_last_page = True
                    break

            if saw_last_page:
                break
            if self._known_file_total_exceeds_rest_limit():
                return False

        if not self._rest_file_list_complete(saw_last_page):
            return False

        self._state.files_loading = LoadingState.LOADED
        self._post_files_loaded()
        return True

    def _file_from_diff(self, diff: FileDiff) -> PRFile:
        status = "modified"
        if diff.is_new:
            status = "added"
        elif diff.is_deleted:
            status = "removed"
        elif diff.old_filename:
            status = "renamed"

        additions = diff.total_additions
        deletions = diff.total_deletions
        return PRFile(
            filename=diff.filename,
            status=status,
            additions=additions,
            deletions=deletions,
            changes=additions + deletions,
            previousFilename=diff.old_filename,
        )

    def _file_from_summary(self, summary: ParsedFilePatchSummary) -> PRFile:
        status = "modified"
        if summary.is_new:
            status = "added"
        elif summary.is_deleted:
            status = "removed"
        elif summary.old_filename:
            status = "renamed"

        return PRFile(
            filename=summary.filename,
            status=status,
            additions=summary.additions,
            deletions=summary.deletions,
            changes=summary.additions + summary.deletions,
            patch=summary.patch,
            previousFilename=summary.old_filename,
        )

    def _append_file(self, file: PRFile) -> None:
        file.comments = self._state.comments_by_file.get(file.filename, [])
        self._state.files.append(file)
        self._state.files_by_filename[file.filename] = file
        self._state.files_loaded_count = len(self._state.files)
        if self._state.files_total_count < self._state.files_loaded_count:
            self._state.files_total_count = self._state.files_loaded_count

    def _append_file_batch(self, batch: list[PRFile]) -> None:
        for file in batch:
            if file.filename in self._state.files_by_filename:
                continue
            self._append_file(file)

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

    def _remaining_file_pages(self) -> list[int]:
        if self._state.files_loaded_count < PR_FILES_PER_PAGE:
            return []

        total_count = self._known_file_total()
        if total_count is None or total_count <= PR_FILES_PER_PAGE:
            return []

        page_count = min(
            PR_FILES_MAX_REST_PAGES,
            (total_count + PR_FILES_PER_PAGE - 1) // PR_FILES_PER_PAGE,
        )
        return list(range(2, page_count + 1))

    def _remaining_rest_file_pages(self) -> list[int]:
        known_pages = self._remaining_file_pages()
        if known_pages:
            return known_pages
        if self._state.files_loaded_count < PR_FILES_PER_PAGE:
            return []
        return list(range(2, PR_FILES_MAX_REST_PAGES + 1))

    def _rest_file_list_complete(self, saw_last_page: bool) -> bool:
        total_count = self._known_file_total()
        if total_count is not None and self._state.files_loaded_count >= total_count:
            return True
        return saw_last_page

    def _known_file_total_exceeds_rest_limit(self) -> bool:
        total_count = self._known_file_total()
        if total_count is None:
            return False
        rest_limit = PR_FILES_MAX_REST_PAGES * PR_FILES_PER_PAGE
        return total_count > rest_limit and self._state.files_loaded_count < total_count

    def _known_file_total(self) -> int | None:
        if self._state.pr is not None and self._state.pr.changed_files > 0:
            return self._state.pr.changed_files
        if self._state.files_total_count > self._state.files_loaded_count:
            return self._state.files_total_count
        return None

    def _parse_file_patch(self, file: PRFile) -> FileDiff:
        diff = parse_patch(file.patch, file.filename)
        diff.old_filename = file.previous_filename
        diff.is_new = file.status == "added"
        diff.is_deleted = file.status == "removed"
        return diff

    def _merge_pr_summary(self, summary: PR) -> PR:
        existing = self._state.pr
        if existing is None:
            return summary

        return summary.model_copy(
            update={
                "body": existing.body,
                "reviews_connection": NodeList(nodes=list(self._state.reviews)),
                "issue_comments_connection": NodeList(
                    nodes=list(self._state.issue_comments)
                ),
                "review_threads_connection": NodeList(
                    nodes=list(self._state.review_threads)
                ),
            }
        )

    def _merge_pr_discussion(self, discussion: PRDiscussion) -> PR:
        pr = self._state.pr or PR(number=self.pr_number)
        body = discussion.body
        if not body:
            body = pr.body
        merged_pr = pr.model_copy(
            update={
                "body": body,
                "reviews_connection": NodeList(nodes=discussion.reviews),
                "issue_comments_connection": NodeList(nodes=discussion.issue_comments),
                "review_threads_connection": NodeList(nodes=discussion.review_threads),
            }
        )
        self._state.pr = merged_pr
        return merged_pr

    def _apply_discussion_state(self, pr: PR) -> None:
        reviews = list(pr.reviews)
        review_threads = list(pr.review_threads)
        reviews, review_threads = self._merge_recent_submitted_discussion(
            reviews,
            review_threads,
        )
        self._state.pr = pr.model_copy(
            update={
                "reviews_connection": NodeList(nodes=reviews),
                "review_threads_connection": NodeList(nodes=review_threads),
            }
        )
        self._state.reviews = reviews
        self._state.issue_comments = pr.issue_comments
        self._state.review_threads = review_threads

        all_comments: list[PRComment] = []
        for thread in review_threads:
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
            for thread in review_threads
            if thread.id and thread.root_comment_id
        }
        self._state.thread_cache = {
            thread.root_comment_id: thread
            for thread in review_threads
            if thread.root_comment_id
        }

    def _merge_recent_submitted_discussion(
        self,
        reviews: list[PRReview],
        review_threads: list[ReviewThread],
    ) -> tuple[list[PRReview], list[ReviewThread]]:
        if (
            not self._recent_submitted_reviews
            and not self._recent_submitted_review_comments
        ):
            return reviews, review_threads

        review_ids = {review.id for review in reviews if review.id}
        for review_id, review in self._recent_submitted_reviews.items():
            if review_id not in review_ids:
                reviews.append(review)
                review_ids.add(review_id)

        comment_ids = {
            comment.id
            for thread in review_threads
            for comment in thread.comments
            if comment.id
        }
        for comments in self._recent_submitted_review_comments.values():
            for comment in comments:
                if comment.id and comment.id in comment_ids:
                    continue
                review_threads.append(self._thread_from_submitted_comment(comment))
                if comment.id:
                    comment_ids.add(comment.id)

        return reviews, review_threads

    @staticmethod
    def _thread_from_submitted_comment(comment: PRComment) -> ReviewThread:
        anchor_side = comment.anchor_side
        anchor_line = comment.anchor_line
        line = anchor_line if anchor_side == "new" else None
        original_line = anchor_line if anchor_side == "old" else None
        return ReviewThread.model_validate(
            {
                "id": "",
                "isResolved": False,
                "path": comment.path,
                "line": line,
                "originalLine": original_line,
                "diffSide": comment.side,
                "comments": {"nodes": [comment]},
            }
        )

    def select_file(self, filename: str) -> None:
        if self._get_file(filename) is None and filename not in self._state.file_diffs:
            return

        self._state.selected_file = filename
        self._post_message(
            self.FileSelected(
                filename=filename,
                diff=self._state.file_diffs.get(filename),
            )
        )

    def get_file_diff(self, filename: str) -> FileDiff | None:
        cached = self._state.file_diffs.get(filename)
        if cached is not None:
            return cached

        file = self._get_file(filename)
        if file is None:
            return None

        diff = self._parse_file_patch(file)
        self._state.file_diffs[filename] = diff
        return diff

    async def get_file_diff_async(self, filename: str) -> FileDiff | None:
        cached = self._state.file_diffs.get(filename)
        if cached is not None:
            return cached

        file = self._get_file(filename)
        if file is None:
            return None

        diff = await asyncio.to_thread(self._parse_file_patch, file)
        cached = self._state.file_diffs.setdefault(filename, diff)
        return cached

    def _get_file(self, filename: str) -> PRFile | None:
        file = self._state.files_by_filename.get(filename)
        if file is not None:
            return file

        file = next(
            (file for file in self._state.files if file.filename == filename),
            None,
        )
        if file is not None:
            self._state.files_by_filename[filename] = file
        return file

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

        author_login = pr.user.login if pr.user else ""
        desired_users = {
            login.strip()
            for login in users
            if login.strip() and login.strip() != author_login
        }
        desired_teams = {team.strip() for team in teams if team.strip()}
        current_users, current_teams = self._current_requested_reviewers(pr)

        add_users = sorted(desired_users - current_users)
        add_teams = sorted(desired_teams - current_teams)
        remove_users = sorted(current_users - desired_users)
        remove_teams = sorted(current_teams - desired_teams)

        if not add_users and not add_teams and not remove_users and not remove_teams:
            return False

        if remove_users or remove_teams:
            await self._service.remove_requested_reviewers(
                self.pr_number,
                reviewers=remove_users,
                team_reviewers=remove_teams,
            )
        if add_users or add_teams:
            await self._service.request_reviewers(
                self.pr_number,
                reviewers=add_users,
                team_reviewers=add_teams,
            )

        await self.load_pr_summary()
        return True

    async def set_assignees(self, logins: Iterable[str]) -> bool:
        """Set PR assignees to the provided user logins."""
        pr = self._state.pr
        if pr is None:
            raise ValueError("PR not loaded")

        desired = {login.strip() for login in logins if login.strip()}
        current = {user.login for user in pr.assignees if user.login}
        add_logins = sorted(desired - current)
        remove_logins = sorted(current - desired)

        if not add_logins and not remove_logins:
            return False

        if remove_logins:
            await self._service.remove_assignees(self.pr_number, remove_logins)
        if add_logins:
            await self._service.add_assignees(self.pr_number, add_logins)

        await self.load_pr_summary()
        return True

    def _current_requested_reviewers(self, pr: PR) -> tuple[set[str], set[str]]:
        users: set[str] = set()
        teams: set[str] = set()
        for request in pr.requested_reviewers:
            kind, key = self._review_request_key(request)
            if kind == "user":
                users.add(key)
            elif kind == "team":
                teams.add(key)
        return users, teams

    def _review_request_key(
        self, request: ReviewRequest
    ) -> tuple[Literal["user", "team", "none"], str]:
        reviewer = request.requested_reviewer
        if isinstance(reviewer, PRUser) and reviewer.login:
            return "user", reviewer.login
        if isinstance(reviewer, PRTeam):
            key = reviewer.slug or reviewer.name
            if key:
                return "team", key
        return "none", ""

    async def submit_issue_comment(self, body: str) -> PRIssueComment:
        """Submit a PR-level comment and update local state."""
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment cannot be empty")

        comment = await self._service.create_issue_comment(self.pr_number, normalized)
        self._state.issue_comments.append(comment)
        self._state.issue_comments.sort(
            key=lambda item: datetime_sort_key(item.created_at)
        )
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

        target_side = cast(Literal["LEFT", "RIGHT"], side)
        self._require_inline_comment_diff_line(
            path=path,
            line=line,
            side=target_side,
        )

        comment = await self._service.create_review_comment(
            self.pr_number,
            body=normalized,
            commit_id=pr.head_sha,
            path=path,
            line=line,
            side=side,
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
        self._pending_review_local_version += 1
        return draft

    @property
    def pending_review_version(self) -> int:
        return self._pending_review_local_version

    def snapshot_pending_review(self) -> PendingReviewSnapshot:
        return PendingReviewSnapshot(
            pending_review_id=self._state.pending_review_id,
            pending_review_body=self._state.pending_review_body,
            pending_review_comments=tuple(self._state.pending_review_comments),
            version=self._pending_review_local_version,
        )

    def restore_pending_review_snapshot(
        self,
        snapshot: PendingReviewSnapshot,
    ) -> None:
        self._state.pending_review_id = snapshot.pending_review_id
        self._state.pending_review_body = snapshot.pending_review_body
        self._state.pending_review_comments = list(snapshot.pending_review_comments)
        self._pending_review_local_version += 1

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
        draft = PendingReviewComment(
            body=body,
            path=path,
            line=line,
            side=side,
            is_diff_line=self.is_inline_comment_diff_line(
                path=path,
                line=line,
                side=side,
            ),
        )
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

    def is_inline_comment_diff_line(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> bool:
        diff = self._state.file_diffs.get(path)
        if diff is None:
            return True

        for hunk in diff.hunks:
            for diff_line in hunk.lines:
                if side == "RIGHT" and diff_line.new_line_no == line:
                    return True
                if side == "LEFT" and diff_line.old_line_no == line:
                    return True
        return False

    def _require_inline_comment_diff_line(
        self,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> None:
        if self.is_inline_comment_diff_line(path=path, line=line, side=side):
            return
        raise UnsupportedInlineCommentTarget(path=path, line=line, side=side)

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

        syncable_comments = [comment for comment in comments if comment.is_diff_line]
        if not syncable_comments:
            return None

        pr = self._state.pr
        commit_id = pr.head_sha if pr is not None and pr.head_sha else None
        return await self._service.create_pending_review(
            self.pr_number,
            comments=syncable_comments,
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
            self._pending_review_local_version += 1
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
                self._state.pending_review_id = (
                    review.id if review is not None else None
                )
                if review is None:
                    self._state.pending_review_body = ""
                elif review.body:
                    self._state.pending_review_body = review.body
                return review
        except Exception:
            if (
                rollback_to is not None
                and rollback_if_version is not None
                and self._pending_review_local_version == rollback_if_version
            ):
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
        normalized = body.strip()
        pending_comments = list(self._state.pending_review_comments)
        unsupported_comment = next(
            (comment for comment in pending_comments if not comment.is_diff_line),
            None,
        )
        if unsupported_comment is not None:
            raise UnsupportedInlineCommentTarget.from_comment(unsupported_comment)
        review_body = normalized

        if event == "REQUEST_CHANGES" and not review_body:
            raise ValueError("Review body cannot be empty")
        if event == "COMMENT" and not review_body and not pending_comments:
            raise ValueError("Review body cannot be empty")

        pending_review_id = self._state.pending_review_id
        if pending_review_id is not None:
            submitted_review = await self._service.submit_pending_review(
                self.pr_number,
                pending_review_id,
                event=event,
                body=review_body if review_body else None,
            )
        else:
            submitted_review = await self._service.submit_review(
                self.pr_number,
                event=event,
                body=review_body if review_body else None,
                comments=pending_comments,
            )
        await self._remember_submitted_review(submitted_review)

        self._state.pending_review_id = None
        self._state.pending_review_body = ""
        self._state.pending_review_comments.clear()

    async def _remember_submitted_review(self, review: PRReview | None) -> None:
        if review is None or not review.id:
            return

        comments: list[PRComment] = []
        with suppress(Exception):
            comments = await self._service.list_review_comments(
                self.pr_number,
                review.id,
            )
        if comments:
            comments = [
                comment
                if comment.pull_request_review_id
                else comment.model_copy(update={"pull_request_review_id": review.id})
                for comment in comments
            ]

        self._recent_submitted_reviews[review.id] = review
        self._recent_submitted_review_comments[review.id] = comments
        if self._state.pr is not None:
            self._apply_discussion_state(self._state.pr)

    def _remember_submitted_comment(self, comment: PRComment) -> None:
        review_id = comment.pull_request_review_id
        if not review_id:
            return

        comments = self._recent_submitted_review_comments.setdefault(review_id, [])
        if not comment.id or all(existing.id != comment.id for existing in comments):
            comments.append(comment)

        if self._state.pr is not None:
            self._apply_discussion_state(self._state.pr)

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
