from __future__ import annotations

import asyncio
import json

from pydantic import TypeAdapter

from rit.services.gh_request import GitHubInputRunner, run_request
from rit.services.pr_graphql_queries import (
    PullRequestGraphQLView,
    pull_request_graphql_request,
)
from rit.services.pr_graphql_response import (
    fetch_pull_request_graphql_pr,
    parse_pull_request_graphql_result,
)
from rit.services.pr_review_comment_threads import review_threads_from_rest_comments
from rit.services.pr_review_request import pull_request_review_comments_request
from rit.state.discussion_projection import PRDiscussion
from rit.state.models import PR, PRComment

__all__ = (
    "PRDiscussion",
    "discussion_from_pr",
    "fast_discussion_from_data",
    "fast_discussion_from_result",
    "fast_discussion_from_results",
    "fetch_pr_discussion",
    "fetch_pr_discussion_fast",
)


_PRCommentListAdapter: TypeAdapter[list[PRComment]] = TypeAdapter(list[PRComment])


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


def fast_discussion_from_data(
    pr_data: object,
    rest_review_comments_data: object,
) -> PRDiscussion:
    """Project fast GraphQL PR data and REST review comments into discussion data."""
    pr = PR.model_validate(pr_data)
    if rest_review_comments_data == []:
        review_comments = []
    elif (
        isinstance(rest_review_comments_data, list)
        and len(rest_review_comments_data) == 1
    ):
        review_comments = [PRComment.model_validate(rest_review_comments_data[0])]
    else:
        review_comments = _PRCommentListAdapter.validate_python(
            rest_review_comments_data
        )
    return PRDiscussion(
        body=pr.body,
        reviews=pr.reviews,
        issue_comments=pr.issue_comments,
        review_threads=review_threads_from_rest_comments(review_comments),
    )


def fast_discussion_from_result(
    pr_data: object,
    rest_review_comments_result: str,
) -> PRDiscussion:
    """Project fast PR data and a REST comments JSON result into discussion data."""
    return fast_discussion_from_data(
        pr_data,
        (
            []
            if rest_review_comments_result == "[]"
            else json.loads(rest_review_comments_result)
        ),
    )


def fast_discussion_from_results(
    pr_result: str,
    rest_review_comments_result: str,
    *,
    pr_number: int,
) -> PRDiscussion:
    """Project raw fast GraphQL and REST comment results into discussion data."""
    return fast_discussion_from_result(
        parse_pull_request_graphql_result(pr_result, pr_number=pr_number),
        rest_review_comments_result,
    )


async def fetch_pr_discussion_fast(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    runner: GitHubInputRunner,
) -> PRDiscussion:
    """Fetch fast PR discussion data via GraphQL and REST."""
    repo_full_name = f"{owner}/{repo}"
    pr_result, review_comments_result = await asyncio.gather(
        run_request(
            pull_request_graphql_request(
                view=PullRequestGraphQLView.FAST_DISCUSSION,
                owner=owner,
                repo=repo,
                pr_number=pr_number,
            ),
            runner,
        ),
        run_request(
            pull_request_review_comments_request(repo_full_name, pr_number),
            runner,
        ),
    )
    return fast_discussion_from_results(
        pr_result,
        review_comments_result,
        pr_number=pr_number,
    )
