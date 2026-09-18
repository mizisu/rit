import asyncio

import pytest

from rit.state.models import (
    PR,
    NodeList,
    PendingReviewComment,
    PRComment,
    PRReview,
    ReviewState,
)
from rit.state.pending_review import ReviewSubmissionEvent
from rit.state.pending_review_workspace import PendingReviewWorkspace


class FakePendingReviewAdapter:
    def __init__(self, server_comments: list[PRComment] | None = None) -> None:
        self.server_comments = server_comments or []
        self.deleted: list[tuple[int, int]] = []
        self.created: list[list[tuple[str, int, str, str]]] = []
        self.updated: list[str] = []
        self.submitted: list[tuple[int, str, str | None]] = []
        self.review: PRReview | None = None

    async def list_review_comments(
        self,
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        return list(self.server_comments)

    async def delete_pending_review(
        self, pr_number: int, review_id: int, *, require_empty: bool = False
    ) -> None:
        if require_empty and (
            self.review is None or self.review.id != review_id or self.server_comments
        ):
            raise RuntimeError(
                "Could not verify comments; not replacing pending review"
            )
        self.deleted.append((pr_number, review_id))
        self.review = None
        self.server_comments = []

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
        self.review = PRReview(id=100, state=ReviewState.PENDING, body=body or "")
        return self.review

    async def update_pending_review(
        self, pr_number: int, review_id: int, *, body: str
    ) -> PRReview:
        if not body.strip():
            raise ValueError("Could not edit a review with a missing body.")
        self.updated.append(body)
        self.review = PRReview(id=review_id, state=ReviewState.PENDING, body=body)
        return self.review

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
        self.submitted.append((review_id, event, body))
        return PRReview(id=review_id, state=ReviewState.COMMENTED, body=body or "")

    async def submit_review(
        self,
        pr_number: int,
        *,
        event: ReviewSubmissionEvent,
        body: str | None = None,
        comments: list[PendingReviewComment] | None = None,
    ) -> PRReview:
        self.submitted.append((101, event, body))
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
@pytest.mark.parametrize("has_local_comment", [False, True])
async def test_pending_review_workspace_sync_refuses_unverified_empty_server_comments(
    has_local_comment: bool,
) -> None:
    adapter = FakePendingReviewAdapter()

    workspace = PendingReviewWorkspace(
        review_id=91,
        comments=[PendingReviewComment(body="local", path="a.py", line=8)]
        if has_local_comment
        else [],
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


def test_pending_review_workspace_local_edits_keep_canonical_order_and_validate() -> (
    None
):
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


@pytest.mark.parametrize("draft_index", [-1, 0, 1, 2])
def test_file_draft_edit_rejects_missing_or_mismatched_target(draft_index: int) -> None:
    workspace = PendingReviewWorkspace()
    workspace.save_file_comment("other file", path="a.py")
    workspace.save_inline_comment("line comment", path="b.py", line=1, side="RIGHT")
    originals = list(workspace.comments)
    revision = workspace.revision

    with pytest.raises(ValueError, match="no longer exists"):
        workspace.save_file_comment("changed", path="b.py", draft_index=draft_index)

    assert workspace.comments == originals
    assert workspace.revision == revision


@pytest.mark.asyncio
@pytest.mark.parametrize("server_body", ["server body", ""])
async def test_pending_review_workspace_sync_installs_body_before_notifying(
    monkeypatch: pytest.MonkeyPatch,
    server_body: str,
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
        adapter=adapter,
        pr_number=123,
        head_sha=lambda: "deadbeef",
        on_sync=on_sync,
    )
    assert observed == [(None, 100, server_body or "local body")]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["", "summary"])
async def test_pending_review_workspace_sync_preserves_body_and_local_only_draft(
    body: str,
) -> None:
    draft = PendingReviewComment(body="local", path="a.py", line=99, is_diff_line=False)
    workspace = PendingReviewWorkspace(body=body, comments=[draft])
    review = await workspace.sync(
        adapter=FakePendingReviewAdapter(),
        pr_number=123,
        head_sha=lambda: "deadbeef",
        on_sync=lambda *_: None,
    )
    assert (review is not None) == bool(body)
    assert workspace.review_id == (100 if body else None)
    assert workspace.body == body
    assert workspace.comments == [draft]
    assert workspace.drafts_are_canonical


@pytest.mark.asyncio
@pytest.mark.parametrize("body_only", [False, True])
async def test_pending_review_workspace_submission_waits_for_sync_and_remembers_before_clear(
    monkeypatch: pytest.MonkeyPatch,
    body_only: bool,
) -> None:
    workspace = PendingReviewWorkspace()
    workspace.set_body("summary")
    draft = workspace.save_inline_comment("draft", path="a.py", line=7, side="RIGHT")
    adapter = FakePendingReviewAdapter()
    create = adapter.create_pending_review
    create_started = asyncio.Event()
    allow_create = asyncio.Event()
    remember_started = asyncio.Event()
    allow_remember = asyncio.Event()
    submit_started = asyncio.Event()

    async def submit() -> None:
        submit_started.set()
        await workspace.submit(
            "COMMENT",
            "summary",
            adapter=adapter,
            pr_number=123,
            remember_submitted=remember_submitted,
        )

    async def blocking_create(*args, **kwargs) -> PRReview:
        create_started.set()
        await allow_create.wait()
        return await create(*args, **kwargs)

    async def remember_submitted(review: PRReview | None) -> None:
        assert review is not None
        remember_started.set()
        await allow_remember.wait()

    monkeypatch.setattr(adapter, "create_pending_review", blocking_create)
    sync_task = asyncio.create_task(
        workspace.sync(
            adapter=adapter,
            pr_number=123,
            head_sha=lambda: "deadbeef",
            on_sync=lambda *_: None,
            body_only=body_only,
        )
    )
    await asyncio.wait_for(create_started.wait(), timeout=1)
    submit_task = asyncio.create_task(submit())
    try:
        await asyncio.wait_for(submit_started.wait(), timeout=1)
        assert not remember_started.is_set()
        assert adapter.submitted == []
        allow_create.set()
        await asyncio.wait_for(remember_started.wait(), timeout=1)
        assert [comment.body for comment in workspace.comments] == [draft.body]
        assert adapter.submitted == [(100, "COMMENT", "summary")]
        assert workspace.body == "summary"
        assert workspace.drafts_are_canonical
        allow_remember.set()
        await asyncio.wait_for(submit_task, timeout=1)
        assert sync_task.done()
        assert workspace.comments == []
        assert workspace.review_id is None
        assert workspace.body == ""
        assert not workspace.drafts_are_canonical
    finally:
        allow_remember.set()
        allow_create.set()
        await asyncio.gather(sync_task, submit_task, return_exceptions=True)


async def _save_body(
    workspace: PendingReviewWorkspace, adapter: FakePendingReviewAdapter
) -> None:
    await workspace.sync(
        adapter=adapter,
        pr_number=123,
        head_sha=lambda: "head",
        on_sync=lambda *_: None,
        body_only=True,
    )


async def test_review_body_round_trip_preserves_inline_drafts() -> None:
    workspace = PendingReviewWorkspace()
    adapter = FakePendingReviewAdapter()
    workspace.set_body("  ")
    await _save_body(workspace, adapter)
    assert adapter.created == []

    workspace.set_body("summary\n\n- unfinished ")
    await _save_body(workspace, adapter)
    assert adapter.created == [[]]
    assert adapter.review is not None
    assert adapter.review.body == "summary\n\n- unfinished "

    async def load_discussion() -> PR:
        assert adapter.review is not None
        return PR(number=123, reviews_connection=NodeList(nodes=[adapter.review]))

    workspace = PendingReviewWorkspace()
    await workspace.refresh(load_discussion, adapter=adapter, pr_number=123)
    assert workspace.body == "summary\n\n- unfinished "
    await workspace.queue_inline_comment(
        "inline",
        path="a.py",
        line=7,
        side="RIGHT",
        adapter=adapter,
        pr_number=123,
        head_sha=lambda: "head",
        on_sync=lambda *_: None,
    )
    original_comments = list(workspace.comments)
    workspace.set_body("updated summary")
    await _save_body(workspace, adapter)
    await _save_body(workspace, adapter)
    assert adapter.updated == ["updated summary"]
    assert workspace.comments == original_comments
    assert len(adapter.created) == 2
    assert len(adapter.deleted) == 1
    assert adapter.submitted == []

    await workspace.remove_comment_at(
        0,
        adapter=adapter,
        pr_number=123,
        head_sha=lambda: "head",
        on_sync=lambda *_: None,
    )
    assert workspace.comments == []
    assert workspace.body == "updated summary"
    assert adapter.review is not None and adapter.review.body == workspace.body

    workspace.set_body("")
    await _save_body(workspace, adapter)
    assert workspace.review_id is None
    assert adapter.review is None

    async def remember(review: PRReview | None) -> None:
        assert review is not None

    await workspace.submit(
        "APPROVE", "", adapter=adapter, pr_number=123, remember_submitted=remember
    )
    await _save_body(workspace, adapter)
    assert adapter.submitted == [(101, "APPROVE", None)]
    assert workspace.review_id is None
    assert workspace.body == ""
    assert len(adapter.created) == 3


@pytest.mark.parametrize("body", ["", " \n "])
@pytest.mark.parametrize("with_comments", [False, True])
async def test_clearing_review_body_preserves_comments_without_empty_update(
    body: str, with_comments: bool
) -> None:
    server_comments = (
        [
            PRComment(id=9, body="inline", path="a.py", line=7, side="RIGHT"),
            PRComment(id=10, body="whole file", path="b.py", subject_type="file"),
        ]
        if with_comments
        else []
    )
    adapter = FakePendingReviewAdapter(server_comments)
    adapter.review = PRReview(id=91, state=ReviewState.PENDING, body="saved")
    workspace = PendingReviewWorkspace(review_id=91, body="saved")
    workspace.set_body(body)

    await _save_body(workspace, adapter)
    await _save_body(workspace, adapter)

    assert adapter.updated == []
    assert adapter.deleted == [(123, 91)]
    assert adapter.submitted == []
    assert not workspace.body.strip()
    if with_comments:
        assert adapter.review is not None and adapter.review.body == ""
        assert workspace.review_id == adapter.review.id
        assert [
            (comment.body, comment.path, comment.line) for comment in workspace.comments
        ] == [
            ("inline", "a.py", 7),
            ("whole file", "b.py", 0),
        ]
        assert workspace.comments[1].is_file_level
    else:
        assert workspace.review_id is None
        assert adapter.created == []
        assert workspace.comments == []


@pytest.mark.parametrize("with_comments", [False, True])
async def test_uneditable_pending_review_preserves_summary_and_comments(
    monkeypatch: pytest.MonkeyPatch, with_comments: bool
) -> None:
    adapter = FakePendingReviewAdapter(
        [PRComment(id=9, body="inline", path="a.py", line=7, side="RIGHT")]
        if with_comments
        else []
    )
    adapter.review = PRReview(id=91, state=ReviewState.PENDING, body="")
    workspace = PendingReviewWorkspace(review_id=91)

    async def uneditable(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(adapter, "update_pending_review", uneditable)
    workspace.set_body("new summary")
    await _save_body(workspace, adapter)

    assert workspace.body == "new summary"
    assert adapter.review is not None and adapter.review.body == "new summary"
    assert workspace.review_id == adapter.review.id
    assert adapter.deleted == [(123, 91)]
    assert [comment.body for comment in workspace.comments] == (
        ["inline"] if with_comments else []
    )
    assert adapter.submitted == []


async def test_review_body_sync_preserves_newer_text_and_coalesces_queued_saves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = PendingReviewWorkspace()
    adapter = FakePendingReviewAdapter()
    started, release = asyncio.Event(), asyncio.Event()
    create = adapter.create_pending_review

    async def blocking_create(*args, **kwargs) -> PRReview:
        started.set()
        await release.wait()
        return await create(*args, **kwargs)

    monkeypatch.setattr(adapter, "create_pending_review", blocking_create)
    workspace.set_body("first")
    first = asyncio.create_task(_save_body(workspace, adapter))
    await asyncio.wait_for(started.wait(), timeout=1)
    workspace.set_body("latest")
    queued = [asyncio.create_task(_save_body(workspace, adapter)) for _ in range(2)]
    release.set()
    await asyncio.gather(first, *queued)

    assert workspace.body == "latest"
    assert adapter.review is not None and adapter.review.body == "latest"
    assert adapter.created == [[]]
    assert adapter.updated == ["latest"]
    assert adapter.deleted == []
    assert adapter.submitted == []


@pytest.mark.parametrize("body", ["new text", ""])
async def test_failed_body_save_survives_refresh_and_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    workspace = PendingReviewWorkspace(review_id=91, body="saved")
    adapter = FakePendingReviewAdapter()
    adapter.review = PRReview(id=91, state=ReviewState.PENDING, body="saved")
    operation = "update_pending_review" if body else "delete_pending_review"
    save = getattr(adapter, operation)

    async def fail(*args, **kwargs) -> PRReview:
        raise RuntimeError("offline")

    async def load_discussion() -> PR:
        return PR(
            number=123,
            reviews_connection=NodeList(nodes=[PRReview(id=91, body="saved")]),
        )

    monkeypatch.setattr(adapter, operation, fail)
    workspace.set_body(body)
    with pytest.raises(RuntimeError, match="offline"):
        await _save_body(workspace, adapter)
    await workspace.refresh(load_discussion, adapter=adapter, pr_number=123)
    assert workspace.body == body

    monkeypatch.setattr(adapter, operation, save)
    await _save_body(workspace, adapter)
    assert adapter.updated == ([body] if body else [])
    assert adapter.created == []
    assert adapter.deleted == ([] if body else [(123, 91)])


@pytest.mark.parametrize("body", ["", "new summary"])
async def test_failed_review_recreation_keeps_comments_for_retry(
    monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    adapter = FakePendingReviewAdapter(
        [PRComment(id=9, body="inline", path="a.py", line=7, side="RIGHT")]
    )
    workspace = PendingReviewWorkspace(review_id=91, body="saved")
    create = adapter.create_pending_review

    async def fail(*args, **kwargs) -> PRReview:
        raise RuntimeError("offline")

    async def uneditable(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(adapter, "create_pending_review", fail)
    monkeypatch.setattr(adapter, "update_pending_review", uneditable)
    workspace.set_body(body)
    with pytest.raises(RuntimeError, match="offline"):
        await _save_body(workspace, adapter)
    assert workspace.body == body
    assert workspace.review_id is None
    assert [comment.body for comment in workspace.comments] == ["inline"]

    monkeypatch.setattr(adapter, "create_pending_review", create)
    await _save_body(workspace, adapter)
    assert adapter.deleted == [(123, 91)]
    assert adapter.created == [[("a.py", 7, "RIGHT", "inline")]]
    assert workspace.review_id == 100
    assert adapter.review is not None and adapter.review.body == body
    assert adapter.submitted == []


async def test_inline_save_rollback_keeps_newly_edited_review_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = PendingReviewWorkspace(body="saved")
    adapter = FakePendingReviewAdapter()

    async def fail(*args, **kwargs) -> PRReview:
        raise RuntimeError("offline")

    async def edit_body() -> None:
        workspace.set_body("newer summary")

    monkeypatch.setattr(adapter, "create_pending_review", fail)
    with pytest.raises(RuntimeError, match="offline"):
        await workspace.queue_inline_comment(
            "inline",
            path="a.py",
            line=7,
            side="RIGHT",
            adapter=adapter,
            pr_number=123,
            head_sha=lambda: "head",
            on_sync=lambda *_: None,
            after_local_save=edit_body,
        )
    assert workspace.body == "newer summary"
    assert workspace.comments == []
