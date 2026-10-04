import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from rit.services.gh_cli import GhCliError, run_gh, run_gh_sync
from rit.services.github_repo import (
    GitHubRepo,
    fetch_repo_view,
)
from rit.services.graphql_mutations import (
    ThreadResolutionMutationError,
)
from rit.services.graphql_mutations import (
    resolve_thread as resolve_thread_via_graphql,
)
from rit.services.graphql_mutations import (
    unresolve_thread as unresolve_thread_via_graphql,
)
from rit.services.local_git import LocalGitSource
from rit.services.pr_checks import fetch_pr_checks
from rit.services.pr_discussion import (
    PRDiscussion,
    fetch_pr_discussion,
    fetch_pr_discussion_fast,
)
from rit.services.pr_file_comment_request import (
    create_file_comment as create_file_comment_via_rest,
)
from rit.services.pr_file_request import (
    fetch_file_content,
    fetch_pr_file_metadata,
    fetch_pr_files,
)
from rit.services.pr_file_view_states import (
    FileViewMutationError,
    FileViewStatesGraphQLError,
    fetch_file_view_states,
)
from rit.services.pr_file_view_states import (
    mark_file_as_viewed as mark_file_as_viewed_via_graphql,
)
from rit.services.pr_file_view_states import (
    unmark_file_as_viewed as unmark_file_as_viewed_via_graphql,
)
from rit.services.pr_graphql_response import (
    PullRequestNotFound,
    fetch_pull_request_all,
    fetch_pull_request_summary,
)
from rit.services.pr_issue_comment_request import (
    create_issue_comment as create_issue_comment_via_graphql,
)
from rit.services.pr_review_comment_request import (
    create_review_comment as create_review_comment_via_rest,
)
from rit.services.pr_review_comment_request import (
    delete_review_comment as delete_review_comment_via_graphql,
)
from rit.services.pr_review_comment_request import (
    update_review_comment as update_review_comment_via_graphql,
)
from rit.services.pr_review_graphql import (
    create_pending_review as create_pending_review_via_graphql,
)
from rit.services.pr_review_graphql import (
    delete_pending_review as delete_pending_review_via_graphql,
)
from rit.services.pr_review_graphql import (
    list_review_comments as list_review_comments_via_graphql,
)
from rit.services.pr_review_graphql import (
    submit_pending_review as submit_pending_review_via_graphql,
)
from rit.services.pr_review_graphql import (
    submit_review as submit_review_via_graphql,
)
from rit.services.pr_review_graphql import (
    update_pending_review as update_pending_review_via_graphql,
)
from rit.services.pr_reviewer_request import (
    add_assignees as add_assignees_via_graphql,
)
from rit.services.pr_reviewer_request import (
    fetch_assignee_candidates,
    fetch_reviewer_candidates,
    fetch_reviewer_team_candidates,
    fetch_reviewer_user_candidates,
)
from rit.services.pr_reviewer_request import (
    remove_assignees as remove_assignees_via_graphql,
)
from rit.services.pr_reviewer_request import (
    remove_requested_reviewers as remove_requested_reviewers_via_graphql,
)
from rit.services.pr_reviewer_request import (
    request_reviewers as request_reviewers_via_graphql,
)
from rit.state.models import (
    PR,
    PendingReviewComment,
    PRComment,
    PRFile,
    PRIssueComment,
    PRReview,
    PRTeam,
    PRUser,
)
from rit.state.pr_overview import PRChecksSnapshot, PRFileMetadata

__all__ = (
    "GitHubError",
    "GitHubRepo",
    "GitHubService",
    "PRDiscussion",
    "translate_pull_request_graphql_errors",
)


class GitHubError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@contextmanager
def translate_pull_request_graphql_errors() -> Iterator[None]:
    """Translate PR GraphQL parser errors into GitHub service errors."""
    try:
        yield
    except PullRequestNotFound as error:
        raise GitHubError(str(error)) from error
    except ValueError as error:
        raise GitHubError(f"GraphQL error: {error}") from error


class GitHubService:
    """Interacts with GitHub API via gh CLI."""

    def __init__(self, owner: str | None = None, repo: str | None = None):
        self._owner = owner
        self._repo = repo
        self._detected_repo: GitHubRepo | None = None
        self._repo_lock = asyncio.Lock()
        self._local_source = LocalGitSource(Path.cwd())
        self._file_metadata_lock = asyncio.Lock()
        self._file_metadata: tuple[int, PRFileMetadata] | None = None
        self._pr_revision: tuple[int, str, str] | None = None

    async def get_repo(self) -> GitHubRepo:
        if self._owner and self._repo:
            return GitHubRepo(owner=self._owner, name=self._repo)

        if self._detected_repo:
            return self._detected_repo

        async with self._repo_lock:
            if self._detected_repo is None:
                self._detected_repo = await fetch_repo_view(self._run_gh)
            return self._detected_repo

    async def get_pr_all(self, pr_number: int) -> PR:
        """Fetch PR data via GraphQL, including all activity pages."""
        repo = await self.get_repo()
        with translate_pull_request_graphql_errors():
            pr = await fetch_pull_request_all(
                owner=repo.owner,
                repo=repo.name,
                pr_number=pr_number,
                runner=self._run_gh,
            )
            self._pr_revision = (pr_number, pr.base_sha, pr.head_sha)
            return pr

    async def get_pr_summary(self, pr_number: int) -> PR:
        """Fetch the summary needed for the header and sidebar."""
        repo = await self.get_repo()
        with translate_pull_request_graphql_errors():
            pr = await fetch_pull_request_summary(
                owner=repo.owner,
                repo=repo.name,
                pr_number=pr_number,
                runner=self._run_gh,
            )
            self._pr_revision = (pr_number, pr.base_sha, pr.head_sha)
            return pr

    async def get_pr_discussion(self, pr_number: int) -> PRDiscussion:
        """Fetch PR discussion and timeline activity."""
        repo = await self.get_repo()
        with translate_pull_request_graphql_errors():
            return await fetch_pr_discussion(
                owner=repo.owner,
                repo=repo.name,
                pr_number=pr_number,
                runner=self._run_gh,
            )

    async def get_pr_discussion_fast(self, pr_number: int) -> PRDiscussion:
        """Fetch discussion data quickly via GraphQL for early timeline paint."""
        repo = await self.get_repo()
        with translate_pull_request_graphql_errors():
            return await fetch_pr_discussion_fast(
                owner=repo.owner,
                repo=repo.name,
                pr_number=pr_number,
                runner=self._run_gh,
            )

    async def get_pr_checks(self, head_sha: str) -> PRChecksSnapshot:
        """Fetch live check results for the given PR head."""
        repo = await self.get_repo()
        with translate_pull_request_graphql_errors():
            return await fetch_pr_checks(
                repo.owner,
                repo.name,
                head_sha,
                runner=self._run_gh,
            )

    async def get_pr_file_metadata(
        self,
        pr_number: int,
        *,
        validate: bool = False,
        refresh: bool = False,
        expected_refs: tuple[str, str] | None = None,
    ) -> PRFileMetadata:
        """Share metadata requests, validating refs before reuse for patches."""
        repo = await self.get_repo()
        with translate_pull_request_graphql_errors():
            async with self._file_metadata_lock:
                cached = self._file_metadata
                snapshot = cached[1] if cached is not None and cached[0] == pr_number and not refresh else None
                if snapshot is not None and validate:
                    current = await fetch_pr_file_metadata(
                        repo.owner,
                        repo.name,
                        pr_number,
                        total_count=1,
                        per_page=1,
                        runner=self._run_gh,
                    )
                    if (snapshot.base_sha, snapshot.head_sha) != (
                        current.base_sha, current.head_sha,
                    ):
                        snapshot = None
                if snapshot is None:
                    snapshot = await fetch_pr_file_metadata(
                        repo.owner, repo.name, pr_number, runner=self._run_gh,
                    )
                    self._file_metadata = (pr_number, snapshot)
                if expected_refs is not None and (snapshot.base_sha, snapshot.head_sha) != expected_refs:
                    raise GitHubError("PR changed while loading files; refresh and try again")
                return snapshot

    async def get_pr_files(
        self,
        pr_number: int,
        *,
        total_count: int | None = None,
    ) -> list[PRFile]:
        """Fetch metadata and canonical patches without downloading full files."""
        repo = await self.get_repo()
        revision = self._pr_revision
        refs = (revision[1], revision[2]) if revision is not None and revision[0] == pr_number and all(revision[1:]) else None
        try:
            return await fetch_pr_files(
                repo.owner,
                repo.name,
                pr_number,
                total_count=total_count,
                runner=self._run_gh,
                metadata=self.get_pr_file_metadata(pr_number, validate=True, expected_refs=refs),
            )
        except ValueError as error:
            raise GitHubError(f"GraphQL error: {error}") from error

    async def get_reviewer_candidates(self) -> tuple[list[PRUser], list[PRTeam]]:
        """Fetch user and team candidates for PR review requests."""
        repo = await self.get_repo()
        return await fetch_reviewer_candidates(repo.owner, repo.name, self._run_gh)

    async def get_reviewer_user_candidates(self) -> list[PRUser]:
        repo = await self.get_repo()
        return await fetch_reviewer_user_candidates(repo.owner, repo.name, self._run_gh)

    async def get_reviewer_team_candidates(self) -> list[PRTeam]:
        repo = await self.get_repo()
        return await fetch_reviewer_team_candidates(repo.owner, repo.name, self._run_gh)

    async def get_assignee_candidates(self) -> list[PRUser]:
        repo = await self.get_repo()
        return await fetch_assignee_candidates(repo.owner, repo.name, self._run_gh)

    async def request_reviewers(
        self,
        pr_number: int,
        *,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> None:
        """Request user and team reviews on a PR."""
        if not reviewers and not team_reviewers:
            return
        repo = await self.get_repo()
        await request_reviewers_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            reviewers=reviewers,
            team_reviewers=team_reviewers,
            runner=self._run_gh,
        )

    async def remove_requested_reviewers(
        self,
        pr_number: int,
        *,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> None:
        """Remove requested user and team reviewers from a PR."""
        if not reviewers and not team_reviewers:
            return
        repo = await self.get_repo()
        await remove_requested_reviewers_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            reviewers=reviewers,
            team_reviewers=team_reviewers,
            runner=self._run_gh,
        )

    async def add_assignees(self, pr_number: int, assignees: list[str]) -> None:
        """Assign users to the PR issue."""
        if not assignees:
            return
        repo = await self.get_repo()
        await add_assignees_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            assignees,
            runner=self._run_gh,
        )

    async def remove_assignees(self, pr_number: int, assignees: list[str]) -> None:
        """Remove assignees from the PR issue."""
        if not assignees:
            return
        repo = await self.get_repo()
        await remove_assignees_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            assignees,
            runner=self._run_gh,
        )

    async def create_issue_comment(
        self,
        pr_number: int,
        body: str,
    ) -> PRIssueComment:
        """Create a PR-level issue comment via GraphQL."""
        repo = await self.get_repo()
        return await create_issue_comment_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            body=body,
            runner=self._run_gh,
        )

    async def create_review_comment(
        self,
        pr_number: int,
        *,
        body: str,
        commit_id: str,
        path: str,
        line: int,
        side: str,
        start_line: int | None = None,
        start_side: str | None = None,
    ) -> PRComment:
        """Create a standalone inline review comment via REST."""
        repo = await self.get_repo()
        return await create_review_comment_via_rest(
            repo.full_name,
            pr_number,
            body=body,
            commit_id=commit_id,
            path=path,
            line=line,
            side=side,
            start_line=start_line,
            start_side=start_side,
            runner=self._run_gh,
        )

    async def update_review_comment(
        self,
        comment_node_id: str,
        body: str,
    ) -> PRComment:
        """Update an existing pull request review comment."""
        return await update_review_comment_via_graphql(
            comment_node_id,
            body=body,
            runner=self._run_gh,
        )

    async def delete_review_comment(self, comment_node_id: str) -> None:
        """Delete an existing pull request review comment."""
        await delete_review_comment_via_graphql(
            comment_node_id,
            runner=self._run_gh,
        )

    async def create_file_comment(
        self,
        pr_number: int,
        *,
        body: str,
        commit_id: str,
        path: str,
    ) -> PRComment:
        """Create a standalone file-level review comment via REST."""
        repo = await self.get_repo()
        return await create_file_comment_via_rest(
            repo.full_name,
            pr_number,
            body=body,
            commit_id=commit_id,
            path=path,
            runner=self._run_gh,
        )

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments: list[PendingReviewComment],
        body: str | None = None,
        commit_id: str | None = None,
    ) -> PRReview:
        repo = await self.get_repo()
        return await create_pending_review_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            comments=comments,
            body=body,
            commit_id=commit_id,
            runner=self._run_gh,
        )

    async def list_review_comments(
        self,
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        repo = await self.get_repo()
        return await list_review_comments_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            review_id=review_id,
            runner=self._run_gh,
        )

    async def update_pending_review(
        self, pr_number: int, review_id: int, *, body: str
    ) -> PRReview | None:
        """Update a pending summary, or return None when it needs recreation."""
        repo = await self.get_repo()
        return await update_pending_review_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            review_id=review_id,
            body=body,
            runner=self._run_gh,
        )

    async def delete_pending_review(
        self, pr_number: int, review_id: int, *, require_empty: bool = False
    ) -> None:
        repo = await self.get_repo()
        await delete_pending_review_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            review_id=review_id,
            runner=self._run_gh,
            require_empty=require_empty,
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
        return await submit_pending_review_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            review_id=review_id,
            event=event,
            body=body,
            runner=self._run_gh,
        )

    async def submit_review(
        self,
        pr_number: int,
        *,
        event: str,
        body: str | None = None,
        comments: list[PendingReviewComment] | None = None,
        commit_id: str | None = None,
    ) -> PRReview:
        """Submit a top-level review via GraphQL."""
        repo = await self.get_repo()
        return await submit_review_via_graphql(
            repo.owner,
            repo.name,
            pr_number,
            event=event,
            body=body,
            comments=comments,
            commit_id=commit_id,
            runner=self._run_gh,
        )

    async def get_file_content(self, path: str, ref: str) -> str:
        """Read immutable local objects first, falling back to GitHub text."""
        content = await self._local_source.read_file(path, ref)
        if content is not None:
            return content
        repo = await self.get_repo()
        try:
            return await fetch_file_content(
                repo.owner,
                repo.name,
                path,
                ref=ref,
                runner=self._run_gh,
            )
        except ValueError as error:
            raise GitHubError(f"GraphQL error: {error}") from error

    async def resolve_thread(self, thread_id: str) -> bool:
        try:
            return await resolve_thread_via_graphql(
                thread_id,
                runner=self._run_gh,
            )
        except ThreadResolutionMutationError as error:
            raise GitHubError(str(error)) from error

    async def unresolve_thread(self, thread_id: str) -> bool:
        try:
            return await unresolve_thread_via_graphql(
                thread_id,
                runner=self._run_gh,
            )
        except ThreadResolutionMutationError as error:
            raise GitHubError(str(error)) from error

    async def get_pr_file_view_states(self, pr_number: int) -> dict[str, str]:
        """Fetch viewerViewedState for all files via paginated GraphQL."""
        repo = await self.get_repo()
        try:
            return await fetch_file_view_states(
                owner=repo.owner,
                repo=repo.name,
                pr_number=pr_number,
                runner=self._run_gh,
            )
        except FileViewStatesGraphQLError as error:
            raise GitHubError(f"GraphQL error: {error}") from error

    async def mark_file_as_viewed(self, pull_request_id: str, path: str) -> None:
        """Mark a file as viewed via GraphQL mutation."""
        try:
            await mark_file_as_viewed_via_graphql(
                pull_request_id=pull_request_id,
                path=path,
                runner=self._run_gh,
            )
            self._file_metadata = None
        except FileViewMutationError as error:
            raise GitHubError(str(error)) from error

    async def unmark_file_as_viewed(self, pull_request_id: str, path: str) -> None:
        """Unmark a file as viewed via GraphQL mutation."""
        try:
            await unmark_file_as_viewed_via_graphql(
                pull_request_id=pull_request_id,
                path=path,
                runner=self._run_gh,
            )
            self._file_metadata = None
        except FileViewMutationError as error:
            raise GitHubError(str(error)) from error

    async def _run_gh(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> str:
        try:
            return await run_gh(args, input_text=input_text)
        except GhCliError as error:
            raise GitHubError(str(error)) from error

    def run_gh_sync(self, args: list[str]) -> str:
        """Synchronous variant of _run_gh for non-async contexts."""
        try:
            return run_gh_sync(args)
        except GhCliError as error:
            raise GitHubError(str(error)) from error
