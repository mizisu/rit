import asyncio

import pytest

from rit.state.models import PendingReviewComment, PRComment, PRReview, ReviewState
from rit.state.pending_review import ReviewSubmissionEvent
from rit.state.pending_review_workspace import PendingReviewWorkspace


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
        self.server_comments = [
            PRComment(
                id=200 + index,
                body=comment.body,
                path=comment.path,
                line=None if comment.is_file_level else comment.line,
                side="" if comment.is_file_level else comment.side,
                subject_type=comment.subject_type,
                pull_request_review_id=100,
            )
            for index, comment in enumerate(comments)
        ]
        return PRReview.model_validate(
            {"id": 100, "state": ReviewState.PENDING, "body": body or ""}
        )

    async def update_review_comment(self, comment_node_id: str, body: str) -> PRComment:
        return PRComment(node_id=comment_node_id, body=body)

    async def submit_pending_review(
        self,
        pr_number: int,
        review_id: int,
        *,
        event: ReviewSubmissionEvent,
        body: str | None = None,
    ) -> PRReview:
        return PRReview(id=review_id, state=ReviewState.COMMENTED, body=body or "")

    async def submit_review(
        self,
        pr_number: int,
        *,
        event: ReviewSubmissionEvent,
        body: str | None = None,
        comments: list[PendingReviewComment] | None = None,
    ) -> PRReview:
        return PRReview(id=101, state=ReviewState.COMMENTED, body=body or "")


@pytest.mark.asyncio
async def test_pending_review_workspace_sync_merges_server_comments_before_delete() -> (
    None
):
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

    workspace = PendingReviewWorkspace(review_id=91, comments=[local])
    review = await workspace.sync(
        adapter=adapter,
        pr_number=123,
        head_sha=lambda: "deadbeef",
        on_sync=lambda *_: None,
    )

    assert adapter.deleted == [(123, 91)]
    assert adapter.created == [
        [("a.py", 7, "RIGHT", "server draft"), ("a.py", 8, "RIGHT", "local draft")]
    ]
    assert review is not None
    assert workspace.review_id == review.id
    assert workspace.obsolete_review_ids == {91}
    assert workspace.drafts_are_canonical
    assert [comment.body for comment in workspace.comments] == [
        "server draft",
        "local draft",
    ]
    assert [comment.review_comment_id for comment in workspace.comments] == [200, 201]


@pytest.mark.asyncio
async def test_pending_review_workspace_sync_refuses_unverified_empty_server_comments() -> (
    None
):
    adapter = FakePendingReviewAdapter()

    workspace = PendingReviewWorkspace(
        review_id=91,
        comments=[PendingReviewComment(body="local", path="a.py", line=8)],
    )
    with pytest.raises(RuntimeError, match="not replacing pending review"):
        await workspace.sync(
            adapter=adapter,
            pr_number=123,
            head_sha=lambda: "deadbeef",
            on_sync=lambda *_: None,
        )

    assert adapter.deleted == []
    assert adapter.created == []


@pytest.mark.asyncio
async def test_pending_review_workspace_sync_recreates_file_level_comments_in_review() -> (
    None
):
    adapter = FakePendingReviewAdapter(
        [
            PRComment(
                id=9,
                body="whole file",
                path="a.py",
                line=1,
                side="RIGHT",
                subject_type="file",
            )
        ]
    )
    local = PendingReviewComment(body="local draft", path="a.py", line=8)

    workspace = PendingReviewWorkspace(review_id=91, comments=[local])
    review = await workspace.sync(
        adapter=adapter,
        pr_number=123,
        head_sha=lambda: "deadbeef",
        on_sync=lambda *_: None,
    )

    assert adapter.deleted == [(123, 91)]
    assert adapter.created == [
        [
            ("a.py", 0, "RIGHT", "whole file"),
            ("a.py", 8, "RIGHT", "local draft"),
        ]
    ]
    assert review is not None
    assert workspace.review_id == review.id
    assert workspace.obsolete_review_ids == {91}
    assert workspace.drafts_are_canonical
    assert [(comment.body, comment.subject_type) for comment in workspace.comments] == [
        ("whole file", "file"),
        ("local draft", "line"),
    ]


def test_pending_review_workspace_local_edits_keep_canonical_order_and_validate() -> None:
    workspace = PendingReviewWorkspace()
    later = workspace.save_inline_comment(" later ", path="b.py", line=7, side="RIGHT")
    earlier = workspace.save_file_comment(" whole file ", path="a.py")
    assert [draft.body for draft in workspace.comments] == ["whole file", "later"]
    assert workspace.comments == [earlier, later]
    assert earlier.is_file_level
    assert workspace.drafts_are_canonical
    revision = workspace.revision
    assert not workspace.delete_inline_comment(path="missing", line=7, side="RIGHT")
    assert workspace.revision == revision
    assert workspace.delete_inline_comment(path="b.py", line=7, side="RIGHT")
    assert workspace.comments == [earlier]
    for body in ["", "   "]:
        with pytest.raises(ValueError, match="empty"):
            workspace.save_inline_comment(body, path="a.py", line=7, side="RIGHT")
        with pytest.raises(ValueError, match="empty"):
            workspace.save_file_comment(body, path="a.py")
    with pytest.raises(ValueError, match="path"):
        workspace.save_file_comment("body", path="")
    assert workspace.comments == [earlier]


@pytest.mark.asyncio
@pytest.mark.parametrize("server_body", ["server body", ""])
async def test_pending_review_workspace_sync_installs_body_before_notifying(
    monkeypatch: pytest.MonkeyPatch, server_body: str,
) -> None:
    workspace = PendingReviewWorkspace(body="local body")
    workspace.save_inline_comment("draft", path="a.py", line=7, side="RIGHT")
    adapter = FakePendingReviewAdapter()
    create = adapter.create_pending_review

    async def create_with_body(*args, **kwargs) -> PRReview:
        review = await create(*args, **kwargs)
        return review.model_copy(update={"body": server_body})

    monkeypatch.setattr(adapter, "create_pending_review", create_with_body)
    observed = []

    def on_sync(previous_id: int | None, review: PRReview | None) -> None:
        observed.append((previous_id, workspace.review_id, workspace.body))
        assert workspace.comments[0].review_comment_id == 200

    await workspace.sync(
        adapter=adapter, pr_number=123, head_sha=lambda: "deadbeef", on_sync=on_sync,
    )
    assert observed == [(None, 100, server_body or "local body")]


@pytest.mark.asyncio
async def test_pending_review_workspace_sync_clears_identity_but_keeps_local_only_draft() -> None:
    draft = PendingReviewComment(body="local", path="a.py", line=99, is_diff_line=False)
    workspace = PendingReviewWorkspace(body="summary", comments=[draft])
    review = await workspace.sync(
        adapter=FakePendingReviewAdapter(), pr_number=123,
        head_sha=lambda: "deadbeef", on_sync=lambda *_: None,
    )
    assert review is None
    assert workspace.review_id is None
    assert workspace.body == ""
    assert workspace.comments == [draft]
    assert workspace.drafts_are_canonical


@pytest.mark.asyncio
async def test_pending_review_workspace_submission_does_not_wait_for_sync_and_remembers_before_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = PendingReviewWorkspace(body="summary")
    draft = workspace.save_inline_comment("draft", path="a.py", line=7, side="RIGHT")
    adapter = FakePendingReviewAdapter()
    create = adapter.create_pending_review
    create_started = asyncio.Event()
    allow_create = asyncio.Event()
    remember_started = asyncio.Event()
    allow_remember = asyncio.Event()

    async def blocking_create(*args, **kwargs) -> PRReview:
        create_started.set()
        await allow_create.wait()
        return await create(*args, **kwargs)

    async def remember_submitted(review: PRReview | None) -> None:
        assert review is not None
        remember_started.set()
        await allow_remember.wait()

    monkeypatch.setattr(adapter, "create_pending_review", blocking_create)
    sync_task = asyncio.create_task(workspace.sync(
        adapter=adapter, pr_number=123, head_sha=lambda: "deadbeef",
        on_sync=lambda *_: None,
    ))
    await asyncio.wait_for(create_started.wait(), timeout=1)
    submit_task = asyncio.create_task(workspace.submit(
        "COMMENT", "summary", adapter=adapter, pr_number=123,
        remember_submitted=remember_submitted,
    ))
    try:
        await asyncio.wait_for(remember_started.wait(), timeout=1)
        assert workspace.comments == [draft]
        assert workspace.body == "summary"
        assert workspace.drafts_are_canonical
        allow_remember.set()
        await asyncio.wait_for(submit_task, timeout=1)
        assert not sync_task.done()
        assert workspace.comments == []
        assert workspace.review_id is None
        assert workspace.body == ""
        assert not workspace.drafts_are_canonical
    finally:
        allow_remember.set()
        allow_create.set()
        await asyncio.gather(sync_task, submit_task, return_exceptions=True)
