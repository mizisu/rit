"""Read complete review checkpoints and immutable commit comparisons through gh."""

import asyncio
import json
import re

from pydantic import TypeAdapter

from rit.core.diff import parse_multi_file_patch_summaries
from rit.services.gh_request import GitHubInputRunner
from rit.services.graphql_request import (
    GraphQLRequestError,
    connection_nodes,
    mapping,
    run_graphql,
)
from rit.state.file_projection import file_from_summary
from rit.state.models import PRFile, PRReview, ReviewState
from rit.state.review_scope import PRCommit, ReviewHistory, ReviewScope

_HISTORY_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  viewer { login }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      baseRefOid headRefOid
      commits(first: 100, after: $after) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          commit {
            oid messageHeadline
            parents(first: 1) { totalCount nodes { oid } }
          }
        }
      }
    }
  }
}
"""


_REVIEWS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $author: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      baseRefOid headRefOid
      reviews(first: 100, after: $after, author: $author) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          databaseId author { login } state submittedAt commit { oid }
        }
      }
    }
  }
}
"""


async def fetch_review_history(
    owner: str, repo: str, number: int, *, runner: GitHubInputRunner
) -> ReviewHistory:
    """Paginate commits and reviews independently; timestamps never infer a SHA."""
    commits: list[PRCommit] = []
    cursor: str | None = None
    seen: set[str] = set()
    refs: tuple[str, str] | None = None
    viewer = ""
    while True:
        result = await run_graphql(
            _HISTORY_QUERY,
            {"owner": owner, "repo": repo, "number": number, "after": cursor},
            runner=runner,
        )
        data = mapping(result.get("data"))
        login = mapping(data.get("viewer")).get("login")
        if not isinstance(login, str) or not login:
            raise ValueError("GitHub did not return the current user")
        viewer = login
        pr = mapping(mapping(data.get("repository")).get("pullRequest"))
        base, head = pr.get("baseRefOid"), pr.get("headRefOid")
        if (
            not isinstance(base, str)
            or not isinstance(head, str)
            or not base
            or not head
        ):
            raise ValueError("GitHub did not return PR comparison refs")
        if refs is not None and refs != (base, head):
            raise ValueError("PR changed while loading commits; refresh and try again")
        refs = (base, head)
        connection = mapping(pr.get("commits"))
        commits.extend(
            PRCommit.model_validate(mapping(node).get("commit"))
            for node in connection_nodes(connection)
        )
        page = mapping(connection.get("pageInfo"))
        if page.get("hasNextPage") is not True:
            if len(commits) != connection.get("totalCount"):
                raise ValueError("GitHub returned an incomplete commit list")
            break
        next_cursor = page.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
            raise ValueError("GitHub returned an invalid commit pagination cursor")
        seen.add(next_cursor)
        cursor = next_cursor

    reviews: list[PRReview] = []
    cursor = None
    seen.clear()
    while True:
        result = await run_graphql(
            _REVIEWS_QUERY,
            {
                "owner": owner,
                "repo": repo,
                "number": number,
                "author": viewer,
                "after": cursor,
            },
            runner=runner,
        )
        pr = mapping(
            mapping(mapping(result.get("data")).get("repository")).get("pullRequest")
        )
        if (pr.get("baseRefOid"), pr.get("headRefOid")) != refs:
            raise ValueError("PR changed while loading reviews; refresh and try again")
        connection = mapping(pr.get("reviews"))
        reviews.extend(
            TypeAdapter(list[PRReview]).validate_python(connection_nodes(connection))
        )
        page = mapping(connection.get("pageInfo"))
        if page.get("hasNextPage") is not True:
            if len(reviews) != connection.get("totalCount"):
                raise ValueError("GitHub returned an incomplete review list")
            break
        next_cursor = page.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
            raise ValueError("GitHub returned an invalid review pagination cursor")
        seen.add(next_cursor)
        cursor = next_cursor
    submitted = [
        review
        for review in reviews
        if review.user is not None
        and review.user.login.casefold() == viewer.casefold()
        and review.state != ReviewState.PENDING
        and review.submitted_at is not None
    ]
    last_review = max(
        submitted,
        key=lambda review: (review.submitted_at, review.id),
        default=None,
    )
    return ReviewHistory(base, head, tuple(commits), last_review)


async def fetch_comparison_files(
    owner: str, repo: str, scope: ReviewScope, *, runner: GitHubInputRunner
) -> list[PRFile]:
    """Require an ancestor comparison and a complete, consistent patch response."""
    if not all(
        re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (scope.base_sha, scope.head_sha)
    ):
        raise ValueError("Comparison requires full immutable commit SHAs")
    if scope.base_sha == scope.head_sha:
        return []
    endpoint = f"repos/{owner}/{repo}/compare/{scope.base_sha}...{scope.head_sha}"
    result = json.loads(await runner(["api", f"{endpoint}?per_page=1"]))
    if (
        mapping(result).get("status") not in {"ahead", "identical"}
        or mapping(mapping(result).get("merge_base_commit")).get("sha")
        != scope.base_sha
    ):
        raise ValueError("History diverged (rebase or force-push); use All changes")
    files = mapping(result).get("files")
    if not isinstance(files, list):
        raise GraphQLRequestError("GitHub did not return comparison files")
    if len(files) >= 300:
        raise ValueError("GitHub caps comparisons at 300 files; use All changes")
    if not files:
        return []
    raw_diff = await runner(
        ["api", endpoint, "-H", "Accept: application/vnd.github.diff"]
    )
    return await asyncio.to_thread(_comparison_files, files, raw_diff)


def _comparison_files(metadata: list[object], raw_diff: str) -> list[PRFile]:
    summaries = parse_multi_file_patch_summaries(raw_diff)
    by_path = {summary.filename: summary for summary in summaries}
    expected = {mapping(file).get("filename") for file in metadata}
    if set(by_path) != expected or len(summaries) != len(metadata):
        raise ValueError("GitHub returned an incomplete comparison diff")
    files: list[PRFile] = []
    for raw_file in metadata:
        data = mapping(raw_file)
        filename = data.get("filename")
        if not isinstance(filename, str):
            raise GraphQLRequestError("GitHub returned an invalid comparison path")
        summary = by_path[filename]
        if not summary.is_binary and (
            summary.additions != data.get("additions")
            or summary.deletions != data.get("deletions")
        ):
            raise ValueError(f"GitHub returned an incomplete patch for {filename}")
        files.append(file_from_summary(summary))
    return files
