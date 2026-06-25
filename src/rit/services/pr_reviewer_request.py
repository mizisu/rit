from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from rit.services.gh_paginated_json import parse_paginated_items
from rit.services.gh_request import (
    GitHubInputRequest,
    GitHubInputRunner,
    run_input_request,
    run_request,
)
from rit.state.models import PRTeam, PRUser


__all__ = (
    "ParticipantChangeRequest",
    "add_assignees",
    "assignee_candidates_request",
    "assignee_change_request",
    "assignee_payload",
    "assignee_request",
    "fetch_assignee_candidates",
    "fetch_reviewer_candidates",
    "fetch_reviewer_team_candidates",
    "fetch_reviewer_user_candidates",
    "parse_reviewer_team_candidates",
    "parse_reviewer_user_candidates",
    "remove_assignees",
    "remove_requested_reviewers",
    "request_reviewers",
    "reviewer_change_request",
    "reviewer_payload",
    "reviewer_request",
    "reviewer_team_candidates_request",
    "reviewer_user_candidates_request",
    "run_participant_change",
    "should_treat_team_candidates_error_as_empty",
)


_PRUserListAdapter: TypeAdapter[list[PRUser]] = TypeAdapter(list[PRUser])
_PRTeamListAdapter: TypeAdapter[list[PRTeam]] = TypeAdapter(list[PRTeam])


@dataclass(frozen=True)
class ParticipantChangeRequest:
    """Repo-scoped request plan for reviewer or assignee mutations."""

    pr_number: int
    method: Literal["POST", "DELETE"]
    payload: dict[str, list[str]]
    target: Literal["reviewers", "assignees"]

    def to_request(self, repo_full_name: str) -> GitHubInputRequest:
        """Build the executable gh request for a repository."""
        if self.target == "reviewers":
            return reviewer_request(
                repo_full_name,
                self.pr_number,
                method=self.method,
                payload=self.payload,
            )
        return assignee_request(
            repo_full_name,
            self.pr_number,
            method=self.method,
            payload=self.payload,
        )


def reviewer_payload(
    *,
    reviewers: list[str] | None,
    team_reviewers: list[str] | None,
) -> dict[str, list[str]]:
    """Build a GitHub reviewer request payload."""
    payload: dict[str, list[str]] = {}
    if reviewers:
        payload["reviewers"] = reviewers
    if team_reviewers:
        payload["team_reviewers"] = team_reviewers
    return payload


def assignee_payload(assignees: list[str]) -> dict[str, list[str]]:
    """Build a GitHub issue assignee payload."""
    if not assignees:
        return {}
    return {"assignees": assignees}


def reviewer_request(
    repo_full_name: str,
    pr_number: int,
    *,
    method: Literal["POST", "DELETE"],
    payload: dict[str, list[str]],
) -> GitHubInputRequest:
    """Build a gh api request for PR reviewer mutations."""
    return GitHubInputRequest(
        args=(
            "api",
            "--method",
            method,
            f"/repos/{repo_full_name}/pulls/{pr_number}/requested_reviewers",
            "--input",
            "-",
        ),
        input_text=json.dumps(payload),
    )


def assignee_request(
    repo_full_name: str,
    pr_number: int,
    *,
    method: Literal["POST", "DELETE"],
    payload: dict[str, list[str]],
) -> GitHubInputRequest:
    """Build a gh api request for PR issue assignee mutations."""
    return GitHubInputRequest(
        args=(
            "api",
            "--method",
            method,
            f"/repos/{repo_full_name}/issues/{pr_number}/assignees",
            "--input",
            "-",
        ),
        input_text=json.dumps(payload),
    )


def reviewer_change_request(
    pr_number: int,
    *,
    reviewers: list[str] | None,
    team_reviewers: list[str] | None,
    method: Literal["POST", "DELETE"],
) -> ParticipantChangeRequest | None:
    """Build a reviewer mutation request plan when there is a payload."""
    payload = reviewer_payload(reviewers=reviewers, team_reviewers=team_reviewers)
    if not payload:
        return None
    return ParticipantChangeRequest(
        pr_number=pr_number,
        method=method,
        payload=payload,
        target="reviewers",
    )


def assignee_change_request(
    pr_number: int,
    assignees: list[str],
    *,
    method: Literal["POST", "DELETE"],
) -> ParticipantChangeRequest | None:
    """Build an assignee mutation request plan when there is a payload."""
    payload = assignee_payload(assignees)
    if not payload:
        return None
    return ParticipantChangeRequest(
        pr_number=pr_number,
        method=method,
        payload=payload,
        target="assignees",
    )


async def run_participant_change(
    repo_full_name: str,
    change: ParticipantChangeRequest | None,
    runner: GitHubInputRunner,
) -> None:
    """Run a reviewer or assignee mutation when there is a payload."""
    if change is None:
        return
    await run_input_request(change.to_request(repo_full_name), runner)


async def request_reviewers(
    repo_full_name: str,
    pr_number: int,
    *,
    reviewers: list[str] | None,
    team_reviewers: list[str] | None,
    runner: GitHubInputRunner,
) -> None:
    """Request user and team reviewers on a pull request."""
    await run_participant_change(
        repo_full_name,
        reviewer_change_request(
            pr_number,
            reviewers=reviewers,
            team_reviewers=team_reviewers,
            method="POST",
        ),
        runner,
    )


async def remove_requested_reviewers(
    repo_full_name: str,
    pr_number: int,
    *,
    reviewers: list[str] | None,
    team_reviewers: list[str] | None,
    runner: GitHubInputRunner,
) -> None:
    """Remove requested user and team reviewers from a pull request."""
    await run_participant_change(
        repo_full_name,
        reviewer_change_request(
            pr_number,
            reviewers=reviewers,
            team_reviewers=team_reviewers,
            method="DELETE",
        ),
        runner,
    )


async def add_assignees(
    repo_full_name: str,
    pr_number: int,
    assignees: list[str],
    *,
    runner: GitHubInputRunner,
) -> None:
    """Assign users to a pull request issue."""
    await run_participant_change(
        repo_full_name,
        assignee_change_request(pr_number, assignees, method="POST"),
        runner,
    )


async def remove_assignees(
    repo_full_name: str,
    pr_number: int,
    assignees: list[str],
    *,
    runner: GitHubInputRunner,
) -> None:
    """Remove assignees from a pull request issue."""
    await run_participant_change(
        repo_full_name,
        assignee_change_request(pr_number, assignees, method="DELETE"),
        runner,
    )


def reviewer_user_candidates_request(repo_full_name: str) -> tuple[str, ...]:
    """Build a gh api request for repository reviewer user candidates."""
    return (
        "api",
        f"/repos/{repo_full_name}/collaborators?affiliation=all&per_page=100",
        "--paginate",
    )


def reviewer_team_candidates_request(repo_full_name: str) -> tuple[str, ...]:
    """Build a gh api request for repository reviewer team candidates."""
    return (
        "api",
        f"/repos/{repo_full_name}/teams?per_page=100",
        "--paginate",
    )


def assignee_candidates_request(repo_full_name: str) -> tuple[str, ...]:
    """Build a gh api request for repository issue assignee candidates."""
    return (
        "api",
        f"/repos/{repo_full_name}/assignees?per_page=100",
        "--paginate",
    )


def parse_reviewer_user_candidates(result: str) -> list[PRUser]:
    """Parse paginated GitHub user candidate output."""
    items = parse_paginated_items(result)
    if not items:
        return []
    if len(items) == 1:
        return [PRUser.model_validate(items[0])]
    return _PRUserListAdapter.validate_python(items)


async def fetch_reviewer_user_candidates(
    repo_full_name: str,
    runner: GitHubInputRunner,
) -> list[PRUser]:
    """Fetch repository reviewer user candidates via gh."""
    return parse_reviewer_user_candidates(
        await run_request(reviewer_user_candidates_request(repo_full_name), runner)
    )


async def fetch_reviewer_candidates(
    repo_full_name: str,
    runner: GitHubInputRunner,
) -> tuple[list[PRUser], list[PRTeam]]:
    """Fetch user and team candidates for PR review requests."""
    return await asyncio.gather(
        fetch_reviewer_user_candidates(repo_full_name, runner),
        fetch_reviewer_team_candidates(repo_full_name, runner),
    )


async def fetch_assignee_candidates(
    repo_full_name: str,
    runner: GitHubInputRunner,
) -> list[PRUser]:
    """Fetch repository issue assignee candidates via gh."""
    return parse_reviewer_user_candidates(
        await run_request(assignee_candidates_request(repo_full_name), runner)
    )


def parse_reviewer_team_candidates(result: str) -> list[PRTeam]:
    """Parse paginated GitHub team candidate output."""
    items = parse_paginated_items(result)
    if not items:
        return []
    if len(items) == 1:
        return [PRTeam.model_validate(items[0])]
    return _PRTeamListAdapter.validate_python(items)


def should_treat_team_candidates_error_as_empty(message: str) -> bool:
    """Return whether a repo teams lookup failure means no visible teams."""
    return "HTTP 404" in message


async def fetch_reviewer_team_candidates(
    repo_full_name: str,
    runner: GitHubInputRunner,
) -> list[PRTeam]:
    """Fetch repository reviewer team candidates via gh."""
    try:
        result = await run_request(
            reviewer_team_candidates_request(repo_full_name),
            runner,
        )
    except RuntimeError as error:
        if should_treat_team_candidates_error_as_empty(str(error)):
            return []
        raise
    return parse_reviewer_team_candidates(result)
