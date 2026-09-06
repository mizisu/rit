import asyncio
from datetime import UTC, datetime

import pytest

from rit.services.github import PRDiscussion
from rit.state.models import (
    PR,
    NodeList,
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
        self.fast_requested = False

    async def get_pr_discussion_fast(self, pr_number: int) -> PRDiscussion:
        self.fast_requested = True
        return PRDiscussion(
            body="",
            reviews=[
                PRReview(
                    id=10,
                    body="fast review",
                    user=PRUser(login="alice"),
                    submitted_at=datetime(2026, 6, 1, tzinfo=UTC),
                )
            ],
            issue_comments=[
                PRIssueComment(
                    id=20,
                    body="fast issue comment",
                    user=PRUser(login="bob"),
                    created_at=datetime(2026, 6, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 6, 1, tzinfo=UTC),
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
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
            updated_at=datetime(2026, 6, 1, tzinfo=UTC),
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
async def test_load_pr_discussion_uses_one_full_request() -> None:
    store = PRStore(pr_number=123)
    service = FastThenSlowDiscussionService()
    store._service = service  # type: ignore[assignment]
    messages = []
    store.set_message_sink(messages.append)

    task = asyncio.create_task(store.load_pr_discussion())
    await asyncio.wait_for(service.full_requested.wait(), timeout=1)
    assert service.fast_requested is False
    assert not any(
        isinstance(message, PRStore.PRDiscussionLoaded) for message in messages
    )

    service.allow_full.set()
    await task

    assert (
        sum(isinstance(message, PRStore.PRDiscussionLoaded) for message in messages)
        == 1
    )


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


def test_file_level_threads_do_not_use_github_line_one_placeholder() -> None:
    store = PRStore(pr_number=123)
    body = "Line comment on `src/app.py:6` (RIGHT):\n\nhello outside hunk"
    comment = PRComment(
        id=501,
        body=body,
        path="src/app.py",
        line=1,
        side="RIGHT",
        subject_type="file",
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
    assert normalized_thread.anchor_line is None
    assert normalized_thread.diff_side == "RIGHT"
    assert normalized_comment is not None
    assert normalized_comment.body == body
    assert normalized_comment.line == 1
    assert normalized_comment.original_line is None
    assert normalized_comment.anchor_line is None
    assert store.state.comments_by_file["src/app.py"] == [normalized_comment]
    assert store.state.thread_info_cache[501].line is None


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
