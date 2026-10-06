"""Fetch GitHub's merge verdict independently of checks and PR discussion."""

from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import run_graphql
from rit.services.pr_graphql_response import parse_pull_request_graphql_data
from rit.state.pr_overview import PRMergeSnapshot

_MERGE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      baseRefOid headRefOid baseRefName
      state isDraft mergeable mergeStateStatus reviewDecision isInMergeQueue
    }
  }
}
"""


async def fetch_pr_merge_status(
    owner: str, repo: str, pr_number: int, *, runner: GitHubInputRunner
) -> PRMergeSnapshot:
    """Keep missing or inaccessible merge information distinct from a ready PR."""
    response = await run_graphql(
        _MERGE_QUERY,
        {"owner": owner, "repo": repo, "number": pr_number},
        runner=runner,
    )
    return PRMergeSnapshot.model_validate(
        parse_pull_request_graphql_data(response, pr_number=pr_number)
    )
