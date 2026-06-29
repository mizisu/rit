from __future__ import annotations

from rit.services.gh_request import GitHubInputRunner, run_request
from rit.services.pr_graphql_queries import (
    PullRequestGraphQLView,
    pull_request_graphql_request,
)
from rit.services.pr_graphql_response import (
    fetch_pull_request_graphql_pr,
    parse_pull_request_graphql_result,
)
from rit.state.discussion_projection import PRDiscussion
from rit.state.models import PR

__all__ = (
    "PRDiscussion",
    "discussion_from_pr",
    "fast_discussion_from_data",
    "fast_discussion_from_result",
    "fetch_pr_discussion",
    "fetch_pr_discussion_fast",
)


def discussion_from_pr(pr: PR) -> PRDiscussion:
    """Project a full PR model into discussion timeline data."""
    return PRDiscussion(
        body=pr.body,
        reviews=pr.reviews,
        issue_comments=pr.issue_comments,
        review_threads=pr.review_threads,
    )


async def fetch_pr_discussion(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> PRDiscussion:
    """Fetch full PR discussion data via GraphQL."""
    return discussion_from_pr(
        await fetch_pull_request_graphql_pr(
            view=PullRequestGraphQLView.DISCUSSION,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            runner=runner,
        )
    )


def fast_discussion_from_data(pr_data: object) -> PRDiscussion:
    """Project fast GraphQL PR discussion data into a discussion model."""
    return discussion_from_pr(PR.model_validate(pr_data))


def fast_discussion_from_result(pr_result: str, *, pr_number: int) -> PRDiscussion:
    """Project a raw fast GraphQL PR result into discussion data."""
    return fast_discussion_from_data(
        parse_pull_request_graphql_result(pr_result, pr_number=pr_number)
    )


async def fetch_pr_discussion_fast(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> PRDiscussion:
    """Fetch fast PR discussion data via GraphQL only."""
    pr_result = await run_request(
        pull_request_graphql_request(
            view=PullRequestGraphQLView.FAST_DISCUSSION,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
        ),
        runner,
    )
    return fast_discussion_from_result(pr_result, pr_number=pr_number)
