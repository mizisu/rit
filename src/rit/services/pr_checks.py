"""Fetch check runs and legacy statuses for a PR head commit."""

from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import (
    GraphQLRequestError,
    connection_nodes,
    mapping,
    run_graphql,
)
from rit.state.pr_overview import CheckOutcome, PRCheck, PRChecksSnapshot

_CHECK_STATUSES: dict[str, CheckOutcome] = dict.fromkeys(
    ("QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"), "pending"
)
_CHECK_CONCLUSIONS: dict[str, CheckOutcome] = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "TIMED_OUT": "failure",
    "ACTION_REQUIRED": "failure",
    "STARTUP_FAILURE": "failure",
    "NEUTRAL": "neutral",
    "SKIPPED": "neutral",
    "CANCELLED": "cancelled",
    "STALE": "unknown",
}
_COMMIT_STATUSES: dict[str, CheckOutcome] = {
    "SUCCESS": "success",
    "PENDING": "pending",
    "EXPECTED": "pending",
    "FAILURE": "failure",
    "ERROR": "failure",
}

_CHECKS_QUERY = """
query($owner: String!, $repo: String!, $head: GitObjectID!, $after: String) {
  repository(owner: $owner, name: $repo) {
    object(oid: $head) {
      ... on Commit {
        oid
        statusCheckRollup {
          state
          contexts(first: 100, after: $after) {
            pageInfo { hasNextPage endCursor }
            nodes {
              __typename
              ... on CheckRun { id name status conclusion detailsUrl }
              ... on StatusContext { id context state targetUrl }
            }
          }
        }
      }
    }
  }
}
"""


async def fetch_pr_checks(
    owner: str, repo: str, head_sha: str, *, runner: GitHubInputRunner
) -> PRChecksSnapshot:
    """Fetch every reported context without depending on a surviving head ref."""
    checks: dict[str, PRCheck] = {}
    after: str | None = None
    state: str | None = None
    seen_cursors: set[str] = set()
    while True:
        response = await run_graphql(
            _CHECKS_QUERY,
            {"owner": owner, "repo": repo, "head": head_sha, "after": after},
            runner=runner,
        )
        repository = mapping(mapping(response.get("data")).get("repository"))
        commit = mapping(repository.get("object"))
        if not head_sha or commit.get("oid") != head_sha:
            raise ValueError("GitHub did not return the requested PR head commit")
        if "statusCheckRollup" not in commit:
            raise ValueError("GitHub did not return check information")
        if commit.get("statusCheckRollup") is None:
            return PRChecksSnapshot(head_sha, None)
        rollup = mapping(commit.get("statusCheckRollup"))
        raw_state = rollup.get("state")
        if not isinstance(raw_state, str) or not isinstance(
            rollup.get("contexts"), dict
        ):
            raise GraphQLRequestError("GitHub returned invalid check information")
        state = raw_state
        connection = mapping(rollup.get("contexts"))
        for node in connection_nodes(connection):
            check = parse_pr_check(node)
            checks[check.node_id] = check
        page_info = mapping(connection.get("pageInfo"))
        if not isinstance(page_info.get("hasNextPage"), bool) or not isinstance(
            connection.get("nodes"), list
        ):
            raise GraphQLRequestError("GitHub returned an incomplete check connection")
        if page_info.get("hasNextPage") is not True:
            break
        cursor = page_info.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
            raise ValueError("GitHub check pagination returned no next cursor")
        seen_cursors.add(cursor)
        after = cursor
    return PRChecksSnapshot(head_sha, state, tuple(checks.values()))


def parse_pr_check(value: object) -> PRCheck:
    """Normalize a context without treating unknown or cancelled results as success."""
    node = mapping(value)
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError("GitHub check did not include an ID")
    if node.get("__typename") == "CheckRun":
        name = node.get("name")
        url = node.get("detailsUrl")
        status = node.get("status")
        conclusion = node.get("conclusion")
        if not isinstance(status, str) or (
            conclusion is not None and not isinstance(conclusion, str)
        ):
            raise GraphQLRequestError("GitHub returned invalid check status")
        detail = conclusion if status == "COMPLETED" else status
        outcome = (
            _CHECK_CONCLUSIONS.get(conclusion or "", "unknown")
            if status == "COMPLETED"
            else _CHECK_STATUSES.get(status, "unknown")
        )
    elif node.get("__typename") == "StatusContext":
        name = node.get("context")
        url = node.get("targetUrl")
        detail = node.get("state")
        if not isinstance(detail, str):
            raise GraphQLRequestError("GitHub returned invalid commit status")
        outcome = _COMMIT_STATUSES.get(detail, "unknown")
    else:
        raise ValueError("GitHub returned an unsupported check context")
    if not isinstance(name, str) or not name:
        raise ValueError("GitHub check did not include a name")
    return PRCheck(
        node_id,
        name,
        outcome,
        detail if isinstance(detail, str) else "UNKNOWN",
        url if isinstance(url, str) else "",
    )
