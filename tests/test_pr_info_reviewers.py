from __future__ import annotations

from datetime import datetime, timezone

from rit.state.models import PR, PRReview, PRTeam, PRUser, ReviewRequest, ReviewState
import rit.state.reviewer_status as reviewer_status_module
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


def test_empty_reviewer_state_skips_author_lookup() -> None:
    class User:
        @property
        def login(self) -> str:
            raise AssertionError("empty reviewer state should not inspect author")

    pr = _pr()
    pr.user = User()

    assert derive_reviewer_states(pr, []) == []


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


def test_single_review_without_requested_reviewers_skips_sort_key(
    monkeypatch,
) -> None:
    pr = _pr()
    review = _review(
        "alice",
        ReviewState.APPROVED,
        submitted_at=datetime(2026, 4, 1, 12),
    )
    monkeypatch.setattr(
        reviewer_status_module,
        "datetime_sort_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single reviewer state should not compute sort keys")
        ),
    )

    reviewers = derive_reviewer_states(pr, [review])

    assert len(reviewers) == 1
    assert reviewers[0].display_name == "alice"
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


def test_pending_review_can_be_compared_with_timezone_aware_reviews() -> None:
    pr = _pr()
    submitted_at = datetime(2026, 4, 3, 9, tzinfo=timezone.utc)
    reviews = [
        _review("alice", ReviewState.APPROVED, submitted_at=submitted_at),
        _review("alice", ReviewState.PENDING, submitted_at=None),
    ]

    reviewers = derive_reviewer_states(pr, reviews)

    assert len(reviewers) == 1
    assert reviewers[0].kind == "approved"
    assert reviewers[0].latest_review_at == submitted_at


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


def test_review_order_uses_seen_set_for_unique_reviewers() -> None:
    class ReviewLogin(str):
        def __eq__(self, other: object) -> bool:
            if isinstance(other, ReviewLogin) and str(self) != str(other):
                raise AssertionError("review order should not scan existing logins")
            return super().__eq__(other)

        def __hash__(self) -> int:
            return {"alice": 1, "bob": 2}[str(self)]

    alice = PRUser(login="alice")
    alice.login = ReviewLogin("alice")
    bob = PRUser(login="bob")
    bob.login = ReviewLogin("bob")
    pr = _pr()
    reviews = [
        PRReview(
            author=alice,
            state=ReviewState.COMMENTED,
            submittedAt=datetime(2026, 4, 1, 9),
        ),
        PRReview(
            author=bob,
            state=ReviewState.APPROVED,
            submittedAt=datetime(2026, 4, 1, 10),
        ),
    ]

    reviewers = derive_reviewer_states(pr, reviews)

    assert [str(reviewer.display_name) for reviewer in reviewers] == ["alice", "bob"]


def test_team_reviewer_is_marked_as_team() -> None:
    pr = _pr(
        requested_reviewers=[ReviewRequest(requestedReviewer=PRTeam(name="backend"))]
    )

    reviewers = derive_reviewer_states(pr, [])

    assert len(reviewers) == 1
    assert reviewers[0].display_name == "backend"
    assert reviewers[0].kind == "requested"
    assert reviewers[0].is_team is True


def test_graphql_team_reviewer_preserves_slug() -> None:
    pr = PR.model_validate(
        {
            "number": 123,
            "author": {"login": "author"},
            "reviewRequests": {
                "nodes": [
                    {"requestedReviewer": {"name": "Backend", "slug": "backend"}}
                ]
            },
        }
    )

    request = pr.requested_reviewers[0]
    reviewers = derive_reviewer_states(pr, [])

    assert isinstance(request.requested_reviewer, PRTeam)
    assert request.requested_reviewer.slug == "backend"
    assert reviewers[0].display_name == "Backend"
    assert reviewers[0].is_team is True
