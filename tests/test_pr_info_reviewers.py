from __future__ import annotations

from datetime import datetime

from rit.state.models import PR, PRReview, PRTeam, PRUser, ReviewRequest, ReviewState
from rit.state.reviewer_status import derive_reviewer_states


def _pr(*, requested_reviewers: list[ReviewRequest] | None = None) -> PR:
    return PR(
        number=123,
        author=PRUser(login="author"),
        reviewRequests={"nodes": requested_reviewers or []},
    )


def _review(
    login: str,
    state: ReviewState,
    *,
    submitted_at: datetime | None,
) -> PRReview:
    return PRReview(author=PRUser(login=login), state=state, submittedAt=submitted_at)


def test_requested_only_reviewer_is_kept() -> None:
    pr = _pr(
        requested_reviewers=[ReviewRequest(requestedReviewer=PRUser(login="alice"))]
    )

    reviewers = derive_reviewer_states(pr, [])

    assert len(reviewers) == 1
    assert reviewers[0].display_name == "alice"
    assert reviewers[0].kind == "requested"
    assert reviewers[0].is_requested is True
    assert reviewers[0].is_team is False


def test_requested_reviewer_with_pending_review_stays_requested() -> None:
    pr = _pr(
        requested_reviewers=[ReviewRequest(requestedReviewer=PRUser(login="alice"))]
    )
    reviews = [_review("alice", ReviewState.PENDING, submitted_at=None)]

    reviewers = derive_reviewer_states(pr, reviews)

    assert len(reviewers) == 1
    assert reviewers[0].kind == "requested"


def test_requested_reviewer_with_approved_review_shows_approved() -> None:
    pr = _pr(
        requested_reviewers=[ReviewRequest(requestedReviewer=PRUser(login="alice"))]
    )
    reviews = [
        _review("alice", ReviewState.APPROVED, submitted_at=datetime(2026, 4, 1, 12))
    ]

    reviewers = derive_reviewer_states(pr, reviews)

    assert len(reviewers) == 1
    assert reviewers[0].kind == "approved"
    assert reviewers[0].latest_review_at == datetime(2026, 4, 1, 12)


def test_latest_review_state_is_chosen_by_timestamp() -> None:
    pr = _pr()
    reviews = [
        _review("alice", ReviewState.APPROVED, submitted_at=datetime(2026, 4, 3, 9)),
        _review(
            "alice",
            ReviewState.CHANGES_REQUESTED,
            submitted_at=datetime(2026, 4, 2, 18),
        ),
    ]

    reviewers = derive_reviewer_states(pr, reviews)

    assert len(reviewers) == 1
    assert reviewers[0].kind == "approved"
    assert reviewers[0].latest_review_at == datetime(2026, 4, 3, 9)


def test_author_reviews_are_excluded() -> None:
    pr = _pr(
        requested_reviewers=[ReviewRequest(requestedReviewer=PRUser(login="author"))]
    )
    reviews = [
        _review("author", ReviewState.APPROVED, submitted_at=datetime(2026, 4, 1, 9)),
        _review("bob", ReviewState.COMMENTED, submitted_at=datetime(2026, 4, 1, 10)),
    ]

    reviewers = derive_reviewer_states(pr, reviews)

    assert [reviewer.display_name for reviewer in reviewers] == ["bob"]
    assert reviewers[0].kind == "commented"


def test_team_reviewer_is_marked_as_team() -> None:
    pr = _pr(
        requested_reviewers=[ReviewRequest(requestedReviewer=PRTeam(name="backend"))]
    )

    reviewers = derive_reviewer_states(pr, [])

    assert len(reviewers) == 1
    assert reviewers[0].display_name == "backend"
    assert reviewers[0].kind == "requested"
    assert reviewers[0].is_team is True
