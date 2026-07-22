from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import connection_nodes, mapping, run_graphql
from rit.state.models import PRTeam, PRUser

__all__ = (
    "add_assignees",
    "fetch_assignee_candidates",
    "fetch_reviewer_candidates",
    "fetch_reviewer_team_candidates",
    "fetch_reviewer_user_candidates",
    "remove_assignees",
    "remove_requested_reviewers",
    "request_reviewers",
)


_USER_CANDIDATES_QUERY = """
query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    collaborators(affiliation: ALL, first: 100, after: $after) {
      nodes {
        id
        login
        avatarUrl
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

_ASSIGNEE_CANDIDATES_QUERY = """
query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    assignableUsers(first: 100, after: $after) {
      nodes {
        id
        login
        avatarUrl
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""

_TEAM_CANDIDATES_QUERY = """
query($owner: String!, $repo: String!, $repoQuery: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    owner {
      ... on Organization {
        teams(first: 100, after: $after) {
          nodes {
            name
            slug
            repositories(first: 1, query: $repoQuery) {
              nodes {
                nameWithOwner
              }
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
  }
}
"""

_PR_NODE_ID_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
    }
  }
}
"""

_REVIEW_REQUESTS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      reviewRequests(first: 100, after: $after) {
        nodes {
          requestedReviewer {
            __typename
            ... on User { id login }
            ... on Bot { id login }
            ... on Mannequin { id login }
            ... on Team { id slug }
            ... on EnterpriseTeam { id slug }
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  }
}
"""

_CURRENT_ASSIGNEES_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      assignedActors(first: 100) {
        nodes {
          __typename
          ... on User { id login }
          ... on Bot { id login }
          ... on Mannequin { id login }
        }
      }
    }
  }
}
"""

_REQUEST_REVIEWS_BY_LOGIN_MUTATION = """
mutation($input: RequestReviewsByLoginInput!) {
  requestReviewsByLogin(input: $input) {
    pullRequest { id }
  }
}
"""

_REQUEST_REVIEWS_MUTATION = """
mutation($input: RequestReviewsInput!) {
  requestReviews(input: $input) {
    pullRequest { id }
  }
}
"""

_ADD_ASSIGNEES_MUTATION = """
mutation($input: AddAssigneesToAssignableInput!) {
  addAssigneesToAssignable(input: $input) {
    assignable { __typename }
  }
}
"""

_REMOVE_ASSIGNEES_MUTATION = """
mutation($input: RemoveAssigneesFromAssignableInput!) {
  removeAssigneesFromAssignable(input: $input) {
    assignable { __typename }
  }
}
"""


@dataclass(frozen=True)
class _ReviewActors:
    pull_request_node_id: str
    users: dict[str, str]
    bots: dict[str, str]
    teams: dict[str, str]


async def fetch_reviewer_user_candidates(
    owner: str,
    repo: str,
    runner: GitHubInputRunner,
) -> list[PRUser]:
    """Fetch all repository collaborators through GraphQL."""
    return await _fetch_user_connection(
        owner,
        repo,
        query=_USER_CANDIDATES_QUERY,
        connection_name="collaborators",
        runner=runner,
    )


async def fetch_assignee_candidates(
    owner: str,
    repo: str,
    runner: GitHubInputRunner,
) -> list[PRUser]:
    """Fetch all users assignable to the repository through GraphQL."""
    return await _fetch_user_connection(
        owner,
        repo,
        query=_ASSIGNEE_CANDIDATES_QUERY,
        connection_name="assignableUsers",
        runner=runner,
    )


async def fetch_reviewer_team_candidates(
    owner: str,
    repo: str,
    runner: GitHubInputRunner,
) -> list[PRTeam]:
    """Fetch organization teams with access to the repository through GraphQL."""
    teams: list[PRTeam] = []
    after: str | None = None
    repo_full_name = f"{owner}/{repo}"
    while True:
        data = await run_graphql(
            _TEAM_CANDIDATES_QUERY,
            {"owner": owner, "repo": repo, "repoQuery": repo, "after": after},
            runner=runner,
        )
        repository = mapping(mapping(data.get("data")).get("repository"))
        organization = mapping(repository.get("owner"))
        connection = mapping(organization.get("teams"))
        for node in connection_nodes(connection):
            team = mapping(node)
            repositories = connection_nodes(team.get("repositories"))
            if not any(
                mapping(candidate).get("nameWithOwner") == repo_full_name
                for candidate in repositories
            ):
                continue
            teams.append(PRTeam.model_validate(team))
        after = _next_cursor(connection, after=after)
        if after is None:
            return teams


async def fetch_reviewer_candidates(
    owner: str,
    repo: str,
    runner: GitHubInputRunner,
) -> tuple[list[PRUser], list[PRTeam]]:
    """Fetch repository user and team review candidates through GraphQL."""
    return await asyncio.gather(
        fetch_reviewer_user_candidates(owner, repo, runner),
        fetch_reviewer_team_candidates(owner, repo, runner),
    )


async def request_reviewers(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    reviewers: list[str] | None,
    team_reviewers: list[str] | None,
    runner: GitHubInputRunner,
) -> None:
    """Add user and team review requests through GraphQL."""
    if not reviewers and not team_reviewers:
        return
    pull_request_node_id = await _fetch_pr_node_id(
        owner, repo, pr_number, runner=runner
    )
    await run_graphql(
        _REQUEST_REVIEWS_BY_LOGIN_MUTATION,
        {
            "input": {
                "pullRequestId": pull_request_node_id,
                "userLogins": reviewers or [],
                "teamSlugs": team_reviewers or [],
                "union": True,
            }
        },
        runner=runner,
    )


async def remove_requested_reviewers(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    reviewers: list[str] | None,
    team_reviewers: list[str] | None,
    runner: GitHubInputRunner,
) -> None:
    """Remove selected review requests while preserving the rest through GraphQL."""
    if not reviewers and not team_reviewers:
        return
    actors = await _fetch_review_actors(owner, repo, pr_number, runner=runner)
    removed_users = set(reviewers or ())
    removed_teams = set(team_reviewers or ())
    await run_graphql(
        _REQUEST_REVIEWS_MUTATION,
        {
            "input": {
                "pullRequestId": actors.pull_request_node_id,
                "userIds": [
                    node_id
                    for login, node_id in actors.users.items()
                    if login not in removed_users
                ],
                "botIds": [
                    node_id
                    for login, node_id in actors.bots.items()
                    if login not in removed_users
                ],
                "teamIds": [
                    node_id
                    for slug, node_id in actors.teams.items()
                    if slug not in removed_teams
                ],
                "union": False,
            }
        },
        runner=runner,
    )


async def add_assignees(
    owner: str,
    repo: str,
    pr_number: int,
    assignees: list[str],
    *,
    runner: GitHubInputRunner,
) -> None:
    """Assign repository users to a pull request through GraphQL."""
    if not assignees:
        return
    pull_request_node_id, assignee_ids = await asyncio.gather(
        _fetch_pr_node_id(owner, repo, pr_number, runner=runner),
        _fetch_assignable_user_ids(owner, repo, assignees, runner=runner),
    )
    await run_graphql(
        _ADD_ASSIGNEES_MUTATION,
        {
            "input": {
                "assignableId": pull_request_node_id,
                "assigneeIds": assignee_ids,
            }
        },
        runner=runner,
    )


async def remove_assignees(
    owner: str,
    repo: str,
    pr_number: int,
    assignees: list[str],
    *,
    runner: GitHubInputRunner,
) -> None:
    """Remove selected pull request assignees through GraphQL."""
    if not assignees:
        return
    pull_request_node_id, current = await _fetch_current_assignees(
        owner, repo, pr_number, runner=runner
    )
    assignee_ids = [current[login] for login in assignees if login in current]
    if not assignee_ids:
        return
    await run_graphql(
        _REMOVE_ASSIGNEES_MUTATION,
        {
            "input": {
                "assignableId": pull_request_node_id,
                "assigneeIds": assignee_ids,
            }
        },
        runner=runner,
    )


async def _fetch_user_connection(
    owner: str,
    repo: str,
    *,
    query: str,
    connection_name: str,
    runner: GitHubInputRunner,
) -> list[PRUser]:
    users: list[PRUser] = []
    after: str | None = None
    while True:
        data = await run_graphql(
            query,
            {"owner": owner, "repo": repo, "after": after},
            runner=runner,
        )
        repository = mapping(mapping(data.get("data")).get("repository"))
        connection = mapping(repository.get(connection_name))
        users.extend(PRUser.model_validate(node) for node in connection_nodes(connection))
        after = _next_cursor(connection, after=after)
        if after is None:
            return users


async def _fetch_assignable_user_ids(
    owner: str,
    repo: str,
    logins: list[str],
    *,
    runner: GitHubInputRunner,
) -> list[str]:
    candidates = await fetch_assignee_candidates(owner, repo, runner)
    ids_by_login = {user.login: user.node_id for user in candidates if user.node_id}
    missing = [login for login in logins if login not in ids_by_login]
    if missing:
        raise ValueError(f"Users are not assignable: {', '.join(missing)}")
    return [ids_by_login[login] for login in logins]


async def _fetch_pr_node_id(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    runner: GitHubInputRunner,
) -> str:
    data = await run_graphql(
        _PR_NODE_ID_QUERY,
        {"owner": owner, "repo": repo, "number": pr_number},
        runner=runner,
    )
    pull_request = _pull_request(data)
    node_id = pull_request.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError(f"PR #{pr_number} node ID not found")
    return node_id


async def _fetch_review_actors(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    runner: GitHubInputRunner,
) -> _ReviewActors:
    pull_request_node_id = ""
    users: dict[str, str] = {}
    bots: dict[str, str] = {}
    teams: dict[str, str] = {}
    after: str | None = None
    while True:
        data = await run_graphql(
            _REVIEW_REQUESTS_QUERY,
            {"owner": owner, "repo": repo, "number": pr_number, "after": after},
            runner=runner,
        )
        pull_request = _pull_request(data)
        raw_node_id = pull_request.get("id")
        if isinstance(raw_node_id, str):
            pull_request_node_id = raw_node_id
        connection = mapping(pull_request.get("reviewRequests"))
        for node in connection_nodes(connection):
            actor = mapping(mapping(node).get("requestedReviewer"))
            _remember_review_actor(actor, users=users, bots=bots, teams=teams)
        after = _next_cursor(connection, after=after)
        if after is None:
            break
    if not pull_request_node_id:
        raise ValueError(f"PR #{pr_number} node ID not found")
    return _ReviewActors(pull_request_node_id, users, bots, teams)


async def _fetch_current_assignees(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    runner: GitHubInputRunner,
) -> tuple[str, dict[str, str]]:
    data = await run_graphql(
        _CURRENT_ASSIGNEES_QUERY,
        {"owner": owner, "repo": repo, "number": pr_number},
        runner=runner,
    )
    pull_request = _pull_request(data)
    node_id = pull_request.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError(f"PR #{pr_number} node ID not found")
    current: dict[str, str] = {}
    for value in connection_nodes(pull_request.get("assignedActors")):
        actor = mapping(value)
        login = actor.get("login")
        actor_id = actor.get("id")
        if isinstance(login, str) and isinstance(actor_id, str):
            current[login] = actor_id
    return node_id, current


def _remember_review_actor(
    actor: Mapping[str, object],
    *,
    users: dict[str, str],
    bots: dict[str, str],
    teams: dict[str, str],
) -> None:
    actor_id = actor.get("id")
    typename = actor.get("__typename")
    if not isinstance(actor_id, str) or not isinstance(typename, str):
        return
    if typename in {"Team", "EnterpriseTeam"}:
        slug = actor.get("slug")
        if isinstance(slug, str):
            teams[slug] = actor_id
        return
    login = actor.get("login")
    if not isinstance(login, str):
        return
    if typename == "Bot":
        bots[login] = actor_id
    else:
        users[login] = actor_id


def _pull_request(data: Mapping[str, object]) -> Mapping[str, object]:
    repository = mapping(mapping(data.get("data")).get("repository"))
    return mapping(repository.get("pullRequest"))


def _next_cursor(connection: Mapping[str, object], *, after: str | None) -> str | None:
    page_info = mapping(connection.get("pageInfo"))
    if page_info.get("hasNextPage") is not True:
        return None
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor or cursor == after:
        raise ValueError("GitHub GraphQL pagination returned no next cursor")
    return cursor
