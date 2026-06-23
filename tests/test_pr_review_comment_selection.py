import pytest

from rit.services.pr_review_comment_selection import (
    review_comment_target,
    select_created_review_comment,
)
from rit.state.models import PRComment, PendingReviewComment


def test_review_comment_target_builds_pending_review_comment() -> None:
    target = review_comment_target(
        body="ship it",
        path="app.py",
        line=42,
        side="RIGHT",
    )

    pending = target.pending_comment()

    assert pending == PendingReviewComment(
        body="ship it",
        path="app.py",
        line=42,
        side="RIGHT",
    )


def test_review_comment_target_rejects_unknown_side() -> None:
    with pytest.raises(ValueError) as exc_info:
        review_comment_target(
            body="ship it",
            path="app.py",
            line=42,
            side="BOTH",
        )

    assert "LEFT or RIGHT" in str(exc_info.value)


def test_select_created_review_comment_prefers_exact_match() -> None:
    target = review_comment_target(
        body="ship it",
        path="app.py",
        line=42,
        side="RIGHT",
    )
    comments = [
        PRComment(id=100, body="other", path="app.py", line=42, side="RIGHT"),
        PRComment(id=101, body="ship it", path="app.py", line=42, side="RIGHT"),
        PRComment(id=102, body="newer", path="other.py", line=1, side="RIGHT"),
    ]

    selected = select_created_review_comment(comments, target, review_id=80)

    assert selected.id == 101


def test_select_created_review_comment_falls_back_to_latest_returned_comment() -> None:
    target = review_comment_target(
        body="ship it",
        path="app.py",
        line=42,
        side="RIGHT",
    )
    comments = [
        PRComment(id=100, body="first", path="other.py", line=1, side="RIGHT"),
        PRComment(id=101, body="latest", path="other.py", line=2, side="RIGHT"),
    ]

    selected = select_created_review_comment(comments, target, review_id=80)

    assert selected.id == 101


def test_select_created_review_comment_returns_synthetic_comment_without_matches() -> (
    None
):
    target = review_comment_target(
        body="ship it",
        path="app.py",
        line=42,
        side="RIGHT",
    )

    selected = select_created_review_comment([], target, review_id=80)

    assert selected == PRComment(
        body="ship it",
        path="app.py",
        line=42,
        side="RIGHT",
        pull_request_review_id=80,
    )
