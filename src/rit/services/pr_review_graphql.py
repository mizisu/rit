from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter

from rit.services.gh_request import (
    GitHubInputRequest,
    GitHubInputRunner,
    run_input_request,
)
from rit.services.graphql_mutations import GraphQLMutationError, graphql_error_summary
from rit.services.pr_review_comment_selection import (
    review_comment_target,
    select_created_review_comment,
)
from rit.state.models import (
    PendingReviewComment,
    PRComment,
    PRReview,
    ReviewThread,
)

__all__ = (
    "create_pending_review",
    "create_review_comment",
    "delete_pending_review",
    "graphql_request",
    "list_review_comments",
    "submit_pending_review",
    "submit_review",
)

_REVIEW_FIELDS = """
nodeId: id
databaseId
author {
  login
  avatarUrl
}
state
body
createdAt
submittedAt
"""

_REVIEW_COMMENT_FIELDS = """
databaseId
author {
  login
  avatarUrl
}
body
createdAt
updatedAt
diffHunk
path
line
originalLine
startLine
originalStartLine
replyTo {
  databaseId
}
pullRequestReview {
  databaseId
}
"""

_ADD_REVIEW_MUTATION = f"""
mutation($input: AddPullRequestReviewInput!) {{
  addPullRequestReview(input: $input) {{
    pullRequestReview {{
      {_REVIEW_FIELDS}
      comments(first: 100) {{
        nodes {{
          {_REVIEW_COMMENT_FIELDS}
        }}
      }}
    }}
  }}
}}
"""

_DELETE_REVIEW_MUTATION = f"""
mutation($reviewId: ID!) {{
  deletePullRequestReview(input: {{pullRequestReviewId: $reviewId}}) {{
    pullRequestReview {{
      {_REVIEW_FIELDS}
    }}
  }}
}}
"""

_SUBMIT_REVIEW_MUTATION = f"""
mutation($input: SubmitPullRequestReviewInput!) {{
  submitPullRequestReview(input: $input) {{
    pullRequestReview {{
      {_REVIEW_FIELDS}
    }}
  }}
}}
"""

_PR_REVIEW_IDENTITY_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      reviews(first: 100) {
        nodes {
          id
          databaseId
        }
      }
    }
  }
}
"""

_REVIEW_THREADS_QUERY = f"""
query($owner: String!, $repo: String!, $number: Int!) {{
  repository(owner: $owner, name: $repo) {{
    pullRequest(number: $number) {{
      reviewThreads(first: 100) {{
        nodes {{
          id
          isResolved
          path
          line
          originalLine
          startLine
          originalStartLine
          diffSide
          startDiffSide
          subjectType
          comments(first: 100) {{
            nodes {{
              {_REVIEW_COMMENT_FIELDS}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

_PRReviewCommentListAdapter: TypeAdapter[list[PRComment]] = TypeAdapter(list[PRComment])


@dataclass(frozen=True)
class _PullRequestReviewIdentity:
    pull_request_node_id: str
    review_node_id_by_database_id: dict[int, str]


@dataclass(frozen=True)
class _ReviewMutationResult:
    review: PRReview
    comments: list[PRComment]


def graphql_request(
    query: str,
    variables: Mapping[str, object],
) -> GitHubInputRequest:
    """Build a gh GraphQL request that sends variables through stdin."""
    return GitHubInputRequest(
        args=("api", "graphql", "--input", "-"),
        input_text=json.dumps({"query": query, "variables": variables}),
    )


async def create_pending_review(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    comments: Sequence[PendingReviewComment],
    body: str | None = None,
    commit_id: str | None = None,
    runner: GitHubInputRunner,
) -> PRReview:
    """Create a pending PR review via GraphQL review threads."""
    identity = await _fetch_pr_review_identity(
        owner,
        repo,
        pr_number,
        runner=runner,
    )
    result = await _add_pull_request_review(
        pull_request_node_id=identity.pull_request_node_id,
        event=None,
        body=body,
        comments=comments,
        commit_id=commit_id,
        runner=runner,
    )
    if body and not result.review.body:
        return result.review.model_copy(update={"body": body})
    return result.review


async def submit_review(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    event: str,
    body: str | None = None,
    comments: Sequence[PendingReviewComment] | None = None,
    commit_id: str | None = None,
    runner: GitHubInputRunner,
) -> PRReview:
    """Create and submit a PR review via GraphQL."""
    identity = await _fetch_pr_review_identity(
        owner,
        repo,
        pr_number,
        runner=runner,
    )
    return (
        await _add_pull_request_review(
            pull_request_node_id=identity.pull_request_node_id,
            event=event,
            body=body,
            comments=comments or (),
            commit_id=commit_id,
            runner=runner,
        )
    ).review


async def create_review_comment(
    owner: str,
    repo: str,
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
    """Create an inline review comment via a submitted GraphQL review."""
    target = review_comment_target(
        body=body,
        path=path,
        line=line,
        side=side,
        start_line=start_line,
        start_side=start_side,
    )
    review = await submit_review(
        owner,
        repo,
        pr_number,
        event="COMMENT",
        comments=[target.pending_comment()],
        commit_id=commit_id,
        runner=runner,
    )
    comments = (
        await list_review_comments(
            owner,
            repo,
            pr_number,
            review_id=review.id,
            runner=runner,
        )
        if review.id
        else []
    )
    return select_created_review_comment(
        comments,
        target,
        review_id=review.id if review.id else None,
    )


async def submit_pending_review(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    review_id: int,
    event: str,
    body: str | None = None,
    runner: GitHubInputRunner,
) -> PRReview:
    """Submit an existing pending PR review via GraphQL."""
    identity = await _fetch_pr_review_identity(
        owner,
        repo,
        pr_number,
        runner=runner,
    )
    review_node_id = _review_node_id(identity, review_id)
    payload: dict[str, object] = {
        "pullRequestReviewId": review_node_id,
        "event": event,
    }
    if body is not None:
        payload["body"] = body
    return _parse_review_payload(
        await _run_graphql(
            _SUBMIT_REVIEW_MUTATION,
            {"input": payload},
            runner=runner,
        ),
        mutation_name="submitPullRequestReview",
    )


async def delete_pending_review(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    review_id: int,
    runner: GitHubInputRunner,
) -> None:
    """Delete a pending PR review via GraphQL."""
    identity = await _fetch_pr_review_identity(
        owner,
        repo,
        pr_number,
        runner=runner,
    )
    await _run_graphql(
        _DELETE_REVIEW_MUTATION,
        {"reviewId": _review_node_id(identity, review_id)},
        runner=runner,
    )


async def list_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    review_id: int,
    runner: GitHubInputRunner,
) -> list[PRComment]:
    """Fetch one review's comments from GraphQL review threads."""
    threads = _parse_review_threads_payload(
        await _run_graphql(
            _REVIEW_THREADS_QUERY,
            {"owner": owner, "repo": repo, "number": pr_number},
            runner=runner,
        )
    )
    comments: list[PRComment] = []
    for thread in threads:
        for comment in thread.comments:
            if comment.pull_request_review_id != review_id:
                continue
            comments.append(_comment_with_thread_position(comment, thread))
    if len(comments) < 2:
        return comments
    return sorted(comments, key=lambda comment: comment.created_at)


async def _add_pull_request_review(
    *,
    pull_request_node_id: str,
    event: str | None,
    body: str | None,
    comments: Sequence[PendingReviewComment],
    commit_id: str | None,
    runner: GitHubInputRunner,
) -> _ReviewMutationResult:
    input_payload: dict[str, object] = {"pullRequestId": pull_request_node_id}
    if event is not None:
        input_payload["event"] = event
    if body is not None:
        input_payload["body"] = body
    if commit_id:
        input_payload["commitOID"] = commit_id
    if comments:
        input_payload["threads"] = [_thread_input(comment) for comment in comments]

    return _parse_add_review_payload(
        await _run_graphql(
            _ADD_REVIEW_MUTATION,
            {"input": input_payload},
            runner=runner,
        )
    )


async def _fetch_pr_review_identity(
    owner: str,
    repo: str,
    pr_number: int,
    *,
    runner: GitHubInputRunner,
) -> _PullRequestReviewIdentity:
    data = await _run_graphql(
        _PR_REVIEW_IDENTITY_QUERY,
        {"owner": owner, "repo": repo, "number": pr_number},
        runner=runner,
    )
    pr = _pull_request_mapping(data)
    node_id = pr.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise GraphQLMutationError(f"PR #{pr_number} node ID not found")

    review_ids: dict[int, str] = {}
    for review in _connection_nodes(pr.get("reviews")):
        if not isinstance(review, Mapping):
            continue
        database_id = review.get("databaseId")
        review_node_id = review.get("id")
        if isinstance(database_id, int) and isinstance(review_node_id, str):
            review_ids[database_id] = review_node_id
    return _PullRequestReviewIdentity(
        pull_request_node_id=node_id,
        review_node_id_by_database_id=review_ids,
    )


async def _run_graphql(
    query: str,
    variables: Mapping[str, object],
    *,
    runner: GitHubInputRunner,
) -> Mapping[str, object]:
    result = await run_input_request(graphql_request(query, variables), runner)
    data = json.loads(result)
    if not isinstance(data, Mapping):
        raise GraphQLMutationError("GitHub GraphQL response was not an object")
    errors = data.get("errors")
    if errors:
        message = graphql_error_summary(data) or str(errors)
        raise GraphQLMutationError(message)
    return data


def _review_node_id(
    identity: _PullRequestReviewIdentity,
    review_id: int,
) -> str:
    node_id = identity.review_node_id_by_database_id.get(review_id)
    if node_id:
        return node_id
    raise GraphQLMutationError(f"Review {review_id} node ID not found")


def _thread_input(comment: PendingReviewComment) -> dict[str, object]:
    thread: dict[str, object] = {
        "path": comment.path,
        "line": comment.line,
        "side": comment.side,
        "body": comment.body,
    }
    if comment.start_line is not None:
        thread["startLine"] = comment.start_line
        thread["startSide"] = comment.start_side or comment.side
    return thread


def _parse_add_review_payload(data: Mapping[str, object]) -> _ReviewMutationResult:
    review_data = _mutation_review_data(data, "addPullRequestReview")
    review = PRReview.model_validate(review_data)
    comments = _comments_from_review_data(review_data)
    return _ReviewMutationResult(review=review, comments=comments)


def _parse_review_payload(
    data: Mapping[str, object],
    *,
    mutation_name: str,
) -> PRReview:
    return PRReview.model_validate(_mutation_review_data(data, mutation_name))


def _mutation_review_data(
    data: Mapping[str, object],
    mutation_name: str,
) -> Mapping[str, object]:
    graphql_data = _mapping(data.get("data"))
    mutation_data = _mapping(graphql_data.get(mutation_name))
    review_data = _mapping(mutation_data.get("pullRequestReview"))
    if not review_data:
        raise GraphQLMutationError(f"{mutation_name} did not return a review")
    return review_data


def _comments_from_review_data(review_data: Mapping[str, object]) -> list[PRComment]:
    nodes = _connection_nodes(review_data.get("comments"))
    if not nodes:
        return []
    if len(nodes) == 1:
        return [PRComment.model_validate(nodes[0])]
    return _PRReviewCommentListAdapter.validate_python(nodes)


def _parse_review_threads_payload(data: Mapping[str, object]) -> list[ReviewThread]:
    pr = _pull_request_mapping(data)
    nodes = _connection_nodes(pr.get("reviewThreads"))
    if not nodes:
        return []
    if len(nodes) == 1:
        return [ReviewThread.model_validate(nodes[0])]
    return TypeAdapter(list[ReviewThread]).validate_python(nodes)


def _comment_with_thread_position(
    comment: PRComment,
    thread: ReviewThread,
) -> PRComment:
    update: dict[str, Any] = {
        "path": comment.path or thread.path,
        "side": thread.diff_side or comment.side,
    }
    if thread.line is not None:
        update["line"] = thread.line
    if thread.original_line is not None:
        update["original_line"] = thread.original_line
    if thread.start_line is not None:
        update["start_line"] = thread.start_line
    if thread.original_start_line is not None:
        update["original_start_line"] = thread.original_start_line
    if thread.start_diff_side:
        update["start_side"] = thread.start_diff_side
    return comment.model_copy(update=update)


def _pull_request_mapping(data: Mapping[str, object]) -> Mapping[str, object]:
    graphql_data = _mapping(data.get("data"))
    repository = _mapping(graphql_data.get("repository"))
    return _mapping(repository.get("pullRequest"))


def _connection_nodes(value: object) -> list[object]:
    connection = _mapping(value)
    nodes = connection.get("nodes")
    if isinstance(nodes, list):
        return cast("list[object]", nodes)
    return []


def _mapping(value: object) -> Mapping[str, object]:
    return cast("Mapping[str, object]", value) if isinstance(value, Mapping) else {}
