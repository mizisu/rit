import pytest

from rit.state.models import PendingReviewComment, PRComment, PRReview, ReviewState
from rit.state.pending_review_workspace import replace_pending_review


class FakePendingReviewAdapter:
    def __init__(self, server_comments: list[PRComment] | None = None) -> None:
        self.server_comments = server_comments or []
        self.deleted: list[tuple[int, int]] = []
        self.created: list[list[tuple[str, int, str, str]]] = []

    async def list_review_comments(
        self,
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        return list(self.server_comments)

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None:
        self.deleted.append((pr_number, review_id))

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments: list[PendingReviewComment],
        body: str | None = None,
        commit_id: str | None = None,
    ) -> PRReview:
        self.created.append(
            [
                (comment.path, comment.line, comment.side, comment.body)
                for comment in comments
            ]
        )
        return PRReview.model_validate(
            {"id": 100, "state": ReviewState.PENDING, "body": body or ""}
        )


@pytest.mark.asyncio
async def test_replace_pending_review_merges_server_comments_before_delete() -> None:
    adapter = FakePendingReviewAdapter(
        [
            PRComment.model_validate(
                {
                    "id": 9,
                    "body": "server draft",
                    "path": "a.py",
                    "line": 7,
                    "side": "RIGHT",
                }
            )
        ]
    )
    local = PendingReviewComment(body="local draft", path="a.py", line=8)

    result = await replace_pending_review(
        adapter=adapter,
        pr_number=123,
        comments=[local],
        pending_review_id=91,
        pending_review_body="",
        head_sha="deadbeef",
    )

    assert adapter.deleted == [(123, 91)]
    assert adapter.created == [
        [("a.py", 7, "RIGHT", "server draft"), ("a.py", 8, "RIGHT", "local draft")]
    ]
    assert result.review is not None
    assert [comment.body for comment in result.comments] == [
        "server draft",
        "local draft",
    ]


@pytest.mark.asyncio
async def test_replace_pending_review_refuses_unverified_empty_server_comments() -> (
    None
):
    adapter = FakePendingReviewAdapter()

    with pytest.raises(RuntimeError, match="not replacing pending review"):
        await replace_pending_review(
            adapter=adapter,
            pr_number=123,
            comments=[PendingReviewComment(body="local", path="a.py", line=8)],
            pending_review_id=91,
            pending_review_body="",
            head_sha="deadbeef",
        )

    assert adapter.deleted == []
    assert adapter.created == []
