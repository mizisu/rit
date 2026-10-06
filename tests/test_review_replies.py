from unittest.mock import AsyncMock

import pytest

from rit.services.github import GitHubError, GitHubService
from rit.state.models import (
    PR,
    NodeList,
    PendingReviewComment,
    PRComment,
    PRReview,
    ReviewState,
    ReviewThread,
)
from rit.state.pending_review import (
    merge_pending_review_drafts,
    plan_pending_review_sync,
)
from rit.state.store import PRStore


class PendingReplyService(GitHubService):
    def __init__(self, thread: ReviewThread) -> None:
        super().__init__("owner", "repo")
        self.thread = thread
        self.comments: list[PRComment] = []
        self.review: PRReview | None = None
        self.next_review_id = 100
        self.updated: list[tuple[str, str]] = []
        self.created: list[list[PendingReviewComment]] = []
        self.posted: list[str] = []
        self.fail_create = False

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments: list[PendingReviewComment],
        body: str | None = None,
        commit_id: str | None = None,
    ) -> PRReview:
        if self.fail_create:
            raise GitHubError("could not save reply")
        review_id = self.next_review_id
        self.next_review_id += 1
        self.created.append([comment.model_copy() for comment in comments])
        self.comments = [
            PRComment(
                id=review_id * 100 + index,
                node_id=f"comment-{review_id}-{index}",
                body=draft.body,
                path=draft.path,
                line=None if draft.is_file_level else draft.line,
                side=draft.side,
                subject_type=draft.subject_type,
                in_reply_to_id=draft.reply_to_id,
                review_thread_id=draft.reply_thread_id,
                pull_request_review_id=review_id,
            )
            for index, draft in enumerate(comments)
        ]
        self.review = PRReview(
            id=review_id,
            node_id=f"review-{review_id}",
            state=ReviewState.PENDING,
            body=body or "",
        )
        return self.review

    async def list_review_comments(
        self, pr_number: int, review_id: int
    ) -> list[PRComment]:
        return list(self.comments)

    async def delete_pending_review(
        self, pr_number: int, review_id: int, *, require_empty: bool = False
    ) -> None:
        assert not require_empty or not self.comments
        self.comments = []
        self.review = None

    async def update_review_comment(self, comment_node_id: str, body: str) -> PRComment:
        self.updated.append((comment_node_id, body))
        for index, comment in enumerate(self.comments):
            if comment.node_id == comment_node_id:
                updated = comment.model_copy(update={"body": body})
                self.comments[index] = updated
                return updated
        raise AssertionError("draft not found")

    async def get_pr_all(self, pr_number: int) -> PR:
        replies = [comment for comment in self.comments if comment.in_reply_to_id]
        threads = [
            self.thread.model_copy(
                update={
                    "comments_connection": NodeList(
                        nodes=[*self.thread.comments, *replies]
                    ),
                }
            )
        ]
        threads.extend(
            ReviewThread(
                id=f"thread-{comment.id}",
                path=comment.path,
                line=comment.line,
                diff_side=comment.side,
                comments_connection=NodeList(nodes=[comment]),
            )
            for comment in self.comments
            if comment.in_reply_to_id is None
        )
        return PR(
            number=pr_number,
            head_sha="head",
            review_threads_connection=NodeList(nodes=threads),
            reviews_connection=NodeList(nodes=[self.review] if self.review else []),
        )

    async def submit_pending_review(
        self, pr_number: int, review_id: int, *, event: str, body: str | None = None
    ) -> PRReview:
        assert self.review is not None
        self.review = self.review.model_copy(update={"state": ReviewState.COMMENTED})
        return self.review

    async def create_review_comment_reply(
        self, pr_number: int, root_comment_id: int, body: str
    ) -> PRComment:
        self.posted.append(body)
        return PRComment(
            id=999,
            body=body,
            path=self.thread.path,
            line=self.thread.line,
            side=self.thread.diff_side,
            in_reply_to_id=root_comment_id,
            pull_request_review_id=999,
        )


@pytest.mark.parametrize("reply_selected", [False, True])
@pytest.mark.parametrize("file_level", [False, True])
async def test_reply_keeps_original_thread_on_stale_refresh(
    reply_selected: bool, file_level: bool
) -> None:
    root = PRComment(
        id=501,
        body="root",
        path="src/app.py",
        subject_type="file" if file_level else "line",
        original_line=None if file_level else 7,
        outdated=True,
        pull_request_review_id=90,
    )
    existing_reply = root.model_copy(
        update={"id": 502, "body": "existing reply", "in_reply_to_id": 501}
    )
    thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "path": root.path,
            "isResolved": True,
            "isOutdated": True,
            "comments": {"nodes": [root, existing_reply]},
        }
    )
    pr = PR(number=123, review_threads_connection=NodeList(nodes=[thread]))
    reply = root.model_copy(
        update={
            "id": 503,
            "body": "new reply",
            "in_reply_to_id": 501,
            "pull_request_review_id": 92,
        }
    )
    service = AsyncMock(spec=GitHubService)
    service.create_review_comment_reply.return_value = reply
    service.get_pr_all.return_value = pr
    store = PRStore(pr_number=123)
    store._service = service
    store._apply_discussion_state(pr)

    result = await store.reply_to_review_comment(
        existing_reply if reply_selected else root, "  new reply  "
    )

    assert result == reply
    service.create_review_comment_reply.assert_awaited_once_with(123, 501, "new reply")
    service.update_review_comment.assert_not_called()
    for _ in range(2):
        assert len(store.state.review_threads) == 1
        projected_thread = store.state.review_threads[0]
        assert projected_thread.id == thread.id
        assert projected_thread.is_resolved is True
        assert projected_thread.comments == [root, existing_reply, reply]
        assert store.get_file_comments(root.path) == [root, existing_reply, reply]
        await store.refresh_review_data()
    assert thread.comments == [root, existing_reply]

    service.get_pr_all.return_value = store.state.pr
    await store.refresh_review_data()
    assert store.state.review_threads[0].comments == [root, existing_reply, reply]


@pytest.mark.parametrize("body,comment_id", [("   ", 501), ("reply", 0)])
async def test_reply_rejects_invalid_input(body: str, comment_id: int) -> None:
    service = AsyncMock(spec=GitHubService)
    store = PRStore(pr_number=123)
    store._service = service
    store.state.pr = PR(number=123)

    with pytest.raises(ValueError):
        await store.reply_to_review_comment(PRComment(id=comment_id), body)

    service.create_review_comment_reply.assert_not_called()
    assert store.state.comments == []


async def test_failed_reply_does_not_change_local_discussion() -> None:
    root = PRComment(id=501, path="test.py", body="original")
    thread = ReviewThread.model_validate(
        {"id": "thread-501", "comments": {"nodes": [root]}}
    )
    store = PRStore(pr_number=123)
    store._apply_discussion_state(
        PR(number=123, review_threads_connection=NodeList(nodes=[thread]))
    )
    service = AsyncMock(spec=GitHubService)
    service.create_review_comment_reply.side_effect = GitHubError("cannot reply")
    store._service = service

    with pytest.raises(GitHubError, match="cannot reply"):
        await store.reply_to_review_comment(root, "reply")

    assert store.state.review_threads == [thread]
    assert store.state.comments == [root]


@pytest.mark.parametrize("file_level", [False, True])
async def test_pending_reply_survives_edit_reload_replacement_and_submission(
    file_level: bool,
) -> None:
    root = PRComment(
        id=501,
        body="root",
        path="test.py",
        line=None if file_level else 7,
        subject_type="file" if file_level else "line",
        pull_request_review_id=90,
    )
    thread = ReviewThread(
        id="thread-501",
        path=root.path,
        line=root.line,
        diff_side="RIGHT",
        subject_type=root.subject_type,
        comments_connection=NodeList(nodes=[root]),
    )
    service = PendingReplyService(thread)
    store = PRStore(pr_number=123)
    store._service = service
    await store.refresh_review_data()
    store.save_pending_inline_comment(
        "other draft", path="other.py", line=1, side="RIGHT"
    )

    draft = await store.queue_pending_reply(root, "  pending reply  ")
    assert draft.is_reply and draft.reply_thread_id == thread.id
    assert draft.review_comment_node_id
    assert len(store.state.pending_review.comments) == 2
    assert service.posted == []
    assert next(c for c in service.created[0] if c.is_reply).reply_to_id == root.id

    draft_index = store.review_annotations().index_for_comment(draft)
    edited = await store.queue_pending_reply(
        root, "edited reply", draft_index=draft_index
    )
    assert service.updated == [(draft.review_comment_node_id, "edited reply")]
    assert edited.reply_thread_id == thread.id
    assert len(service.created) == 1

    restored = PRStore(pr_number=123)
    restored._service = service
    await restored.refresh_review_data()
    loaded_reply = next(c for c in restored.state.pending_review.comments if c.is_reply)
    assert loaded_reply.body == "edited reply"
    assert loaded_reply.reply_to_id == root.id
    assert loaded_reply.reply_thread_id == thread.id
    assert restored.visible_review_threads_for_paths([root.path])[0].comments == [root]
    visible_reply = next(
        c for c in restored.visible_timeline_comments() if c.in_reply_to_id
    )
    assert visible_reply.body == "edited reply"

    await restored.queue_pending_inline_comment(
        "another", path="third.py", line=2, side="RIGHT"
    )
    assert len(service.created) == 2
    recreated_reply = next(c for c in service.created[-1] if c.is_reply)
    assert recreated_reply.reply_thread_id == thread.id
    assert recreated_reply.body == "edited reply"

    await restored.submit_review("COMMENT", "")
    assert service.review is not None and service.review.state == ReviewState.COMMENTED
    assert restored.state.pending_review.comments == []
    assert service.posted == []
    submitted_thread = restored.visible_review_threads_for_paths([root.path])[0]
    assert [c.body for c in submitted_thread.comments] == ["root", "edited reply"]
    await restored.refresh_review_data()
    assert [c.body for c in restored.visible_review_threads_for_paths([root.path])[0].comments] == [
        "root", "edited reply",
    ]


async def test_failed_pending_reply_keeps_other_local_drafts() -> None:
    root = PRComment(id=501, body="root", path="test.py", line=7)
    thread = ReviewThread(
        id="thread-501",
        path=root.path,
        line=7,
        comments_connection=NodeList(nodes=[root]),
    )
    service = PendingReplyService(thread)
    store = PRStore(pr_number=123)
    store._service = service
    await store.refresh_review_data()
    original = store.save_pending_inline_comment(
        "keep", path="test.py", line=1, side="RIGHT"
    )
    service.fail_create = True
    with pytest.raises(GitHubError, match="could not save reply"):
        await store.queue_pending_reply(root, "reply")
    assert store.state.pending_review.comments == [original]
    assert service.posted == []


async def test_post_pending_reply_removes_only_its_draft() -> None:
    root = PRComment(id=501, body="root", path="test.py", line=7)
    thread = ReviewThread(
        id="thread-501",
        path=root.path,
        line=7,
        comments_connection=NodeList(nodes=[root]),
    )
    service = PendingReplyService(thread)
    store = PRStore(pr_number=123)
    store._service = service
    await store.refresh_review_data()
    store.save_pending_inline_comment("keep", path="other.py", line=1, side="RIGHT")
    draft = await store.queue_pending_reply(root, "reply")
    await store.reply_to_review_comment(
        root, "reply", draft_index=store.review_annotations().index_for_comment(draft)
    )
    assert service.posted == ["reply"]
    assert [c.body for c in store.state.pending_review.comments] == ["keep"]
    assert all(not c.is_reply for c in service.created[-1])


def test_pending_replies_do_not_merge_with_other_threads_or_new_comments() -> None:
    comment = PendingReviewComment(path="test.py", line=7, body="same")
    first = comment.model_copy(
        update={"reply_to_id": 501, "reply_thread_id": "thread-501"}
    )
    second = comment.model_copy(
        update={"reply_to_id": 502, "reply_thread_id": "thread-502"}
    )
    assert len(merge_pending_review_drafts([comment, first], [second])) == 3
    with pytest.raises(ValueError, match="Reply thread ID is unavailable"):
        plan_pending_review_sync(
            [first.model_copy(update={"reply_thread_id": ""})],
            pending_review_id=100,
            pending_review_body="",
            head_sha="head",
        )
