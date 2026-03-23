import asyncio
import json
import subprocess
from dataclasses import dataclass

from pydantic import TypeAdapter

from rit.state.models import (
    PR,
    PRFile,
)

_PRFileListAdapter: TypeAdapter[list[PRFile]] = TypeAdapter(list[PRFile])


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


class GitHubError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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
        repo = await self.get_repo()

        result = await self._run_gh(
            [
                "api",
                "graphql",
                "-f",
                f"query={_PR_GRAPHQL_QUERY}",
                "-F",
                f"owner={repo.owner}",
                "-F",
                f"repo={repo.name}",
                "-F",
                f"number={pr_number}",
            ]
        )

        data = json.loads(result)

        if "errors" in data:
            raise GitHubError(f"GraphQL error: {data['errors']}")

        pr_data = data.get("data", {}).get("repository", {}).get("pullRequest")
        if not pr_data:
            raise GitHubError(f"PR #{pr_number} not found")

        return PR.model_validate(pr_data)

    async def get_pr_files(self, pr_number: int) -> list[PRFile]:
        """Fetch files via REST API (GraphQL lacks patch data)."""
        repo = await self.get_repo()
        result = await self._run_gh(
            [
                "api",
                f"/repos/{repo.full_name}/pulls/{pr_number}/files",
                "--paginate",
            ]
        )

        files: list[PRFile] = []
        for line in result.strip().split("\n"):
            if line:
                try:
                    data = json.loads(line)
                    if isinstance(data, list):
                        files.extend(_PRFileListAdapter.validate_python(data))
                    else:
                        files.append(_PRFileListAdapter.validate_python([data])[0])
                except json.JSONDecodeError:
                    continue

        return files

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

    async def _run_gh(self, args: list[str]) -> str:
        cmd = ["gh", *args]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

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
