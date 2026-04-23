import pytest

from rit.state.store import PRStore


class FakeReviewService:
    def __init__(self) -> None:
        self.submit_review_calls: list[tuple[int, str, str | None, int]] = []
        self.submit_pending_review_calls: list[tuple[int, int, str, str | None]] = []

    async def submit_review(
        self,
        pr_number: int,
        *,
        event: str,
        body: str | None = None,
        comments=None,
    ) -> None:
        self.submit_review_calls.append((pr_number, event, body, len(comments or [])))

    async def submit_pending_review(
        self,
        pr_number: int,
        review_id: int,
        *,
        event: str,
        body: str | None = None,
    ) -> None:
        self.submit_pending_review_calls.append((pr_number, review_id, event, body))


@pytest.mark.asyncio
async def test_submit_review_passes_event_and_trimmed_body() -> None:
    store = PRStore(pr_number=123)
    service = FakeReviewService()
    store._service = service  # type: ignore[assignment]

    await store.submit_review("COMMENT", "  hello review  ")

    assert service.submit_review_calls == [(123, "COMMENT", "hello review", 0)]


@pytest.mark.asyncio
async def test_submit_review_allows_empty_body_for_approve() -> None:
    store = PRStore(pr_number=123)
    service = FakeReviewService()
    store._service = service  # type: ignore[assignment]

    await store.submit_review("APPROVE", "")

    assert service.submit_review_calls == [(123, "APPROVE", None, 0)]


@pytest.mark.asyncio
async def test_submit_review_requires_body_for_request_changes() -> None:
    store = PRStore(pr_number=123)

    with pytest.raises(ValueError, match="empty"):
        await store.submit_review("REQUEST_CHANGES", "   ")


@pytest.mark.asyncio
async def test_submit_review_includes_pending_comments_and_clears_them() -> None:
    store = PRStore(pr_number=123)
    store.save_pending_inline_comment(
        "hello inline",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    service = FakeReviewService()
    store._service = service  # type: ignore[assignment]

    await store.submit_review("COMMENT", "")

    assert service.submit_review_calls == [(123, "COMMENT", None, 1)]
    assert store.state.pending_review_comments == []


@pytest.mark.asyncio
async def test_submit_review_submits_existing_pending_review() -> None:
    store = PRStore(pr_number=123)
    store.state.pending_review_id = 91
    store.save_pending_inline_comment(
        "hello inline",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    service = FakeReviewService()
    store._service = service  # type: ignore[assignment]

    await store.submit_review("COMMENT", "summary")

    assert service.submit_pending_review_calls == [(123, 91, "COMMENT", "summary")]
    assert service.submit_review_calls == []
    assert store.state.pending_review_id is None
    assert store.state.pending_review_comments == []
