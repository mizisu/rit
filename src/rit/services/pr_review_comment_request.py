from __future__ import annotations

import json
from collections.abc import Mapping

from rit.services.gh_request import (
    GitHubInputRequest,
    GitHubInputRunner,
    run_input_request,
)
from rit.services.graphql_request import graphql_request, mapping, run_graphql
from rit.state.models import PRComment

__all__ = (
    "create_review_comment",
    "create_review_comment_request",
    "delete_review_comment",
    "delete_review_comment_request",
    "parse_created_review_comment_response",
    "parse_review_comment_response",
    "update_review_comment",
    "update_review_comment_request",
)


def create_review_comment_request(
    repo_full_name: str,
    pr_number: int,
    *,
    body: str,
    commit_id: str,
    path: str,
    line: int,
    side: str,
    start_line: int | None = None,
    start_side: str | None = None,
) -> GitHubInputRequest:
    """Build a REST request for a standalone inline review comment."""
    payload: dict[str, object] = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "line": line,
        "side": side,
    }
    if start_line is not None:
        payload["start_line"] = start_line
        payload["start_side"] = start_side or side
    return GitHubInputRequest(
        args=(
            "api",
            "--method",
            "POST",
            f"/repos/{repo_full_name}/pulls/{pr_number}/comments",
            "--input",
            "-",
        ),
        input_text=json.dumps(payload),
    )


def parse_created_review_comment_response(result: str) -> PRComment:
    """Parse a standalone inline review comment response."""
    return PRComment.model_validate(json.loads(result))


async def create_review_comment(
    repo_full_name: str,
    pr_number: int,
    *,
    body: str,
    commit_id: str,
    path: str,
    line: int,
    side: str,
    start_line: int | None = None,
    start_side: str | None = None,
    runner: GitHubInputRunner,
) -> PRComment:
    """Create a standalone inline review comment through REST."""
    request = create_review_comment_request(
        repo_full_name,
        pr_number,
        body=body,
        commit_id=commit_id,
        path=path,
        line=line,
        side=side,
        start_line=start_line,
        start_side=start_side,
    )
    return parse_created_review_comment_response(
        await run_input_request(request, runner)
    )


def update_review_comment_request(
    comment_node_id: str,
    *,
    body: str,
) -> GitHubInputRequest:
    """Build a GraphQL request for updating a PR review comment."""
    return graphql_request(
        _UPDATE_REVIEW_COMMENT_MUTATION,
        {
            "input": {
                "pullRequestReviewCommentId": comment_node_id,
                "body": body,
            }
        },
    )


def parse_review_comment_response(result: str) -> PRComment:
    """Parse an updated GraphQL review comment response."""
    data = json.loads(result)
    if not isinstance(data, Mapping):
        raise TypeError("GitHub GraphQL response was not an object")
    return _parse_review_comment_data(data)


async def update_review_comment(
    comment_node_id: str,
    *,
    body: str,
    runner: GitHubInputRunner,
) -> PRComment:
    """Update a PR review comment through GraphQL."""
    data = await run_graphql(
        _UPDATE_REVIEW_COMMENT_MUTATION,
        {
            "input": {
                "pullRequestReviewCommentId": comment_node_id,
                "body": body,
            }
        },
        runner=runner,
    )
    return _parse_review_comment_data(data)


def delete_review_comment_request(comment_node_id: str) -> GitHubInputRequest:
    """Build a GraphQL request for deleting a PR review comment."""
    return graphql_request(
        _DELETE_REVIEW_COMMENT_MUTATION,
        {"input": {"id": comment_node_id}},
    )


async def delete_review_comment(
    comment_node_id: str,
    *,
    runner: GitHubInputRunner,
) -> None:
    """Delete a PR review comment through GraphQL."""
    await run_graphql(
        _DELETE_REVIEW_COMMENT_MUTATION,
        {"input": {"id": comment_node_id}},
        runner=runner,
    )


def _parse_review_comment_data(data: Mapping[str, object]) -> PRComment:
    mutation = mapping(mapping(data.get("data")).get("updatePullRequestReviewComment"))
    comment = mapping(mutation.get("pullRequestReviewComment"))
    if not comment:
        raise ValueError("updatePullRequestReviewComment did not return a comment")
    return PRComment.model_validate(comment)


_DELETE_REVIEW_COMMENT_MUTATION = """
mutation($input: DeletePullRequestReviewCommentInput!) {
  deletePullRequestReviewComment(input: $input) {
    pullRequestReviewComment {
      nodeId: id
      databaseId
    }
  }
}
"""


_UPDATE_REVIEW_COMMENT_MUTATION = """
mutation($input: UpdatePullRequestReviewCommentInput!) {
  updatePullRequestReviewComment(input: $input) {
    pullRequestReviewComment {
      nodeId: id
      databaseId
      body
      createdAt
      updatedAt
      diffHunk
      path
      line
      originalLine
      startLine
      originalStartLine
      author {
        login
        avatarUrl
      }
      replyTo {
        databaseId
      }
      pullRequestReview {
        databaseId
      }
    }
  }
}
"""
