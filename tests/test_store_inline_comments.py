import asyncio

import pytest

from rit.core.diff import parse_patch
from rit.state.models import (
    PR,
    NodeList,
    PendingReviewComment,
    PRComment,
    PRIssueComment,
    PRReview,
    PRUser,
    ReviewState,
    ReviewThread,
)
from rit.state.store import PRStore, UnsupportedInlineCommentTarget


def _created_review_comment(
    review_id: int,
    index: int,
    comment: PendingReviewComment,
) -> PRComment:
    data: dict[str, object] = {
        "id": review_id * 1000 + index,
        "body": comment.body,
        "path": comment.path,
        "line": comment.line,
        "side": comment.side,
        "pullRequestReview": review_id,
    }
    if comment.start_line is not None:
        data["start_line"] = comment.start_line
        data["start_side"] = comment.start_side or comment.side
    return PRComment.model_validate(data)


class FakeInlineCommentService:
    def __init__(self) -> None:
        self.inline_comment_calls: list[tuple[int, str, str, str, int, str]] = []
        self.issue_comment_calls: list[tuple[int, str]] = []
        self.create_pending_review_calls: list[list[tuple[str, int, str, str]]] = []
        self.delete_pending_review_calls: list[tuple[int, int]] = []
        self.list_review_comments_result: list[PRComment] = []
        self.next_review_id = 100
        self.pr_all_result = PR(number=123)

    async def create_review_comment(
        self,
        pr_number: int,
        *,
        body: str,
        commit_id: str,
        path: str,
        line: int,
        side: str,
        start_line: int | None = None,
        start_side: str | None = None,
    ) -> PRComment:
        self.inline_comment_calls.append((pr_number, body, commit_id, path, line, side))
        return PRComment(
            id=1,
            body=body,
            user=PRUser(login="alice"),
            path=path,
            line=line,
            side=side,
            start_line=start_line,
            start_side=start_side or "",
            pull_request_review_id=90,
        )

    async def create_issue_comment(self, pr_number: int, body: str) -> PRIssueComment:
        self.issue_comment_calls.append((pr_number, body))
        return PRIssueComment(id=2, body=body, user=PRUser(login="alice"))

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
        review_id = self.next_review_id
        self.list_review_comments_result = [
            _created_review_comment(review_id, index, comment)
            for index, comment in enumerate(comments, start=1)
        ]
        review = PRReview(id=review_id, state=ReviewState.PENDING, body=body or "")
        self.next_review_id += 1
        return review

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None:
        self.delete_pending_review_calls.append((pr_number, review_id))

    async def list_review_comments(
        self, pr_number: int, review_id: int
    ) -> list[PRComment]:
        return list(self.list_review_comments_result)

    async def get_pr_all(self, pr_number: int) -> PR:
        return self.pr_all_result


class BlockingPendingReviewService(FakeInlineCommentService):
    def __init__(self) -> None:
        super().__init__()
        self.create_started = asyncio.Event()
        self.allow_create = asyncio.Event()
        self.delete_started = asyncio.Event()
        self.allow_delete = asyncio.Event()
        self.fail_create = False
        self.block_delete = False

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments,
        body=None,
        commit_id=None,
    ) -> PRReview:
        self.create_started.set()
        await self.allow_create.wait()
        if self.fail_create:
            raise RuntimeError("sync failed")
        return await super().create_pending_review(
            pr_number,
            comments=comments,
            body=body,
            commit_id=commit_id,
        )

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None:
        self.delete_started.set()
        if self.block_delete:
            await self.allow_delete.wait()
        await super().delete_pending_review(pr_number, review_id)


class BlockingPRDataService(FakeInlineCommentService):
    def __init__(self) -> None:
        super().__init__()
        self.get_started = asyncio.Event()
        self.allow_get = asyncio.Event()

    async def get_pr_all(self, pr_number: int) -> PR:
        self.get_started.set()
        await self.allow_get.wait()
        return self.pr_all_result


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


@pytest.mark.asyncio
async def test_submit_inline_comment_on_unchanged_line_is_rejected() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.file_diffs = {
        "src/app.py": parse_patch(
            "@@ -2,2 +2,3 @@\n line 2\n+line 3\n line 4",
            "src/app.py",
        )
    }
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    with pytest.raises(UnsupportedInlineCommentTarget, match="outside the PR diff"):
        await store.submit_inline_comment(
            "hello outside hunk",
            path="src/app.py",
            line=6,
            side="RIGHT",
        )

    assert service.inline_comment_calls == []
    assert service.issue_comment_calls == []


@pytest.mark.asyncio
async def test_submit_inline_comment_preserves_multiline_range() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    comment = await store.submit_inline_comment(
        "hello",
        path="src/app.py",
        start_line=5,
        line=7,
        start_side="RIGHT",
        side="RIGHT",
    )

    assert comment.start_line == 5
    assert comment.start_side == "RIGHT"


def test_save_pending_inline_comment_allows_multiple_drafts_on_same_line() -> None:
    store = PRStore(pr_number=123)

    first = store.save_pending_inline_comment(
        "  first body  ",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    second = store.save_pending_inline_comment(
        "second body",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    assert first.body == "first body"
    assert second.body == "second body"
    assert store.state.pending_review_comments == [first, second]


def test_save_pending_inline_comment_preserves_multiline_range() -> None:
    store = PRStore(pr_number=123)

    draft = store.save_pending_inline_comment(
        "hello",
        path="src/app.py",
        start_line=5,
        line=7,
        start_side="RIGHT",
        side="RIGHT",
    )

    assert draft.start_line == 5
    assert draft.start_side == "RIGHT"


def test_save_pending_inline_comment_replaces_selected_draft() -> None:
    store = PRStore(pr_number=123)
    store.save_pending_inline_comment(
        "first body",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    second = store.save_pending_inline_comment(
        "updated body",
        path="src/app.py",
        line=7,
        side="RIGHT",
        draft_index=0,
    )

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
    previous_version = store.pending_review_version

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
    assert store.pending_review_version == previous_version + 2


@pytest.mark.asyncio
async def test_queue_pending_inline_comment_adds_multiple_drafts_on_same_line() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    first = await store.queue_pending_inline_comment(
        "first",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    second = await store.queue_pending_inline_comment(
        "second",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    assert [comment.body for comment in store.state.pending_review_comments] == [
        first.body,
        second.body,
    ]
    assert service.create_pending_review_calls == [
        [("src/app.py", 7, "RIGHT", "first")],
        [
            ("src/app.py", 7, "RIGHT", "first"),
            ("src/app.py", 7, "RIGHT", "second"),
        ],
    ]


@pytest.mark.asyncio
async def test_queue_pending_inline_comment_edits_selected_draft_without_dropping_siblings() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    await store.queue_pending_inline_comment(
        "first",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    await store.queue_pending_inline_comment(
        "second",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    updated = await store.queue_pending_inline_comment(
        "updated second",
        path="src/app.py",
        line=7,
        side="RIGHT",
        draft_index=1,
    )

    assert updated.body == "updated second"
    assert [comment.body for comment in store.state.pending_review_comments] == [
        "first",
        "updated second",
    ]
    assert service.create_pending_review_calls[-1] == [
        ("src/app.py", 7, "RIGHT", "first"),
        ("src/app.py", 7, "RIGHT", "updated second"),
    ]


@pytest.mark.asyncio
async def test_remove_pending_inline_comment_deletes_selected_draft_without_dropping_siblings() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    await store.queue_pending_inline_comment(
        "first",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    await store.queue_pending_inline_comment(
        "second",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    deleted = await store.remove_pending_inline_comment(
        path="src/app.py",
        line=7,
        side="RIGHT",
        draft_index=1,
    )

    assert deleted is True
    assert [comment.body for comment in store.state.pending_review_comments] == [
        "first"
    ]
    assert service.create_pending_review_calls[-1] == [
        ("src/app.py", 7, "RIGHT", "first")
    ]


@pytest.mark.asyncio
async def test_remove_pending_inline_comment_marks_replaced_review_obsolete() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 91
    store.state.reviews = [PRReview(id=91, state=ReviewState.PENDING)]
    store.state.review_threads = [
        ReviewThread(
            path="src/app.py",
            line=7,
            diff_side="RIGHT",
            comments_connection=NodeList(
                nodes=[
                    PRComment(
                        id=91001,
                        body="keep",
                        path="src/app.py",
                        line=7,
                        side="RIGHT",
                        pull_request_review_id=91,
                    )
                ]
            ),
        )
    ]
    keep = store.save_pending_inline_comment(
        "keep",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    store.save_pending_inline_comment(
        "drop",
        path="src/app.py",
        line=8,
        side="RIGHT",
    )
    service = FakeInlineCommentService()
    service.list_review_comments_result = [
        PRComment(
            id=91001,
            body="keep",
            path="src/app.py",
            line=7,
            side="RIGHT",
            pull_request_review_id=91,
        ),
        PRComment(
            id=91002,
            body="drop",
            path="src/app.py",
            line=8,
            side="RIGHT",
            pull_request_review_id=91,
        ),
    ]
    store._service = service  # type: ignore[assignment]

    deleted = await store.remove_pending_inline_comment(
        path="src/app.py",
        line=8,
        side="RIGHT",
        draft_index=1,
    )

    assert deleted is True
    assert store.state.pending_review_id == 100
    assert store.state.obsolete_pending_review_ids == {91}
    assert [review.id for review in store.state.reviews] == [100]
    assert [comment.body for comment in store.state.pending_review_comments] == [
        keep.body
    ]
    assert store.visible_review_threads_for_paths({"src/app.py"}) == []


def test_visible_timeline_comments_render_pending_draft_once_after_replacement() -> None:
    store = PRStore(pr_number=123)
    store.state.pending_review_id = 100
    store.state.obsolete_pending_review_ids = {91}
    store.state.reviews = [PRReview(id=100, state=ReviewState.PENDING)]
    store.state.comments = [
        PRComment(
            id=91001,
            body="keep",
            path="src/app.py",
            line=7,
            side="RIGHT",
            pull_request_review_id=91,
        )
    ]
    store.state.pending_review_comments = [
        PendingReviewComment(
            body="keep",
            path="src/app.py",
            line=7,
            side="RIGHT",
            review_comment_id=100001,
        )
    ]

    visible = store.visible_timeline_comments()

    assert [(comment.body, comment.pull_request_review_id) for comment in visible] == [
        ("keep", 100)
    ]


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_preserves_server_drafts_on_sync() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 91
    service = FakeInlineCommentService()
    service.list_review_comments_result = [
        PRComment.model_validate(
            {
                "id": 5,
                "body": "old server draft",
                "path": "src/app.py",
                "line": 7,
                "side": "RIGHT",
            }
        )
    ]
    store._service = service  # type: ignore[assignment]

    await store.upsert_pending_inline_comment(
        "new draft",
        path="src/app.py",
        line=8,
        side="RIGHT",
    )

    assert service.delete_pending_review_calls == [(123, 91)]
    assert service.create_pending_review_calls == [
        [
            ("src/app.py", 7, "RIGHT", "old server draft"),
            ("src/app.py", 8, "RIGHT", "new draft"),
        ]
    ]
    assert [comment.body for comment in store.state.pending_review_comments] == [
        "old server draft",
        "new draft",
    ]


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_preserves_server_pending_draft_position() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 91
    service = FakeInlineCommentService()
    service.list_review_comments_result = [
        PRComment.model_validate(
            {
                "id": 5,
                "body": "old server draft",
                "path": "src/app.py",
                "position": 2,
                "original_position": 2,
                "diff_hunk": "@@ -0,0 +1,3 @@\n+one\n+two\n+three",
            }
        )
    ]
    store._service = service  # type: ignore[assignment]

    await store.upsert_pending_inline_comment(
        "new draft",
        path="src/app.py",
        line=3,
        side="RIGHT",
    )

    assert service.delete_pending_review_calls == [(123, 91)]
    assert service.create_pending_review_calls == [
        [
            ("src/app.py", 2, "RIGHT", "old server draft"),
            ("src/app.py", 3, "RIGHT", "new draft"),
        ]
    ]
    assert [comment.body for comment in store.state.pending_review_comments] == [
        "old server draft",
        "new draft",
    ]


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_does_not_delete_when_server_fetch_fails() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 91
    service = FakeInlineCommentService()

    async def list_review_comments(
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        raise RuntimeError("cannot load pending comments")

    service.list_review_comments = list_review_comments  # type: ignore[method-assign]
    store._service = service  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="cannot load pending comments"):
        await store.upsert_pending_inline_comment(
            "new draft",
            path="src/app.py",
            line=8,
            side="RIGHT",
        )

    assert service.delete_pending_review_calls == []
    assert service.create_pending_review_calls == []
    assert store.state.pending_review_comments == []


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_does_not_delete_when_server_comments_are_empty() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 91
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="not replacing pending review"):
        await store.upsert_pending_inline_comment(
            "new draft",
            path="src/app.py",
            line=8,
            side="RIGHT",
        )

    assert service.delete_pending_review_calls == []
    assert service.create_pending_review_calls == []
    assert store.state.pending_review_comments == []


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_keeps_unchanged_line_local() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.file_diffs = {
        "src/app.py": parse_patch(
            "@@ -2,2 +2,3 @@\n line 2\n+line 3\n line 4",
            "src/app.py",
        )
    }
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    draft = await store.upsert_pending_inline_comment(
        "hello outside hunk",
        path="src/app.py",
        line=6,
        side="RIGHT",
    )

    assert draft.is_diff_line is False
    assert service.create_pending_review_calls == []
    assert store.state.pending_review_id is None
    assert store.state.pending_review_comments == [draft]


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_syncs_mixed_pending_comments() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.file_diffs = {
        "src/app.py": parse_patch(
            "@@ -2,2 +2,3 @@\n line 2\n+line 3\n line 4",
            "src/app.py",
        )
    }
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]

    diff_draft = await store.upsert_pending_inline_comment(
        "hello diff",
        path="src/app.py",
        line=3,
        side="RIGHT",
    )
    full_file_draft = await store.upsert_pending_inline_comment(
        "hello outside hunk",
        path="src/app.py",
        line=6,
        side="RIGHT",
    )

    assert diff_draft.is_diff_line is True
    assert full_file_draft.is_diff_line is False
    assert service.create_pending_review_calls == [
        [("src/app.py", 3, "RIGHT", "hello diff")],
        [("src/app.py", 3, "RIGHT", "hello diff")],
    ]
    assert [comment.body for comment in store.state.pending_review_comments] == [
        diff_draft.body,
        full_file_draft.body,
    ]


@pytest.mark.asyncio
async def test_queue_pending_inline_comment_runs_hook_after_local_save_before_sync() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = BlockingPendingReviewService()
    store._service = service  # type: ignore[assignment]
    hook_started = asyncio.Event()
    allow_hook = asyncio.Event()

    async def after_local_save() -> None:
        assert store.state.pending_review_comments[0].body == "hello"
        assert not service.create_started.is_set()
        hook_started.set()
        await allow_hook.wait()

    task = asyncio.create_task(
        store.queue_pending_inline_comment(
            "hello",
            path="src/app.py",
            line=7,
            side="RIGHT",
            after_local_save=after_local_save,
        )
    )

    await asyncio.wait_for(hook_started.wait(), timeout=1)
    assert task.done() is False
    assert not service.create_started.is_set()

    allow_hook.set()
    await asyncio.wait_for(service.create_started.wait(), timeout=1)
    service.allow_create.set()
    draft = await task
    assert draft.body == "hello"


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_updates_local_state_before_sync_finishes() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = BlockingPendingReviewService()
    store._service = service  # type: ignore[assignment]

    task = asyncio.create_task(
        store.upsert_pending_inline_comment(
            "hello",
            path="src/app.py",
            line=7,
            side="RIGHT",
        )
    )

    await asyncio.wait_for(service.create_started.wait(), timeout=1)

    assert task.done() is False
    assert store.state.pending_review_comments[0].body == "hello"

    service.allow_create.set()
    await task


@pytest.mark.asyncio
async def test_upsert_pending_inline_comment_rolls_back_local_state_on_sync_failure() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 91
    old_draft = store.save_pending_inline_comment(
        "old body",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    service = BlockingPendingReviewService()
    service.list_review_comments_result = [
        PRComment.model_validate(
            {
                "id": 5,
                "body": old_draft.body,
                "path": old_draft.path,
                "line": old_draft.line,
                "side": old_draft.side,
            }
        )
    ]
    service.fail_create = True
    store._service = service  # type: ignore[assignment]

    task = asyncio.create_task(
        store.upsert_pending_inline_comment(
            "new body",
            path="src/app.py",
            line=7,
            side="RIGHT",
        )
    )
    await asyncio.wait_for(service.create_started.wait(), timeout=1)

    assert store.state.pending_review_comments[0].body == "new body"

    service.allow_create.set()
    with pytest.raises(RuntimeError, match="sync failed"):
        await task

    assert store.state.pending_review_id == 91
    assert store.state.pending_review_comments == [old_draft]


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
async def test_remove_pending_inline_comment_runs_hook_after_local_delete_before_sync() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 100
    store.save_pending_inline_comment(
        "hello",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    service = BlockingPendingReviewService()
    service.block_delete = True
    store._service = service  # type: ignore[assignment]
    hook_started = asyncio.Event()
    allow_hook = asyncio.Event()

    async def after_local_delete() -> None:
        assert store.state.pending_review_comments == []
        assert not service.delete_started.is_set()
        hook_started.set()
        await allow_hook.wait()

    task = asyncio.create_task(
        store.remove_pending_inline_comment(
            path="src/app.py",
            line=7,
            side="RIGHT",
            after_local_delete=after_local_delete,
        )
    )

    await asyncio.wait_for(hook_started.wait(), timeout=1)
    assert task.done() is False
    assert not service.delete_started.is_set()

    allow_hook.set()
    await asyncio.wait_for(service.delete_started.wait(), timeout=1)
    service.allow_delete.set()
    assert await task is True


@pytest.mark.asyncio
async def test_remove_pending_inline_comment_updates_local_state_before_sync_finishes() -> (
    None
):
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    store.state.pending_review_id = 100
    store.save_pending_inline_comment(
        "hello",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    service = BlockingPendingReviewService()
    service.block_delete = True
    store._service = service  # type: ignore[assignment]

    task = asyncio.create_task(
        store.remove_pending_inline_comment(
            path="src/app.py",
            line=7,
            side="RIGHT",
        )
    )

    await asyncio.wait_for(service.delete_started.wait(), timeout=1)

    assert task.done() is False
    assert store.state.pending_review_comments == []

    service.allow_delete.set()
    assert await task is True


@pytest.mark.asyncio
async def test_load_pending_review_does_not_clear_local_drafts() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = FakeInlineCommentService()
    store._service = service  # type: ignore[assignment]
    draft = store.save_pending_inline_comment(
        "local draft",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    previous_version = store.pending_review_version

    await store._load_pending_review(PR(number=123, head_sha="deadbeef"))

    assert store.state.pending_review_comments == [draft]
    assert store.pending_review_version == previous_version


@pytest.mark.asyncio
async def test_stale_pr_refresh_does_not_clear_new_pending_drafts() -> None:
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, head_sha="deadbeef")
    service = BlockingPRDataService()
    service.pr_all_result = PR(number=123, head_sha="deadbeef")
    store._service = service  # type: ignore[assignment]

    refresh_task = asyncio.create_task(store._load_pr_data())
    await asyncio.wait_for(service.get_started.wait(), timeout=1)

    await store.queue_pending_inline_comment(
        "first draft",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    service.allow_get.set()
    await refresh_task
    await store.queue_pending_inline_comment(
        "second draft",
        path="src/app.py",
        line=8,
        side="RIGHT",
    )

    assert [comment.body for comment in store.state.pending_review_comments] == [
        "first draft",
        "second draft",
    ]
    assert service.create_pending_review_calls[-1] == [
        ("src/app.py", 7, "RIGHT", "first draft"),
        ("src/app.py", 8, "RIGHT", "second draft"),
    ]


@pytest.mark.asyncio
async def test_load_pr_data_restores_pending_review_comments() -> None:
    store = PRStore(pr_number=123)
    service = FakeInlineCommentService()
    pending_review = PRReview(id=91, state=ReviewState.PENDING, body="pending body")
    service.list_review_comments_result = [
        PRComment(body="hello", path="src/app.py", line=7, side="RIGHT")
    ]

    async def get_pr_all(pr_number: int) -> PR:
        return PR(number=pr_number, reviews_connection=NodeList(nodes=[pending_review]))

    service.get_pr_all = get_pr_all  # type: ignore[method-assign]
    store._service = service  # type: ignore[assignment]
    previous_version = store.pending_review_version

    await store._load_pr_data()

    assert store.state.pending_review_id == 91
    assert store.state.pending_review_body == "pending body"
    assert store.state.pending_review_comments == [
        store.state.pending_review_comments[0]
    ]
    assert store.state.pending_review_comments[0].body == "hello"
    assert store.pending_review_version == previous_version + 1


@pytest.mark.asyncio
async def test_load_pr_data_restores_pending_review_comment_from_thread_anchor() -> (
    None
):
    store = PRStore(pr_number=123)
    service = FakeInlineCommentService()
    pending_review = PRReview(id=91, state=ReviewState.PENDING, body="pending body")
    thread = ReviewThread.model_validate(
        {
            "path": "src/app.py",
            "line": 13,
            "originalLine": 13,
            "diffSide": "RIGHT",
            "comments": {
                "nodes": [
                    {
                        "databaseId": 5,
                        "body": "server draft",
                        "path": "src/app.py",
                        "pullRequestReview": {"databaseId": 91},
                    }
                ]
            },
        }
    )
    service.list_review_comments_result = [
        PRComment.model_validate(
            {
                "id": 5,
                "body": "server draft",
                "path": "src/app.py",
                "position": 13,
                "original_position": 13,
                "diff_hunk": "@@ -0,0 +1,3 @@\n+one\n+two\n+three",
            }
        )
    ]

    async def get_pr_all(pr_number: int) -> PR:
        return PR(
            number=pr_number,
            reviews_connection=NodeList(nodes=[pending_review]),
            review_threads_connection=NodeList(nodes=[thread]),
        )

    service.get_pr_all = get_pr_all  # type: ignore[method-assign]
    store._service = service  # type: ignore[assignment]

    await store._load_pr_data()

    assert store.state.pending_review_id == 91
    assert store.state.pending_review_comments == [
        PendingReviewComment(
            body="server draft",
            path="src/app.py",
            line=13,
            side="RIGHT",
            review_comment_id=5,
        )
    ]
