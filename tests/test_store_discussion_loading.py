import asyncio
from datetime import datetime, timezone

import pytest

from rit.services.github import PRDiscussion
from rit.state.models import (
    NodeList,
    PR,
    PRComment,
    PRIssueComment,
    PRReview,
    PRUser,
    ReviewThread,
)
from rit.state.store import PRStore


class FastThenSlowDiscussionService:
    def __init__(self) -> None:
        self.full_requested = asyncio.Event()
        self.allow_full = asyncio.Event()

    async def get_pr_discussion_fast(self, pr_number: int) -> PRDiscussion:
        return PRDiscussion(
            body="",
            reviews=[
                PRReview(
                    id=10,
                    body="fast review",
                    user=PRUser(login="alice"),
                    submitted_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                )
            ],
            issue_comments=[
                PRIssueComment(
                    id=20,
                    body="fast issue comment",
                    user=PRUser(login="bob"),
                    created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                    updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                )
            ],
            review_threads=[],
        )

    async def get_pr_discussion(self, pr_number: int) -> PRDiscussion:
        self.full_requested.set()
        await self.allow_full.wait()
        return PRDiscussion(
            body="full body",
            reviews=[],
            issue_comments=[],
            review_threads=[],
        )


class ConcurrentDiscussionService(FastThenSlowDiscussionService):
    def __init__(self) -> None:
        super().__init__()
        self.fast_started = asyncio.Event()
        self.allow_fast = asyncio.Event()

    async def get_pr_discussion_fast(self, pr_number: int) -> PRDiscussion:
        self.fast_started.set()
        await self.allow_fast.wait()
        return await super().get_pr_discussion_fast(pr_number)


class MetadataOnlyFullDiscussionService:
    def _comment(
        self,
        *,
        login: str,
        line: int | None = 12,
        original_line: int | None = None,
        side: str = "RIGHT",
    ) -> PRComment:
        return PRComment(
            id=100,
            body="Same comment",
            user=PRUser(login=login),
            path="app.py",
            line=line,
            original_line=original_line,
            side=side,
            pull_request_review_id=10,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

    async def get_pr_discussion_fast(self, pr_number: int) -> PRDiscussion:
        comment = self._comment(login="coderabbitai[bot]")
        return PRDiscussion(
            body="Same body",
            reviews=[],
            issue_comments=[],
            review_threads=[
                ReviewThread.model_validate(
                    {
                        "id": "",
                        "isResolved": False,
                        "path": "app.py",
                        "line": 12,
                        "comments": {"nodes": [comment]},
                    }
                )
            ],
        )

    async def get_pr_discussion(self, pr_number: int) -> PRDiscussion:
        comment = self._comment(
            login="coderabbitai",
            line=None,
            original_line=12,
            side="",
        )
        return PRDiscussion(
            body="Same body",
            reviews=[],
            issue_comments=[],
            review_threads=[
                ReviewThread.model_validate(
                    {
                        "id": "thread-100",
                        "isResolved": True,
                        "path": "app.py",
                        "line": 12,
                        "comments": {"nodes": [comment]},
                    }
                )
            ],
        )


@pytest.mark.asyncio
async def test_load_pr_discussion_posts_fast_discussion_before_full_metadata() -> None:
    store = PRStore(pr_number=123)
    service = FastThenSlowDiscussionService()
    store._service = service  # type: ignore[assignment]
    discussion_loaded = asyncio.Event()

    def sink(message) -> None:
        if isinstance(message, PRStore.PRDiscussionLoaded):
            discussion_loaded.set()

    store.set_message_sink(sink)

    task = asyncio.create_task(store.load_pr_discussion())
    try:
        await asyncio.wait_for(service.full_requested.wait(), timeout=1)
        await asyncio.wait_for(discussion_loaded.wait(), timeout=0.1)
    finally:
        service.allow_full.set()
        await task


@pytest.mark.asyncio
async def test_load_pr_discussion_starts_full_metadata_while_fast_loading() -> None:
    store = PRStore(pr_number=123)
    service = ConcurrentDiscussionService()
    store._service = service  # type: ignore[assignment]
    store.set_message_sink(lambda _message: None)

    task = asyncio.create_task(store.load_pr_discussion())
    try:
        await asyncio.wait_for(service.fast_started.wait(), timeout=1)
        await asyncio.wait_for(service.full_requested.wait(), timeout=0.1)
    finally:
        service.allow_fast.set()
        service.allow_full.set()
        await task


@pytest.mark.asyncio
async def test_load_pr_discussion_posts_metadata_only_when_full_content_matches() -> (
    None
):
    store = PRStore(pr_number=123)
    store._service = MetadataOnlyFullDiscussionService()  # type: ignore[assignment]
    messages = []
    store.set_message_sink(messages.append)

    await store.load_pr_discussion()

    message_names = [type(message).__name__ for message in messages]
    assert message_names.count("PRDiscussionLoaded") == 1
    assert message_names.count("PRDiscussionMetadataLoaded") == 1


def test_file_level_line_notes_keep_file_level_anchor() -> None:
    store = PRStore(pr_number=123)
    body = "Line comment on `src/app.py:6` (RIGHT):\n\nhello outside hunk"
    comment = PRComment(
        id=501,
        body=body,
        path="src/app.py",
        line=1,
        side="RIGHT",
        subjectType="file",
    )
    thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "isResolved": False,
            "path": "src/app.py",
            "line": 1,
            "diffSide": "RIGHT",
            "subjectType": "FILE",
            "comments": {"nodes": [comment]},
        }
    )
    pr = PR(
        number=123,
        review_threads_connection=NodeList(nodes=[thread]),
    )

    store._apply_discussion_state(pr)

    normalized_thread = store.state.review_threads[0]
    normalized_comment = normalized_thread.root_comment
    assert normalized_thread.line == 1
    assert normalized_thread.original_line is None
    assert normalized_thread.anchor_line == 1
    assert normalized_thread.diff_side == "RIGHT"
    assert normalized_comment is not None
    assert normalized_comment.body == body
    assert normalized_comment.line == 1
    assert normalized_comment.original_line is None
    assert normalized_comment.side == "RIGHT"
    assert store.state.comments_by_file["src/app.py"] == [normalized_comment]
    assert store.state.thread_info_cache[501].line == 1


def test_line_thread_with_line_note_shaped_body_keeps_original_anchor() -> None:
    store = PRStore(pr_number=123)
    comment = PRComment(
        id=501,
        body="Line comment on `src/app.py:6` (RIGHT):\n\nnot a fallback",
        path="src/app.py",
        line=12,
        side="RIGHT",
    )
    thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "isResolved": False,
            "path": "src/app.py",
            "line": 12,
            "diffSide": "RIGHT",
            "subjectType": "LINE",
            "comments": {"nodes": [comment]},
        }
    )
    pr = PR(
        number=123,
        review_threads_connection=NodeList(nodes=[thread]),
    )

    store._apply_discussion_state(pr)

    normalized_thread = store.state.review_threads[0]
    normalized_comment = normalized_thread.root_comment
    assert normalized_thread.anchor_line == 12
    assert normalized_comment is not None
    assert normalized_comment.body == comment.body
    assert store.state.thread_info_cache[501].line == 12
