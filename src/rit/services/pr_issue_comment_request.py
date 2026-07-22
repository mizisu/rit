from __future__ import annotations

from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import mapping, run_graphql
from rit.state.models import PRIssueComment

__all__ = (
    "create_issue_comment",
)


async def create_issue_comment(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    body: str,
    runner: GitHubInputRunner,
) -> PRIssueComment:
    """Create a PR-level issue comment via GraphQL."""
    identity = await run_graphql(
        _PR_NODE_ID_QUERY,
        {"owner": owner, "repo": repo, "number": pr_number},
        runner=runner,
    )
    repository = mapping(mapping(identity.get("data")).get("repository"))
    pull_request = mapping(repository.get("pullRequest"))
    pull_request_node_id = pull_request.get("id")
    if not isinstance(pull_request_node_id, str) or not pull_request_node_id:
        raise ValueError(f"PR #{pr_number} node ID not found")

    data = await run_graphql(
        _ADD_COMMENT_MUTATION,
        {"input": {"subjectId": pull_request_node_id, "body": body}},
        runner=runner,
    )
    mutation = mapping(mapping(data.get("data")).get("addComment"))
    comment_edge = mapping(mutation.get("commentEdge"))
    comment = mapping(comment_edge.get("node"))
    if not comment:
        raise ValueError("addComment did not return an issue comment")
    return PRIssueComment.model_validate(comment)


_PR_NODE_ID_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
    }
  }
}
"""

_ADD_COMMENT_MUTATION = """
mutation($input: AddCommentInput!) {
  addComment(input: $input) {
    commentEdge {
      node {
        databaseId
        body
        createdAt
        updatedAt
        htmlUrl: url
        author {
          login
          avatarUrl
        }
      }
    }
  }
}
"""
