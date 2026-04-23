import pytest

from rit.state.models import PR, PRComment, PRReview, PRUser, ReviewState
from rit.state.store import PRStore


class FakeInlineCommentService:
    def __init__(self) -> None:
        self.inline_comment_calls: list[tuple[int, str, str, str, int, str]] = []
        self.create_pending_review_calls: list[list[tuple[str, int, str, str]]] = []
        self.delete_pending_review_calls: list[tuple[int, int]] = []
        self.list_review_comments_result: list[PRComment] = []
        self.next_review_id = 100

    async def create_review_comment(
        self,
        pr_number: int,
        *,
        body: str,
        commit_id: str,
        path: str,
        line: int,
        side: str,
    ) -> PRComment:
        self.inline_comment_calls.append((pr_number, body, commit_id, path, line, side))
        return PRComment(
            id=1,
            body=body,
            user=PRUser(login="alice"),
            path=path,
            line=line,
            side=side,
        )

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments,
        body=None,
        commit_id=None,
    ) -> PRReview:
        self.create_pending_review_calls.append(
            [
                (comment.path, comment.line, comment.side, comment.body)
                for comment in comments
            ]
        )
        review = PRReview(
            id=self.next_review_id, state=ReviewState.PENDING, body=body or ""
        )
        self.next_review_id += 1
        return review

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None:
        self.delete_pending_review_calls.append((pr_number, review_id))

    async def list_review_comments(
        self, pr_number: int, review_id: int
    ) -> list[PRComment]:
        return list(self.list_review_comments_result)


@pytest.mark.asyncio
async def test_submit_inline_comment_uses_head_sha_and_target() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    comment = await store.submit_inline_comment(
        "  hello inline  ",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    assert service.inline_comment_calls == [
        (123, "hello inline", "deadbeef", "src/app.py", 7, "RIGHT")
    ]
    assert comment.path == "src/app.py"
    assert comment.line == 7


@pytest.mark.asyncio
async def test_submit_inline_comment_requires_head_sha() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123)

    with pytest.raises(ValueError, match="head SHA"):
        await store.submit_inline_comment(
            "hello",
            path="src/app.py",
            line=7,
            side="RIGHT",
        )


def test_save_pending_inline_comment_replaces_existing_target() -> None:
    store = PRStore(pr_number=123)

    first = store.save_pending_inline_comment(
        "  first body  ",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    second = store.save_pending_inline_comment(
        "updated body",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    assert first.body == "first body"
    assert second.body == "updated body"
    assert store.state.pending_review_comments == [second]


def test_delete_pending_inline_comment_removes_matching_target() -> None:
    store = PRStore(pr_number=123)
    store.save_pending_inline_comment(
        "hello",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    deleted = store.delete_pending_inline_comment(
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    assert deleted is True
    assert store.state.pending_review_comments == []


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_syncs_pending_review() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    draft = await store.upsert_pending_inline_comment(
        "hello",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    assert draft.body == "hello"
    assert service.create_pending_review_calls == [
        [("src/app.py", 7, "RIGHT", "hello")]
    ]
    assert store.state.pending_review_id == 100


@pytest.mark.asyncio
async def test_remove_pending_inline_comment_deletes_server_pending_review() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    await store.upsert_pending_inline_comment(
        "hello",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    deleted = await store.remove_pending_inline_comment(
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    assert deleted is True
    assert service.delete_pending_review_calls == [(123, 100)]
    assert store.state.pending_review_id is None
    assert store.state.pending_review_comments == []


@pytest.mark.asyncio
async def test_load_pr_data_restores_pending_review_comments() -> None:
    store = PRStore(pr_number=123)
    service = FakeInlineCommentService()
    pending_review = PRReview(id=91, state=ReviewState.PENDING, body="pending body")
    service.list_review_comments_result = [
        PRComment(body="hello", path="src/app.py", line=7, side="RIGHT")
    ]

    async def get_pr_all(pr_number: int) -> PR:
        return PR(number=pr_number, reviews_connection={"nodes": [pending_review]})

    service.get_pr_all = get_pr_all  # type: ignore[method-assign]
    store._service = service  # type: ignore[assignment]

    await store._load_pr_data()

    assert store.state.pending_review_id == 91
    assert store.state.pending_review_body == "pending body"
    assert store.state.pending_review_comments == [
        store.state.pending_review_comments[0]
    ]
    assert store.state.pending_review_comments[0].body == "hello"
