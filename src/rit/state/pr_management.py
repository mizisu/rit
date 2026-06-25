from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, cast

from rit.state.models import PR, PRTeam, PRUser, ReviewRequest


__all__ = (
    "AssigneeSelectionPlan",
    "ReviewerSelectionPlan",
    "plan_assignee_selection",
    "plan_reviewer_selection",
)


@dataclass(frozen=True)
class ReviewerSelectionPlan:
    """Requested reviewer changes needed to match a desired selection."""

    add_users: tuple[str, ...] = ()
    add_teams: tuple[str, ...] = ()
    remove_users: tuple[str, ...] = ()
    remove_teams: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        return bool(
            self.add_users or self.add_teams or self.remove_users or self.remove_teams
        )


@dataclass(frozen=True)
class AssigneeSelectionPlan:
    """Assignee changes needed to match a desired selection."""

    add_logins: tuple[str, ...] = ()
    remove_logins: tuple[str, ...] = ()

    @property
    def has_changes(self) -> bool:
        return bool(self.add_logins or self.remove_logins)


def plan_reviewer_selection(
    pr: PR,
    *,
    users: Iterable[str],
    teams: Iterable[str],
) -> ReviewerSelectionPlan:
    """Return requested reviewer mutations for a desired user/team selection."""
    author_login = pr.user.login if pr.user else ""
    desired_users = _clean_values(
        users,
        excluded={author_login} if author_login else None,
    )
    desired_teams = _clean_values(teams)
    current_users, current_teams = _current_requested_reviewers(pr)

    return ReviewerSelectionPlan(
        add_users=_sorted_tuple(desired_users - current_users),
        add_teams=_sorted_tuple(desired_teams - current_teams),
        remove_users=_sorted_tuple(current_users - desired_users),
        remove_teams=_sorted_tuple(current_teams - desired_teams),
    )


def plan_assignee_selection(
    pr: PR,
    logins: Iterable[str],
) -> AssigneeSelectionPlan:
    """Return assignee mutations for a desired user selection."""
    desired = _clean_values(logins)
    current = {user.login for user in pr.assignees if user.login}

    return AssigneeSelectionPlan(
        add_logins=_sorted_tuple(desired - current),
        remove_logins=_sorted_tuple(current - desired),
    )


def _current_requested_reviewers(pr: PR) -> tuple[set[str], set[str]]:
    users: set[str] = set()
    teams: set[str] = set()
    for request in pr.requested_reviewers:
        kind, key = _review_request_key(request)
        if kind == "user":
            users.add(key)
        elif kind == "team":
            teams.add(key)
    return users, teams


def _review_request_key(
    request: ReviewRequest,
) -> tuple[Literal["user", "team", "none"], str]:
    reviewer = request.requested_reviewer
    if isinstance(reviewer, PRUser) and reviewer.login:
        return "user", reviewer.login
    if isinstance(reviewer, PRTeam):
        key = reviewer.slug or reviewer.name
        if key:
            return "team", key
    return "none", ""


def _clean_values(
    values: Iterable[str],
    *,
    excluded: set[str] | None = None,
) -> set[str]:
    excluded = excluded or set()
    cleaned: set[str] = set()
    for value in values:
        stripped = value.strip()
        if stripped and stripped not in excluded:
            cleaned.add(stripped)
    return cleaned


def _sorted_tuple(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, set):
        value_set = cast(set[str], values)
        if not value_set:
            return ()
        if len(value_set) == 1:
            return (next(iter(value_set)),)
    return tuple(sorted(values))
