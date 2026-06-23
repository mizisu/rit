from datetime import datetime, timezone

import pytest

from rit.state.models import PR, PRIssueComment, PRUser
from rit.state.store import PRStore


class FakeCommentService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    async def create_issue_comment(self, pr_number: int, body: str) -> PRIssueComment:
        self.calls.append((pr_number, body))
        return PRIssueComment(
            id=1,
            body=body,
            user=PRUser(login="alice"),
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_submit_issue_comment_updates_store_state() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123)
    service = FakeCommentService()
    store._service = service  # type: ignore[assignment]

    comment = await store.submit_issue_comment("  hello world  ")

    assert service.calls == [(123, "hello world")]
    assert comment.body == "hello world"
    assert store.state.issue_comments == [comment]
    assert store.state.pr is not None
    assert store.state.pr.issue_comments == [comment]


@pytest.mark.asyncio
async def test_submit_issue_comment_sorts_missing_and_aware_dates() -> None:
    store = PRStore(pr_number=123)
    service = FakeCommentService()
    store._service = service  # type: ignore[assignment]
    existing = PRIssueComment(
        id=2,
        body="missing date",
        user=PRUser(login="bob"),
    )
    store.state.issue_comments = [existing]

    comment = await store.submit_issue_comment("new comment")

    assert store.state.issue_comments == [existing, comment]


@pytest.mark.asyncio
async def test_submit_issue_comment_rejects_empty_body() -> None:
    store = PRStore(pr_number=123)

    with pytest.raises(ValueError, match="empty"):
        await store.submit_issue_comment("   ")
