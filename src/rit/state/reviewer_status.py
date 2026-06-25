from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from rit.core.datetime_utils import datetime_sort_key
from rit.state.models import PR, PRReview, PRTeam, ReviewRequest, ReviewState

ReviewerKind = Literal[
    "requested",
    "pending",
    "commented",
    "approved",
    "changes_requested",
    "dismissed",
]


__all__ = (
    "ReviewerDisplayState",
    "ReviewerKind",
    "derive_reviewer_states",
)


@dataclass(frozen=True)
class ReviewerDisplayState:
    display_name: str
    kind: ReviewerKind
    latest_review_at: datetime | None
    is_requested: bool
    is_team: bool


def _reviewer_kind(review: PRReview | None, *, is_requested: bool) -> ReviewerKind:
    if review is None:
        return "requested"
    if review.state == ReviewState.APPROVED:
        return "approved"
    if review.state == ReviewState.CHANGES_REQUESTED:
        return "changes_requested"
    if review.state == ReviewState.COMMENTED:
        return "commented"
    if review.state == ReviewState.DISMISSED:
        return "dismissed"
    return "requested" if is_requested else "pending"


def derive_reviewer_states(
    pr: PR, reviews: list[PRReview]
) -> list[ReviewerDisplayState]:
    requested_reviewers = pr.requested_reviewers
    if not requested_reviewers and not reviews:
        return []

    author_login = pr.user.login if pr.user else ""

    if not requested_reviewers and len(reviews) == 1:
        review = reviews[0]
        user = review.user
        login = user.login if user else ""
        if not login or login == author_login:
            return []
        return [
            ReviewerDisplayState(
                display_name=login,
                kind=_reviewer_kind(review, is_requested=False),
                latest_review_at=review.submitted_at,
                is_requested=False,
                is_team=False,
            )
        ]

    requested_by_name: dict[str, ReviewRequest] = {}
    requested_order: list[str] = []
    for request in requested_reviewers:
        display_name = request.display_name
        if not display_name or display_name == author_login:
            continue
        if display_name in requested_by_name:
            continue
        requested_by_name[display_name] = request
        requested_order.append(display_name)

    latest_reviews: dict[str, tuple[PRReview, tuple[datetime, int]]] = {}
    review_order: list[str] = []
    review_order_seen: set[str] = set()
    for index, review in enumerate(reviews):
        user = review.user
        login = user.login if user else ""
        if not login or login == author_login:
            continue

        review_key = (datetime_sort_key(review.submitted_at), index)
        existing = latest_reviews.get(login)
        if existing is None or review_key >= existing[1]:
            latest_reviews[login] = (review, review_key)
        if login not in review_order_seen:
            review_order.append(login)
            review_order_seen.add(login)

    ordered_names = requested_order + [
        login for login in review_order if login not in requested_by_name
    ]

    reviewer_states: list[ReviewerDisplayState] = []
    for display_name in ordered_names:
        request = requested_by_name.get(display_name)
        latest_review = latest_reviews.get(display_name)
        review = latest_review[0] if latest_review is not None else None
        reviewer_states.append(
            ReviewerDisplayState(
                display_name=display_name,
                kind=_reviewer_kind(review, is_requested=request is not None),
                latest_review_at=review.submitted_at if review else None,
                is_requested=request is not None,
                is_team=isinstance(
                    request.requested_reviewer if request is not None else None,
                    PRTeam,
                ),
            )
        )

    return reviewer_states
