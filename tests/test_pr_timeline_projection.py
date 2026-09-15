from datetime import UTC, datetime

import pytest

import rit.ui.components.pr_timeline_projection as timeline_projection_module
from rit.core.datetime_utils import datetime_min_utc
from rit.state.models import (
    CommentThread,
    PRComment,
    PRIssueComment,
    PRReview,
    PRTimelineEvent,
    PRUser,
    ReviewState,
)
from rit.ui.components.pr_timeline_projection import (
    build_timeline_items,
    review_timeline_time,
)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 18, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize("state", list(ReviewState))
def test_blank_reviews_keep_decisions_but_not_empty_pending_or_comment_reviews(
    state,
) -> None:
    review = PRReview(id=1, body=" \n", state=state, submitted_at=_dt(7))
    items = build_timeline_items(issue_comments=[], reviews=[review], comments=[])

    assert [item.review for item in items] == (
        [] if state in {ReviewState.PENDING, ReviewState.COMMENTED} else [review]
    )


def test_activity_keeps_github_order_despite_commit_dates_and_appends_local_drafts() -> (
    None
):
    events = [
        PRTimelineEvent(
            id="a", kind="PullRequestCommit", actor="alice", created_at=_dt(5)
        ),
        PRTimelineEvent(
            id="b", kind="PullRequestCommit", actor="alice", created_at=_dt(5, 10)
        ),
        PRTimelineEvent(
            id="c", kind="PullRequestCommit", actor="bob", created_at=_dt(5, 20)
        ),
        PRTimelineEvent(
            id="issue-1", kind="IssueComment", database_id=1, created_at=_dt(5, 20)
        ),
        PRTimelineEvent(
            id="d", kind="PullRequestCommit", actor="bob", created_at=_dt(4)
        ),
        PRTimelineEvent(id="closed", kind="ClosedEvent", created_at=_dt(8)),
        PRTimelineEvent(id="reopened", kind="ReopenedEvent", created_at=_dt(9)),
        PRTimelineEvent(
            id="review-2", kind="PullRequestReview", database_id=2, created_at=_dt(9, 30)
        ),
        PRTimelineEvent(id="merged", kind="MergedEvent", created_at=_dt(10)),
        PRTimelineEvent(id="merge-closed", kind="ClosedEvent", created_at=_dt(10)),
    ]
    items = build_timeline_items(
        issue_comments=[PRIssueComment(id=1, body="Please update", created_at=_dt(5, 20))],
        reviews=[
            PRReview(id=2, state=ReviewState.APPROVED, submitted_at=_dt(9, 30)),
            PRReview(id=3, body="Local draft", created_at=_dt(3)),
        ],
        comments=[],
        events=events,
    )

    assert [([event.id for event in item.events] or [item.kind]) for item in items] == [
        ["a", "b"],
        ["c"],
        ["issue_comment"],
        ["d"],
        ["closed"],
        ["reopened"],
        ["review"],
        ["merged"],
        ["review"],
    ]
    assert items[-1].review is not None
    assert items[-1].review.id == 3


def test_review_timeline_time_prefers_submitted_then_pending_thread_then_created() -> (
    None
):
    thread = build_timeline_items(
        issue_comments=[],
        reviews=[],
        comments=[
            PRComment(
                id=100,
                body="thread",
                path="app.py",
                line=12,
                created_at=_dt(6, 25),
            )
        ],
    )[0].thread
    assert thread is not None

    assert review_timeline_time(
        PRReview(created_at=_dt(6, 10), submitted_at=_dt(6, 30)),
        [thread],
    ) == _dt(6, 30)
    assert review_timeline_time(
        PRReview(
            state=ReviewState.PENDING,
            created_at=_dt(6, 10),
            submitted_at=None,
        ),
        [thread],
    ) == _dt(6, 25)
    assert review_timeline_time(
        PRReview(
            state=ReviewState.COMMENTED,
            created_at=_dt(6, 10),
            submitted_at=None,
        ),
        [thread],
    ) == _dt(6, 10)
    assert review_timeline_time(
        PRReview(
            state=ReviewState.COMMENTED,
            created_at=datetime_min_utc(),
            submitted_at=None,
        ),
        [thread],
    ) == _dt(6, 25)


def test_review_timeline_time_scans_thread_times_without_min_list(
    monkeypatch,
) -> None:
    threads = [
        CommentThread(
            root_comment=PRComment(
                id=100,
                body="later",
                path="app.py",
                line=12,
                created_at=_dt(8),
            )
        ),
        CommentThread(
            root_comment=PRComment(
                id=101,
                body="earlier",
                path="app.py",
                line=20,
                created_at=_dt(6),
            )
        ),
    ]

    monkeypatch.setattr(
        timeline_projection_module,
        "min",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("thread time fallback should scan without building a list")
        ),
        raising=False,
    )

    assert review_timeline_time(
        PRReview(
            state=ReviewState.COMMENTED,
            created_at=datetime_min_utc(),
            submitted_at=None,
        ),
        threads,
    ) == _dt(6)


def test_pending_review_timeline_time_uses_latest_thread_time(monkeypatch) -> None:
    threads = [
        CommentThread(
            root_comment=PRComment(
                id=100,
                body="later",
                path="app.py",
                line=12,
                created_at=_dt(8),
            )
        ),
        CommentThread(
            root_comment=PRComment(
                id=101,
                body="earlier",
                path="app.py",
                line=20,
                created_at=_dt(6),
            )
        ),
    ]

    monkeypatch.setattr(
        timeline_projection_module,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pending thread time should scan without building a list")
        ),
        raising=False,
    )

    assert review_timeline_time(
        PRReview(
            state=ReviewState.PENDING,
            created_at=_dt(5),
            submitted_at=None,
        ),
        threads,
    ) == _dt(8)


def test_review_timeline_time_single_thread_skips_sort_key(monkeypatch) -> None:
    thread = CommentThread(
        root_comment=PRComment(
            id=100,
            body="thread",
            path="app.py",
            line=12,
            created_at=_dt(6),
        )
    )
    monkeypatch.setattr(
        timeline_projection_module,
        "datetime_sort_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single fallback thread time should not compute sort key")
        ),
    )

    assert review_timeline_time(
        PRReview(created_at=datetime_min_utc(), submitted_at=None),
        [thread],
    ) == _dt(6)


def test_build_timeline_items_filters_blank_comments_and_groups_review_threads() -> (
    None
):
    items = build_timeline_items(
        issue_comments=[
            PRIssueComment(id=1, body="  ", created_at=_dt(5)),
            PRIssueComment(id=2, body="Issue comment", created_at=_dt(6)),
        ],
        reviews=[
            PRReview(
                id=10,
                body="",
                state=ReviewState.PENDING,
                user=PRUser(login="alice"),
                created_at=_dt(8),
                submitted_at=None,
            )
        ],
        comments=[
            PRComment(
                id=100,
                body="  ",
                path="app.py",
                line=12,
                created_at=_dt(7),
                pull_request_review_id=10,
            ),
            PRComment(
                id=101,
                body="Pending thread",
                path="app.py",
                line=12,
                created_at=_dt(8, 1),
                pull_request_review_id=10,
            ),
            PRComment(
                id=102,
                body="Orphan thread",
                path="app.py",
                line=20,
                created_at=_dt(9),
            ),
        ],
    )

    assert [item.kind for item in items] == ["issue_comment", "review", "thread"]
    assert items[0].issue_comment is not None
    assert items[0].issue_comment.id == 2
    assert items[1].review is not None
    assert items[1].review.id == 10
    assert [thread.root_comment.id for thread in items[1].threads] == [101]
    assert items[2].thread is not None
    assert items[2].thread.root_comment.id == 102


def test_build_timeline_items_does_not_copy_review_comments() -> None:
    class NoListComments:
        def __iter__(self):
            return iter(
                [
                    PRComment(
                        id=100,
                        body="Review thread",
                        path="app.py",
                        line=12,
                        created_at=_dt(6),
                        pull_request_review_id=10,
                    )
                ]
            )

        def __len__(self) -> int:
            raise AssertionError("timeline projection should not copy comments")

    items = build_timeline_items(
        issue_comments=[],
        reviews=[
            PRReview(
                id=10,
                body="",
                state=ReviewState.PENDING,
                user=PRUser(login="alice"),
                created_at=_dt(8),
                submitted_at=None,
            )
        ],
        comments=NoListComments(),
    )

    assert [item.kind for item in items] == ["review"]
    assert items[0].threads[0].root_comment.id == 100


def test_build_timeline_items_skips_sort_key_for_single_item(monkeypatch) -> None:
    monkeypatch.setattr(
        timeline_projection_module,
        "datetime_sort_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single timeline item should not compute sort keys")
        ),
    )

    items = build_timeline_items(
        issue_comments=[
            PRIssueComment(id=1, body="Issue", created_at=_dt(7)),
        ],
        reviews=[],
        comments=[],
    )

    assert [item.kind for item in items] == ["issue_comment"]


def test_build_timeline_items_skips_grouping_empty_comment_sequence(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        timeline_projection_module,
        "group_comments_into_threads",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty comment sequence should not be grouped")
        ),
    )

    items = build_timeline_items(
        issue_comments=[
            PRIssueComment(id=1, body="Issue", created_at=_dt(7)),
        ],
        reviews=[],
        comments=[],
    )

    assert [item.kind for item in items] == ["issue_comment"]


def test_build_timeline_items_orders_review_threads_by_review_time() -> None:
    items = build_timeline_items(
        issue_comments=[
            PRIssueComment(id=1, body="Issue", created_at=_dt(7)),
        ],
        reviews=[
            PRReview(
                id=10,
                body="",
                created_at=_dt(5),
                submitted_at=_dt(8),
            )
        ],
        comments=[
            PRComment(
                id=100,
                body="Review thread",
                path="app.py",
                line=12,
                created_at=_dt(6),
                pull_request_review_id=10,
            )
        ],
    )

    assert [item.kind for item in items] == ["issue_comment", "review"]
    assert items[1].when == _dt(8)


def test_build_timeline_items_keeps_review_threads_in_created_order() -> None:
    items = build_timeline_items(
        issue_comments=[],
        reviews=[
            PRReview(
                id=10,
                body="",
                state=ReviewState.COMMENTED,
                created_at=_dt(8),
            )
        ],
        comments=[
            PRComment(
                id=200,
                body="Later thread",
                path="app.py",
                line=20,
                created_at=_dt(7),
                pull_request_review_id=10,
            ),
            PRComment(
                id=100,
                body="Earlier thread",
                path="app.py",
                line=12,
                created_at=_dt(6),
                pull_request_review_id=10,
            ),
        ],
    )

    assert [thread.root_comment.id for thread in items[0].threads] == [100, 200]
