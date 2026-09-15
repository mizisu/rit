import pytest

from rit.services.pr_graphql_queries import (
    PullRequestGraphQLView,
    pull_request_graphql_request,
    pull_request_query,
)


def test_pull_request_summary_query_fetches_body_for_early_description() -> None:
    assert "\n      body\n" in pull_request_query(PullRequestGraphQLView.SUMMARY)


def test_pull_request_graphql_request_builds_gh_args_for_named_view() -> None:
    request = pull_request_graphql_request(
        view=PullRequestGraphQLView.DISCUSSION,
        owner="owner",
        repo="repo",
        pr_number=123,
    )

    assert request[:4] == ("api", "graphql", "-f", request[3])
    assert request[3].startswith("query=")
    assert "reviewThreads(first: 100)" in request[3]
    assert request[-6:] == (
        "-F",
        "owner=owner",
        "-F",
        "repo=repo",
        "-F",
        "number=123",
    )


@pytest.mark.parametrize(
    "view",
    [
        PullRequestGraphQLView.DISCUSSION,
        PullRequestGraphQLView.FAST_DISCUSSION,
        PullRequestGraphQLView.ALL,
    ],
)
def test_discussion_queries_fetch_review_threads_with_publication_times(
    view: PullRequestGraphQLView,
) -> None:
    query = pull_request_query(view)

    assert "reviewThreads(first: 100)" in query
    assert "startLine" in query
    assert "startDiffSide" in query
    assert "publishedAt" in query
    assert "/pulls/" not in query


def test_pull_request_node_id_view_requests_only_identity_data() -> None:
    query = pull_request_query(PullRequestGraphQLView.NODE_ID)

    assert "\n      id\n" in query
    assert "reviews(first:" not in query
    assert "reviewThreads(first:" not in query
