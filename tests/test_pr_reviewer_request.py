import json

from rit.services.pr_reviewer_request import (
    add_assignees,
    fetch_assignee_candidates,
    fetch_reviewer_team_candidates,
    fetch_reviewer_user_candidates,
    remove_assignees,
    remove_requested_reviewers,
    request_reviewers,
)


def _connection(
    name: str,
    nodes: list[dict[str, object]],
    *,
    has_next_page: bool = False,
    end_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "data": {
            "repository": {
                name: {
                    "nodes": nodes,
                    "pageInfo": {
                        "hasNextPage": has_next_page,
                        "endCursor": end_cursor,
                    },
                }
            }
        }
    }


async def test_fetch_reviewer_user_candidates_paginates_graphql_collaborators() -> None:
    after_values: list[str | None] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        assert input_text is not None
        payload = json.loads(input_text)
        after = payload["variables"]["after"]
        after_values.append(after)
        if after is None:
            return json.dumps(
                _connection(
                    "collaborators",
                    [{"id": "U_1", "login": "alice"}],
                    has_next_page=True,
                    end_cursor="cursor-1",
                )
            )
        return json.dumps(
            _connection("collaborators", [{"id": "U_2", "login": "bob"}])
        )

    users = await fetch_reviewer_user_candidates("owner", "repo", runner)

    assert [(user.login, user.node_id) for user in users] == [
        ("alice", "U_1"),
        ("bob", "U_2"),
    ]
    assert after_values == [None, "cursor-1"]


async def test_fetch_assignee_candidates_uses_assignable_users() -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        assert "assignableUsers" in payload["query"]
        return json.dumps(
            _connection("assignableUsers", [{"id": "U_1", "login": "alice"}])
        )

    users = await fetch_assignee_candidates("owner", "repo", runner)

    assert [(user.login, user.node_id) for user in users] == [("alice", "U_1")]


async def test_fetch_reviewer_team_candidates_filters_repository_access() -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "owner": {
                            "teams": {
                                "nodes": [
                                    {
                                        "name": "Backend",
                                        "slug": "backend",
                                        "repositories": {
                                            "nodes": [{"nameWithOwner": "owner/repo"}]
                                        },
                                    },
                                    {
                                        "name": "Other",
                                        "slug": "other",
                                        "repositories": {
                                            "nodes": [{"nameWithOwner": "owner/other"}]
                                        },
                                    },
                                ],
                                "pageInfo": {
                                    "hasNextPage": False,
                                    "endCursor": None,
                                },
                            }
                        }
                    }
                }
            }
        )

    teams = await fetch_reviewer_team_candidates("owner", "repo", runner)

    assert [(team.name, team.slug) for team in teams] == [("Backend", "backend")]


async def test_request_reviewers_uses_graphql_login_mutation() -> None:
    calls: list[dict[str, object]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if "requestReviewsByLogin" not in payload["query"]:
            return json.dumps(
                {"data": {"repository": {"pullRequest": {"id": "PR_node"}}}}
            )
        return json.dumps(
            {"data": {"requestReviewsByLogin": {"pullRequest": {"id": "PR_node"}}}}
        )

    await request_reviewers(
        "owner",
        "repo",
        123,
        reviewers=["alice"],
        team_reviewers=["backend"],
        runner=runner,
    )

    assert calls[1]["variables"]["input"] == {
        "pullRequestId": "PR_node",
        "userLogins": ["alice"],
        "teamSlugs": ["backend"],
        "union": True,
    }


async def test_remove_requested_reviewers_replaces_remaining_graphql_actors() -> None:
    calls: list[dict[str, object]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if "reviewRequests(first" in payload["query"]:
            return json.dumps(
                {
                    "data": {
                        "repository": {
                            "pullRequest": {
                                "id": "PR_node",
                                "reviewRequests": {
                                    "nodes": [
                                        {
                                            "requestedReviewer": {
                                                "__typename": "User",
                                                "id": "U_alice",
                                                "login": "alice",
                                            }
                                        },
                                        {
                                            "requestedReviewer": {
                                                "__typename": "User",
                                                "id": "U_bob",
                                                "login": "bob",
                                            }
                                        },
                                        {
                                            "requestedReviewer": {
                                                "__typename": "Team",
                                                "id": "T_backend",
                                                "slug": "backend",
                                            }
                                        },
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": False,
                                        "endCursor": None,
                                    },
                                },
                            }
                        }
                    }
                }
            )
        return json.dumps(
            {"data": {"requestReviews": {"pullRequest": {"id": "PR_node"}}}}
        )

    await remove_requested_reviewers(
        "owner",
        "repo",
        123,
        reviewers=["alice"],
        team_reviewers=["backend"],
        runner=runner,
    )

    assert calls[1]["variables"]["input"] == {
        "pullRequestId": "PR_node",
        "userIds": ["U_bob"],
        "botIds": [],
        "teamIds": [],
        "union": False,
    }


async def test_add_assignees_resolves_graphql_user_ids() -> None:
    mutation_inputs: list[dict[str, object]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        query = payload["query"]
        if "assignableUsers" in query:
            return json.dumps(
                _connection("assignableUsers", [{"id": "U_alice", "login": "alice"}])
            )
        if "addAssigneesToAssignable" in query:
            mutation_inputs.append(payload["variables"]["input"])
            return json.dumps(
                {"data": {"addAssigneesToAssignable": {"assignable": {"__typename": "PullRequest"}}}}
            )
        return json.dumps(
            {"data": {"repository": {"pullRequest": {"id": "PR_node"}}}}
        )

    await add_assignees("owner", "repo", 123, ["alice"], runner=runner)

    assert mutation_inputs == [
        {"assignableId": "PR_node", "assigneeIds": ["U_alice"]}
    ]


async def test_remove_assignees_uses_current_graphql_actor_ids() -> None:
    calls: list[dict[str, object]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        calls.append(payload)
        if "removeAssigneesFromAssignable" in payload["query"]:
            return json.dumps(
                {"data": {"removeAssigneesFromAssignable": {"assignable": {"__typename": "PullRequest"}}}}
            )
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "id": "PR_node",
                            "assignedActors": {
                                "nodes": [
                                    {
                                        "__typename": "User",
                                        "id": "U_alice",
                                        "login": "alice",
                                    }
                                ]
                            },
                        }
                    }
                }
            }
        )

    await remove_assignees("owner", "repo", 123, ["alice"], runner=runner)

    assert calls[1]["variables"]["input"] == {
        "assignableId": "PR_node",
        "assigneeIds": ["U_alice"],
    }
