from datetime import datetime, timezone

from rit.core.datetime_utils import datetime_min_utc
from rit.state.models import PRComment, PRIssueComment, PRReview, PRUser, ReviewState
from rit.ui.components.pr_timeline_projection import (
    build_timeline_items,
    review_timeline_time,
)


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 18, hour, minute, tzinfo=timezone.utc)


def test_review_timeline_time_prefers_submitted_then_created_then_thread_time() -> None:
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

    assert (
        review_timeline_time(
            PRReview(created_at=_dt(6, 10), submitted_at=_dt(6, 30)),
            [thread],
        )
        == _dt(6, 30)
    )
    assert (
        review_timeline_time(
            PRReview(created_at=_dt(6, 10), submitted_at=None),
            [thread],
        )
        == _dt(6, 10)
    )
    assert (
        review_timeline_time(
            PRReview(created_at=datetime_min_utc(), submitted_at=None),
            [thread],
        )
        == _dt(6, 25)
    )


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
