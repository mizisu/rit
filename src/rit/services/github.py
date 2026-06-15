import asyncio
import json
import queue
import subprocess
import threading
import urllib.request
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter

from rit.state.models import (
    NodeList,
    PR,
    PendingReviewComment,
    PRComment,
    PRFile,
    PRIssueComment,
    PRReview,
    PRTeam,
    PRUser,
    ReviewThread,
    group_comments_into_threads,
)

_PRCommentListAdapter: TypeAdapter[list[PRComment]] = TypeAdapter(list[PRComment])
_PRFileListAdapter: TypeAdapter[list[PRFile]] = TypeAdapter(list[PRFile])
_PRIssueCommentListAdapter: TypeAdapter[list[PRIssueComment]] = TypeAdapter(
    list[PRIssueComment]
)
_PRReviewListAdapter: TypeAdapter[list[PRReview]] = TypeAdapter(list[PRReview])
_PRUserListAdapter: TypeAdapter[list[PRUser]] = TypeAdapter(list[PRUser])
_PRTeamListAdapter: TypeAdapter[list[PRTeam]] = TypeAdapter(list[PRTeam])
PR_FILES_PER_PAGE = 100
PR_FILES_MAX_REST_PAGES = 30
PR_FILES_PAGE_CONCURRENCY = 6


def _fetch_url_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "rit"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def _iter_url_diff_sections(url: str) -> Iterator[str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "rit"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        current_section: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace")
            if line.startswith("diff --git ") and current_section:
                yield "".join(current_section).rstrip("\n")
                current_section = [line]
            else:
                current_section.append(line)

        if current_section:
            yield "".join(current_section).rstrip("\n")


def _remaining_pr_file_pages(total_count: int | None) -> list[int]:
    if total_count is None or total_count <= PR_FILES_PER_PAGE:
        return []

    page_count = min(
        PR_FILES_MAX_REST_PAGES,
        (total_count + PR_FILES_PER_PAGE - 1) // PR_FILES_PER_PAGE,
    )
    return list(range(2, page_count + 1))


@dataclass
class GitHubRepo:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class ReviewThreadInfo:
    thread_id: str  # GraphQL node ID
    is_resolved: bool
    path: str
    line: int | None
    root_comment_id: int  # Database ID of root comment


@dataclass
class PRDiscussion:
    body: str
    reviews: list[PRReview]
    issue_comments: list[PRIssueComment]
    review_threads: list[ReviewThread]


class GitHubError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _review_threads_from_rest_comments(
    comments: list[PRComment],
) -> list[ReviewThread]:
    threads: list[ReviewThread] = []
    normalized_comments = [
        _comment_with_normalized_author(comment) for comment in comments
    ]
    for thread in group_comments_into_threads(normalized_comments):
        root = thread.root_comment
        threads.append(
            ReviewThread.model_validate(
                {
                    "id": "",
                    "isResolved": False,
                    "path": root.path,
                    "line": root.line,
                    "originalLine": root.original_line,
                    "diffSide": root.side,
                    "comments": NodeList(nodes=thread.all_comments),
                }
            )
        )
    return threads


def _comment_with_normalized_author(comment: PRComment) -> PRComment:
    user = comment.user
    if user is None or not user.login.endswith("[bot]"):
        return comment

    return comment.model_copy(
        update={"user": user.model_copy(update={"login": user.login[: -len("[bot]")]})}
    )


_PR_SUMMARY_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      number
      title
      body
      state
      isDraft
      additions
      deletions
      changedFiles
      createdAt
      updatedAt
      mergedAt
      closedAt
      author {
        login
        avatarUrl
      }
      baseRefName
      headRefName
      baseRefOid
      headRefOid
      assignees(first: 20) {
        nodes {
          login
          avatarUrl
        }
      }
      labels(first: 50) {
        nodes {
          name
          color
          description
        }
      }
      reviewRequests(first: 20) {
        nodes {
          requestedReviewer {
            ... on User {
              login
              avatarUrl
            }
            ... on Team {
              name
              slug
            }
          }
        }
      }
    }
  }
}
"""


_PR_DISCUSSION_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      body
      reviews(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          state
          body
          submittedAt
        }
      }
      reviewThreads(first: 100) {
        nodes {
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
          comments(first: 100) {
            nodes {
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
              replyTo {
                databaseId
              }
              pullRequestReview {
                databaseId
              }
            }
          }
        }
      }
      comments(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          body
          createdAt
          updatedAt
        }
      }
    }
  }
}
"""


_PR_FAST_DISCUSSION_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      body
      reviews(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          state
          body
          submittedAt
        }
      }
      comments(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          body
          createdAt
          updatedAt
        }
      }
    }
  }
}
"""


_PR_GRAPHQL_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      id
      number
      title
      body
      state
      isDraft
      additions
      deletions
      changedFiles
      createdAt
      updatedAt
      mergedAt
      closedAt
      
      author {
        login
        avatarUrl
      }
      
      baseRefName
      headRefName
      baseRefOid
      headRefOid
      
      assignees(first: 20) {
        nodes {
          login
          avatarUrl
        }
      }
      
      labels(first: 50) {
        nodes {
          name
          color
          description
        }
      }
      
      reviewRequests(first: 20) {
        nodes {
          requestedReviewer {
            ... on User {
              login
              avatarUrl
            }
            ... on Team {
              name
              slug
            }
          }
        }
      }
      
      reviews(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          state
          body
          submittedAt
        }
      }
      
      reviewThreads(first: 100) {
        nodes {
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
          comments(first: 100) {
            nodes {
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
              replyTo {
                databaseId
              }
              pullRequestReview {
                databaseId
              }
            }
          }
        }
      }
      
      comments(first: 100) {
        nodes {
          databaseId
          author {
            login
            avatarUrl
          }
          body
          createdAt
          updatedAt
        }
      }
    }
  }
}
"""


_FILE_VIEW_STATES_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      files(first: 100, after: $after) {
        nodes {
          path
          viewerViewedState
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

_MARK_FILE_VIEWED_MUTATION = """
mutation($pullRequestId: ID!, $path: String!) {
  markFileAsViewed(input: {pullRequestId: $pullRequestId, path: $path}) {
    clientMutationId
  }
}
"""

_UNMARK_FILE_VIEWED_MUTATION = """
mutation($pullRequestId: ID!, $path: String!) {
  unmarkFileAsViewed(input: {pullRequestId: $pullRequestId, path: $path}) {
    clientMutationId
  }
}
"""


class GitHubService:
    """Interacts with GitHub API via gh CLI."""

    def __init__(self, owner: str | None = None, repo: str | None = None):
        self._owner = owner
        self._repo = repo
        self._detected_repo: GitHubRepo | None = None

    async def get_repo(self) -> GitHubRepo:
        if self._owner and self._repo:
            return GitHubRepo(owner=self._owner, name=self._repo)

        if self._detected_repo:
            return self._detected_repo

        result = await self._run_gh(["repo", "view", "--json", "owner,name"])
        data = json.loads(result)
        self._detected_repo = GitHubRepo(
            owner=data["owner"]["login"],
            name=data["name"],
        )
        return self._detected_repo

    async def get_pr_all(self, pr_number: int) -> PR:
        """Fetch all PR data in a single GraphQL request."""
        pr_data = await self._get_pull_request_data(pr_number, query=_PR_GRAPHQL_QUERY)
        return PR.model_validate(pr_data)

    async def get_pr_summary(self, pr_number: int) -> PR:
        """Fetch the summary needed for the header and sidebar."""
        pr_data = await self._get_pull_request_data(
            pr_number,
            query=_PR_SUMMARY_GRAPHQL_QUERY,
        )
        return PR.model_validate(pr_data)

    async def get_pr_discussion(self, pr_number: int) -> PRDiscussion:
        """Fetch the discussion body, reviews, threads, and issue comments."""
        pr_data = await self._get_pull_request_data(
            pr_number,
            query=_PR_DISCUSSION_GRAPHQL_QUERY,
        )
        pr = PR.model_validate(pr_data)
        return PRDiscussion(
            body=pr.body,
            reviews=pr.reviews,
            issue_comments=pr.issue_comments,
            review_threads=pr.review_threads,
        )

    async def get_pr_discussion_fast(self, pr_number: int) -> PRDiscussion:
        """Fetch comments and reviews quickly via REST for early timeline paint."""
        repo = await self.get_repo()
        pr_result, review_comments_result = await asyncio.gather(
            self._run_gh(
                [
                    "api",
                    "graphql",
                    "-f",
                    f"query={_PR_FAST_DISCUSSION_GRAPHQL_QUERY}",
                    "-F",
                    f"owner={repo.owner}",
                    "-F",
                    f"repo={repo.name}",
                    "-F",
                    f"number={pr_number}",
                ]
            ),
            self._run_gh(
                [
                    "api",
                    f"/repos/{repo.full_name}/pulls/{pr_number}/comments?per_page=100",
                ]
            ),
        )
        pr = PR.model_validate(
            self._parse_pull_request_graphql_result(pr_result, pr_number)
        )
        review_comments = _PRCommentListAdapter.validate_python(
            json.loads(review_comments_result)
        )
        return PRDiscussion(
            body=pr.body,
            reviews=pr.reviews,
            issue_comments=pr.issue_comments,
            review_threads=_review_threads_from_rest_comments(review_comments),
        )

    async def get_pr_diff_text(self, pr_number: int) -> str:
        """Fetch the full raw PR diff from GitHub's web diff endpoint."""
        repo = await self.get_repo()
        url = f"https://github.com/{repo.full_name}/pull/{pr_number}.diff"
        try:
            return await asyncio.to_thread(_fetch_url_text, url)
        except OSError as error:
            raise GitHubError(f"Failed to fetch raw PR diff: {error}") from error

    async def iter_pr_diff_sections(self, pr_number: int) -> AsyncIterator[str]:
        """Stream raw PR diff sections as each file patch arrives."""
        repo = await self.get_repo()
        url = f"https://github.com/{repo.full_name}/pull/{pr_number}.diff"
        items: queue.Queue[str | BaseException | None] = queue.Queue(maxsize=20)

        def read_sections() -> None:
            try:
                for section in _iter_url_diff_sections(url):
                    items.put(section)
            except BaseException as error:
                items.put(error)
            finally:
                items.put(None)

        thread = threading.Thread(
            target=read_sections,
            name="rit-pr-diff-stream",
            daemon=True,
        )
        thread.start()

        while True:
            item = await asyncio.to_thread(items.get)
            if item is None:
                break
            if isinstance(item, BaseException):
                if isinstance(item, OSError):
                    raise GitHubError(
                        f"Failed to fetch raw PR diff: {item}"
                    ) from item
                raise item
            yield item

    async def get_pr_files(
        self,
        pr_number: int,
        *,
        total_count: int | None = None,
    ) -> list[PRFile]:
        """Fetch PR files with a fast first page and concurrent remaining pages."""
        first_page = await self.get_pr_files_page(
            pr_number,
            page=1,
            per_page=PR_FILES_PER_PAGE,
        )
        if len(first_page) < PR_FILES_PER_PAGE:
            return first_page

        remaining_pages = _remaining_pr_file_pages(total_count)
        if not remaining_pages:
            remaining_pages = list(range(2, PR_FILES_MAX_REST_PAGES + 1))

        page_batches = await self.get_pr_file_pages(
            pr_number,
            pages=remaining_pages,
            per_page=PR_FILES_PER_PAGE,
        )
        files = list(first_page)
        for page in remaining_pages:
            batch = page_batches.get(page, [])
            if not batch:
                break
            files.extend(batch)
        return files

    async def get_pr_files_page(
        self,
        pr_number: int,
        *,
        page: int,
        per_page: int = 100,
    ) -> list[PRFile]:
        """Fetch one page of PR files via the REST API."""
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                f"/repos/{repo.full_name}/pulls/{pr_number}/files?per_page={per_page}&page={page}",
            ]
        )
        data = json.loads(result)
        if isinstance(data, list):
            return _PRFileListAdapter.validate_python(data)
        return _PRFileListAdapter.validate_python([data])

    async def get_pr_file_pages(
        self,
        pr_number: int,
        *,
        pages: Sequence[int],
        per_page: int = PR_FILES_PER_PAGE,
        concurrency: int = PR_FILES_PAGE_CONCURRENCY,
    ) -> dict[int, list[PRFile]]:
        """Fetch multiple PR file pages concurrently and return them by page number."""
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_page(page: int) -> tuple[int, list[PRFile]]:
            async with semaphore:
                return page, await self.get_pr_files_page(
                    pr_number,
                    page=page,
                    per_page=per_page,
                )

        results = await asyncio.gather(*(fetch_page(page) for page in pages))
        return dict(results)

    def _parse_paginated_items(self, result: str) -> list[object]:
        items: list[object] = []
        decoder = json.JSONDecoder()
        index = 0

        while index < len(result):
            while index < len(result) and result[index].isspace():
                index += 1
            if index >= len(result):
                break

            data, index = decoder.raw_decode(result, index)
            if isinstance(data, list):
                items.extend(data)
            else:
                items.append(data)

        return items

    async def get_reviewer_candidates(self) -> tuple[list[PRUser], list[PRTeam]]:
        """Fetch user and team candidates for PR review requests."""
        return await asyncio.gather(
            self.get_reviewer_user_candidates(),
            self.get_reviewer_team_candidates(),
        )

    async def get_reviewer_user_candidates(self) -> list[PRUser]:
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                f"/repos/{repo.full_name}/collaborators?affiliation=all&per_page=100",
                "--paginate",
            ]
        )
        return _PRUserListAdapter.validate_python(self._parse_paginated_items(result))

    async def get_reviewer_team_candidates(self) -> list[PRTeam]:
        repo = await self.get_repo()
        try:
            result = await self._run_gh(
                [
                    "api",
                    f"/repos/{repo.full_name}/teams?per_page=100",
                    "--paginate",
                ]
            )
        except GitHubError as error:
            if "HTTP 404" in str(error):
                return []
            raise
        return _PRTeamListAdapter.validate_python(self._parse_paginated_items(result))

    async def get_assignee_candidates(self) -> list[PRUser]:
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                f"/repos/{repo.full_name}/assignees?per_page=100",
                "--paginate",
            ]
        )
        return _PRUserListAdapter.validate_python(self._parse_paginated_items(result))

    async def _get_pull_request_data(
        self, pr_number: int, *, query: str
    ) -> dict[str, object]:
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-F",
                f"owner={repo.owner}",
                "-F",
                f"repo={repo.name}",
                "-F",
                f"number={pr_number}",
            ]
        )

        data = json.loads(result)

        return self._parse_pull_request_graphql_data(data, pr_number)

    def _parse_pull_request_graphql_result(
        self, result: str, pr_number: int
    ) -> dict[str, object]:
        data = json.loads(result)
        return self._parse_pull_request_graphql_data(data, pr_number)

    def _parse_pull_request_graphql_data(
        self, data: object, pr_number: int
    ) -> dict[str, object]:
        if not isinstance(data, dict):
            raise GitHubError(f"PR #{pr_number} not found")

        graphql_data = cast(dict[str, Any], data)
        errors = graphql_data.get("errors")
        if errors:
            raise GitHubError(f"GraphQL error: {errors}")

        data_node = graphql_data.get("data")
        if not isinstance(data_node, dict):
            raise GitHubError(f"PR #{pr_number} not found")

        repository = data_node.get("repository")
        if not isinstance(repository, dict):
            raise GitHubError(f"PR #{pr_number} not found")

        pr_data = repository.get("pullRequest")
        if not isinstance(pr_data, dict):
            raise GitHubError(f"PR #{pr_number} not found")

        return cast(dict[str, object], pr_data)

    async def request_reviewers(
        self,
        pr_number: int,
        *,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> None:
        """Request user and team reviews on a PR."""
        payload = self._reviewer_payload(reviewers, team_reviewers)
        if not payload:
            return
        repo = await self.get_repo()
        await self._run_gh(
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo.full_name}/pulls/{pr_number}/requested_reviewers",
                "--input",
                "-",
            ],
            input_text=json.dumps(payload),
        )

    async def remove_requested_reviewers(
        self,
        pr_number: int,
        *,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> None:
        """Remove requested user and team reviewers from a PR."""
        payload = self._reviewer_payload(reviewers, team_reviewers)
        if not payload:
            return
        repo = await self.get_repo()
        await self._run_gh(
            [
                "api",
                "--method",
                "DELETE",
                f"/repos/{repo.full_name}/pulls/{pr_number}/requested_reviewers",
                "--input",
                "-",
            ],
            input_text=json.dumps(payload),
        )

    def _reviewer_payload(
        self,
        reviewers: list[str] | None,
        team_reviewers: list[str] | None,
    ) -> dict[str, list[str]]:
        payload: dict[str, list[str]] = {}
        if reviewers:
            payload["reviewers"] = reviewers
        if team_reviewers:
            payload["team_reviewers"] = team_reviewers
        return payload

    async def add_assignees(self, pr_number: int, assignees: list[str]) -> None:
        """Assign users to the PR issue."""
        if not assignees:
            return
        repo = await self.get_repo()
        await self._run_gh(
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo.full_name}/issues/{pr_number}/assignees",
                "--input",
                "-",
            ],
            input_text=json.dumps({"assignees": assignees}),
        )

    async def remove_assignees(self, pr_number: int, assignees: list[str]) -> None:
        """Remove assignees from the PR issue."""
        if not assignees:
            return
        repo = await self.get_repo()
        await self._run_gh(
            [
                "api",
                "--method",
                "DELETE",
                f"/repos/{repo.full_name}/issues/{pr_number}/assignees",
                "--input",
                "-",
            ],
            input_text=json.dumps({"assignees": assignees}),
        )

    async def create_issue_comment(
        self,
        pr_number: int,
        body: str,
    ) -> PRIssueComment:
        """Create a PR-level issue comment via the REST API."""
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo.full_name}/issues/{pr_number}/comments",
                "-f",
                f"body={body}",
            ]
        )
        return PRIssueComment.model_validate(json.loads(result))

    async def create_review_comment(
        self,
        pr_number: int,
        *,
        body: str,
        commit_id: str,
        path: str,
        line: int,
        side: str,
    ) -> PRComment:
        """Create an inline review comment via the REST API."""
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo.full_name}/pulls/{pr_number}/comments",
                "-f",
                f"body={body}",
                "-f",
                f"commit_id={commit_id}",
                "-f",
                f"path={path}",
                "-F",
                f"line={line}",
                "-f",
                f"side={side}",
            ]
        )
        return PRComment.model_validate(json.loads(result))

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments: list[PendingReviewComment],
        body: str | None = None,
        commit_id: str | None = None,
    ) -> PRReview:
        repo = await self.get_repo()
        payload: dict[str, object] = {
            "comments": [
                {
                    "path": comment.path,
                    "line": comment.line,
                    "side": comment.side,
                    "body": comment.body,
                }
                for comment in comments
            ]
        }
        if body is not None and body != "":
            payload["body"] = body
        if commit_id is not None and commit_id != "":
            payload["commit_id"] = commit_id
        result = await self._run_gh(
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo.full_name}/pulls/{pr_number}/reviews",
                "--input",
                "-",
            ],
            input_text=json.dumps(payload),
        )
        return PRReview.model_validate(json.loads(result))

    async def list_review_comments(
        self,
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                f"/repos/{repo.full_name}/pulls/{pr_number}/reviews/{review_id}/comments",
                "--paginate",
            ]
        )

        comments: list[PRComment] = []
        for line in result.strip().split("\n"):
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                comments.extend(PRComment.model_validate(item) for item in data)
            else:
                comments.append(PRComment.model_validate(data))
        return comments

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None:
        repo = await self.get_repo()
        await self._run_gh(
            [
                "api",
                "--method",
                "DELETE",
                f"/repos/{repo.full_name}/pulls/{pr_number}/reviews/{review_id}",
            ]
        )

    async def submit_pending_review(
        self,
        pr_number: int,
        review_id: int,
        *,
        event: str,
        body: str | None = None,
    ) -> PRReview:
        repo = await self.get_repo()
        payload: dict[str, object] = {"event": event}
        if body is not None and body != "":
            payload["body"] = body
        result = await self._run_gh(
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo.full_name}/pulls/{pr_number}/reviews/{review_id}/events",
                "--input",
                "-",
            ],
            input_text=json.dumps(payload),
        )
        return PRReview.model_validate(json.loads(result))

    async def submit_review(
        self,
        pr_number: int,
        *,
        event: str,
        body: str | None = None,
        comments: list[PendingReviewComment] | None = None,
    ) -> PRReview:
        """Submit a top-level review via the REST API."""
        repo = await self.get_repo()
        payload: dict[str, object] = {"event": event}
        if body is not None and body != "":
            payload["body"] = body
        if comments:
            payload["comments"] = [
                {
                    "path": comment.path,
                    "line": comment.line,
                    "side": comment.side,
                    "body": comment.body,
                }
                for comment in comments
            ]
        result = await self._run_gh(
            [
                "api",
                "--method",
                "POST",
                f"/repos/{repo.full_name}/pulls/{pr_number}/reviews",
                "--input",
                "-",
            ],
            input_text=json.dumps(payload),
        )
        return PRReview.model_validate(json.loads(result))

    async def _set_thread_resolved(self, thread_id: str, resolve: bool) -> bool:
        mutation_name = "resolveReviewThread" if resolve else "unresolveReviewThread"
        mutation = f"""
        mutation($threadId: ID!) {{
          {mutation_name}(input: {{threadId: $threadId}}) {{
            thread {{
              isResolved
            }}
          }}
        }}
        """

        result = await self._run_gh(
            [
                "api",
                "graphql",
                "-f",
                f"query={mutation}",
                "-F",
                f"threadId={thread_id}",
            ]
        )

        data = json.loads(result)
        if "errors" in data:
            action = "resolve" if resolve else "unresolve"
            raise GitHubError(f"Failed to {action} thread: {data['errors']}")

        is_resolved = (
            data.get("data", {})
            .get(mutation_name, {})
            .get("thread", {})
            .get("isResolved", not resolve)
        )

        return is_resolved == resolve

    async def get_file_content(self, path: str, ref: str) -> str:
        """Fetch raw file content at a given ref (branch/sha)."""
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                f"/repos/{repo.full_name}/contents/{path}?ref={ref}",
                "-H",
                "Accept: application/vnd.github.raw+json",
            ]
        )
        return result

    async def resolve_thread(self, thread_id: str) -> bool:
        return await self._set_thread_resolved(thread_id, resolve=True)

    async def unresolve_thread(self, thread_id: str) -> bool:
        return await self._set_thread_resolved(thread_id, resolve=False)

    async def get_pr_file_view_states(self, pr_number: int) -> dict[str, str]:
        """Fetch viewerViewedState for all files via paginated GraphQL."""
        repo = await self.get_repo()
        result: dict[str, str] = {}
        cursor: str | None = None

        while True:
            args = [
                "api",
                "graphql",
                "-f",
                f"query={_FILE_VIEW_STATES_QUERY}",
                "-F",
                f"owner={repo.owner}",
                "-F",
                f"repo={repo.name}",
                "-F",
                f"number={pr_number}",
            ]
            if cursor:
                args.extend(["-f", f"after={cursor}"])

            output = await self._run_gh(args)
            data = json.loads(output)

            if "errors" in data:
                raise GitHubError(f"GraphQL error: {data['errors']}")

            pr_data = data.get("data", {}).get("repository", {}).get("pullRequest")
            if not pr_data:
                break

            files_data = pr_data.get("files", {})
            for node in files_data.get("nodes", []):
                result[node["path"]] = node["viewerViewedState"]

            page_info = files_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return result

    async def mark_file_as_viewed(self, pull_request_id: str, path: str) -> None:
        """Mark a file as viewed via GraphQL mutation."""
        result = await self._run_gh(
            [
                "api",
                "graphql",
                "-f",
                f"query={_MARK_FILE_VIEWED_MUTATION}",
                "-F",
                f"pullRequestId={pull_request_id}",
                "-f",
                f"path={path}",
            ]
        )
        data = json.loads(result)
        if "errors" in data:
            raise GitHubError(f"Failed to mark file as viewed: {data['errors']}")

    async def unmark_file_as_viewed(self, pull_request_id: str, path: str) -> None:
        """Unmark a file as viewed via GraphQL mutation."""
        result = await self._run_gh(
            [
                "api",
                "graphql",
                "-f",
                f"query={_UNMARK_FILE_VIEWED_MUTATION}",
                "-F",
                f"pullRequestId={pull_request_id}",
                "-f",
                f"path={path}",
            ]
        )
        data = json.loads(result)
        if "errors" in data:
            raise GitHubError(f"Failed to unmark file as viewed: {data['errors']}")

    async def _run_gh(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> str:
        cmd = ["gh", *args]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate(
            input_text.encode() if input_text is not None else None
        )

        if proc.returncode != 0:
            error_msg = (
                stderr.decode().strip() or f"gh command failed: {' '.join(args)}"
            )
            raise GitHubError(error_msg)

        return stdout.decode()

    def run_gh_sync(self, args: list[str]) -> str:
        """Synchronous variant of _run_gh for non-async contexts."""
        cmd = ["gh", *args]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.strip() or f"gh command failed: {' '.join(args)}"
            raise GitHubError(error_msg) from e
        except FileNotFoundError:
            raise GitHubError(
                "gh CLI not found. Please install GitHub CLI: https://cli.github.com/"
            )
