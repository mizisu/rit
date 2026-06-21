import pytest

from rit.core.diff import parse_patch
from rit.state.models import PR, PRComment, PendingReviewComment, PRReview, ReviewState
from rit.state.store import PRStore, UnsupportedInlineCommentTarget


class FakeReviewService:
    def __init__(self) -> None:
        self.submit_review_calls: list[tuple[int, str, str | None, int]] = []
        self.submit_pending_review_calls: list[tuple[int, int, str, str | None]] = []
        self.create_pending_review_calls: list[
            tuple[int, list[tuple[str, int, str, str]], str | None]
        ] = []
        self.list_review_comments_calls: list[tuple[int, int]] = []
        self.submitted_review = PRReview(id=91, state=ReviewState.COMMENTED)
        self.pending_review = PRReview(id=88, state=ReviewState.PENDING)
        self.review_comments_result: list[PRComment] = []
        self.pr_all_result = PR(number=123)

    async def submit_review(
        self,
        pr_number: int,
        *,
        event: str,
        body: str | None = None,
        comments=None,
    ) -> PRReview:
        self.submit_review_calls.append((pr_number, event, body, len(comments or [])))
        return self.submitted_review

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments=None,
        body: str | None = None,
        commit_id: str | None = None,
    ) -> PRReview:
        self.create_pending_review_calls.append(
            (
                pr_number,
                [
                    (comment.path, comment.line, comment.side, comment.body)
                    for comment in comments or []
                ],
                body,
            )
        )
        return self.pending_review

    async def submit_pending_review(
        self,
        pr_number: int,
        review_id: int,
        *,
        event: str,
        body: str | None = None,
    ) -> PRReview:
        self.submit_pending_review_calls.append((pr_number, review_id, event, body))
        return self.submitted_review

    async def list_review_comments(
        self, pr_number: int, review_id: int
    ) -> list[PRComment]:
        self.list_review_comments_calls.append((pr_number, review_id))
        return list(self.review_comments_result)

    async def get_pr_all(self, pr_number: int) -> PR:
        return self.pr_all_result


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
async def test_submit_review_rejects_unsupported_inline_targets() -> None:
    store = PRStore(pr_number=123)
    store.state.file_diffs = {
        "src/app.py": parse_patch(
            "@@ -2,2 +2,3 @@\n line 2\n+line 3\n line 4",
            "src/app.py",
        )
    }
    store.state.pending_review_comments = [
        PendingReviewComment(
            body="hello outside hunk",
            path="src/app.py",
            line=6,
            side="RIGHT",
            is_diff_line=False,
        )
    ]
    service = FakeReviewService()
    store._service = service  # type: ignore[assignment]

    with pytest.raises(UnsupportedInlineCommentTarget, match="outside the PR diff"):
        await store.submit_review("COMMENT", "")

    assert service.create_pending_review_calls == []
    assert service.submit_pending_review_calls == []
    assert service.submit_review_calls == []


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


@pytest.mark.asyncio
async def test_submitted_pending_comments_survive_stale_review_refresh() -> None:
    store = PRStore(pr_number=123)
    store.state.pending_review_id = 91
    store.save_pending_inline_comment(
        "hello inline",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    service = FakeReviewService()
    submitted_comment = PRComment(
        id=501,
        body="hello inline",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    service.submitted_review = PRReview(id=91, state=ReviewState.COMMENTED)
    service.review_comments_result = [submitted_comment]
    service.pr_all_result = PR(number=123)
    store._service = service  # type: ignore[assignment]

    await store.submit_review("COMMENT", "")
    await store.refresh_review_data()

    assert service.list_review_comments_calls == [(123, 91)]
    assert store.state.pending_review_comments == []
    assert store.state.comments_by_file["src/app.py"] == [submitted_comment]
    assert store.state.review_threads[0].root_comment == submitted_comment
