import json

import pytest

import rit.services.pr_reviewer_request as pr_reviewer_request
from rit.services.pr_reviewer_request import (
    add_assignees,
    assignee_payload,
    assignee_request,
    assignee_change_request,
    assignee_candidates_request,
    fetch_assignee_candidates,
    fetch_reviewer_candidates,
    fetch_reviewer_team_candidates,
    fetch_reviewer_user_candidates,
    parse_reviewer_team_candidates,
    parse_reviewer_user_candidates,
    remove_assignees,
    remove_requested_reviewers,
    reviewer_change_request,
    reviewer_team_candidates_request,
    reviewer_payload,
    reviewer_request,
    reviewer_user_candidates_request,
    request_reviewers,
    run_participant_change,
    should_treat_team_candidates_error_as_empty,
)


def test_reviewer_payload_includes_users_and_teams() -> None:
    payload = reviewer_payload(
        reviewers=["alice", "bob"],
        team_reviewers=["backend"],
    )

    assert payload == {
        "reviewers": ["alice", "bob"],
        "team_reviewers": ["backend"],
    }


def test_reviewer_payload_omits_empty_groups() -> None:
    assert reviewer_payload(reviewers=[], team_reviewers=["backend"]) == {
        "team_reviewers": ["backend"]
    }
    assert reviewer_payload(reviewers=["alice"], team_reviewers=[]) == {
        "reviewers": ["alice"]
    }


def test_reviewer_payload_returns_empty_for_empty_input() -> None:
    assert reviewer_payload(reviewers=None, team_reviewers=None) == {}
    assert reviewer_payload(reviewers=[], team_reviewers=[]) == {}


def test_parse_reviewer_user_candidates_empty_output_skips_model_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Adapter:
        def validate_python(self, _data: object) -> list[object]:
            raise AssertionError("empty reviewer user candidates should not validate")

    monkeypatch.setattr(pr_reviewer_request, "_PRUserListAdapter", Adapter())

    assert parse_reviewer_user_candidates("") == []


def test_parse_reviewer_user_candidates_single_item_skips_list_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Adapter:
        def validate_python(self, _data: object) -> list[object]:
            raise AssertionError("single reviewer user candidate should not validate list")

    monkeypatch.setattr(pr_reviewer_request, "_PRUserListAdapter", Adapter())

    users = parse_reviewer_user_candidates('[{"login": "alice"}]')

    assert len(users) == 1
    assert users[0].login == "alice"


def test_parse_reviewer_team_candidates_empty_output_skips_model_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Adapter:
        def validate_python(self, _data: object) -> list[object]:
            raise AssertionError("empty reviewer team candidates should not validate")

    monkeypatch.setattr(pr_reviewer_request, "_PRTeamListAdapter", Adapter())

    assert parse_reviewer_team_candidates("") == []


def test_parse_reviewer_team_candidates_single_item_skips_list_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Adapter:
        def validate_python(self, _data: object) -> list[object]:
            raise AssertionError("single reviewer team candidate should not validate list")

    monkeypatch.setattr(pr_reviewer_request, "_PRTeamListAdapter", Adapter())

    teams = parse_reviewer_team_candidates('[{"name": "Backend", "slug": "backend"}]')

    assert len(teams) == 1
    assert teams[0].slug == "backend"


def test_assignee_payload_includes_assignees() -> None:
    assert assignee_payload(["alice", "bob"]) == {
        "assignees": ["alice", "bob"]
    }


def test_assignee_payload_returns_empty_for_empty_input() -> None:
    assert assignee_payload([]) == {}


def test_reviewer_request_targets_pr_requested_reviewers_endpoint() -> None:
    request = reviewer_request(
        "owner/repo",
        123,
        method="POST",
        payload={"reviewers": ["alice"]},
    )

    assert request.args == (
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/requested_reviewers",
        "--input",
        "-",
    )
    assert request.input_text is not None
    assert json.loads(request.input_text) == {"reviewers": ["alice"]}


def test_assignee_request_targets_issue_assignees_endpoint() -> None:
    request = assignee_request(
        "owner/repo",
        123,
        method="DELETE",
        payload={"assignees": ["alice"]},
    )

    assert request.args == (
        "api",
        "--method",
        "DELETE",
        "/repos/owner/repo/issues/123/assignees",
        "--input",
        "-",
    )
    assert request.input_text is not None
    assert json.loads(request.input_text) == {"assignees": ["alice"]}


def test_reviewer_change_request_builds_request_plan_when_payload_exists() -> None:
    change = reviewer_change_request(
        123,
        reviewers=["alice"],
        team_reviewers=["backend"],
        method="POST",
    )

    assert change is not None
    request = change.to_request("owner/repo")
    assert request.args == (
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/requested_reviewers",
        "--input",
        "-",
    )
    assert request.input_text is not None
    assert json.loads(request.input_text) == {
        "reviewers": ["alice"],
        "team_reviewers": ["backend"],
    }


def test_reviewer_change_request_returns_none_for_empty_payload() -> None:
    assert (
        reviewer_change_request(
            123,
            reviewers=[],
            team_reviewers=None,
            method="DELETE",
        )
        is None
    )


def test_assignee_change_request_builds_request_plan_when_payload_exists() -> None:
    change = assignee_change_request(123, ["alice"], method="DELETE")

    assert change is not None
    request = change.to_request("owner/repo")
    assert request.args == (
        "api",
        "--method",
        "DELETE",
        "/repos/owner/repo/issues/123/assignees",
        "--input",
        "-",
    )
    assert request.input_text is not None
    assert json.loads(request.input_text) == {"assignees": ["alice"]}


def test_assignee_change_request_returns_none_for_empty_payload() -> None:
    assert assignee_change_request(123, [], method="POST") is None


@pytest.mark.asyncio
async def test_run_participant_change_runs_change_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "{}"

    await run_participant_change(
        "owner/repo",
        reviewer_change_request(
            123,
            reviewers=["alice"],
            team_reviewers=["backend"],
            method="POST",
        ),
        runner,
    )

    assert len(calls) == 1
    args, input_text = calls[0]
    assert args == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/requested_reviewers",
        "--input",
        "-",
    ]
    assert input_text is not None
    assert json.loads(input_text) == {
        "reviewers": ["alice"],
        "team_reviewers": ["backend"],
    }


@pytest.mark.asyncio
async def test_run_participant_change_skips_empty_change_request() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "{}"

    await run_participant_change("owner/repo", None, runner)

    assert calls == []


@pytest.mark.asyncio
async def test_request_reviewers_runs_post_reviewer_change() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "{}"

    await request_reviewers(
        "owner/repo",
        123,
        reviewers=["alice"],
        team_reviewers=["backend"],
        runner=runner,
    )

    assert calls[0][0] == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/pulls/123/requested_reviewers",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {
        "reviewers": ["alice"],
        "team_reviewers": ["backend"],
    }


@pytest.mark.asyncio
async def test_remove_requested_reviewers_runs_delete_reviewer_change() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "{}"

    await remove_requested_reviewers(
        "owner/repo",
        123,
        reviewers=["alice"],
        team_reviewers=[],
        runner=runner,
    )

    assert calls[0][0] == [
        "api",
        "--method",
        "DELETE",
        "/repos/owner/repo/pulls/123/requested_reviewers",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {"reviewers": ["alice"]}


@pytest.mark.asyncio
async def test_add_and_remove_assignees_run_issue_assignee_changes() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "{}"

    await add_assignees("owner/repo", 123, ["alice"], runner=runner)
    await remove_assignees("owner/repo", 123, ["bob"], runner=runner)

    assert calls[0][0] == [
        "api",
        "--method",
        "POST",
        "/repos/owner/repo/issues/123/assignees",
        "--input",
        "-",
    ]
    assert calls[0][1] is not None
    assert json.loads(calls[0][1]) == {"assignees": ["alice"]}
    assert calls[1][0] == [
        "api",
        "--method",
        "DELETE",
        "/repos/owner/repo/issues/123/assignees",
        "--input",
        "-",
    ]
    assert calls[1][1] is not None
    assert json.loads(calls[1][1]) == {"assignees": ["bob"]}


@pytest.mark.asyncio
async def test_empty_high_level_participant_changes_skip_runner() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return "{}"

    await request_reviewers(
        "owner/repo",
        123,
        reviewers=[],
        team_reviewers=None,
        runner=runner,
    )
    await add_assignees("owner/repo", 123, [], runner=runner)

    assert calls == []


def test_candidate_requests_target_paginated_repo_participant_endpoints() -> None:
    assert reviewer_user_candidates_request("owner/repo") == (
        "api",
        "/repos/owner/repo/collaborators?affiliation=all&per_page=100",
        "--paginate",
    )
    assert reviewer_team_candidates_request("owner/repo") == (
        "api",
        "/repos/owner/repo/teams?per_page=100",
        "--paginate",
    )
    assert assignee_candidates_request("owner/repo") == (
        "api",
        "/repos/owner/repo/assignees?per_page=100",
        "--paginate",
    )


def test_team_candidate_404_can_be_treated_as_empty_repo_teams() -> None:
    assert should_treat_team_candidates_error_as_empty("gh: Not Found (HTTP 404)")
    assert not should_treat_team_candidates_error_as_empty("gh: forbidden (HTTP 403)")


@pytest.mark.asyncio
async def test_fetch_reviewer_team_candidates_propagates_non_runtime_404_errors() -> (
    None
):
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        raise ValueError("unexpected parser state mentioning HTTP 404")

    with pytest.raises(ValueError, match="unexpected parser state"):
        await fetch_reviewer_team_candidates("owner/repo", runner)


def test_parse_reviewer_user_candidates_flattens_paginated_json() -> None:
    users = parse_reviewer_user_candidates(
        json.dumps([{"login": "alice"}, {"login": "bob"}])
        + json.dumps([{"login": "carol"}])
    )

    assert [user.login for user in users] == ["alice", "bob", "carol"]


@pytest.mark.asyncio
async def test_fetch_reviewer_user_candidates_runs_request_and_parses_result() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps([{"login": "alice"}]) + json.dumps([{"login": "bob"}])

    users = await fetch_reviewer_user_candidates("owner/repo", runner)

    assert [user.login for user in users] == ["alice", "bob"]
    assert calls == [
        (
            [
                "api",
                "/repos/owner/repo/collaborators?affiliation=all&per_page=100",
                "--paginate",
            ],
            None,
        )
    ]


@pytest.mark.asyncio
async def test_fetch_reviewer_candidates_fetches_users_and_teams() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        if "collaborators" in args[1]:
            return json.dumps([{"login": "alice"}])
        return json.dumps([{"name": "Backend", "slug": "backend"}])

    users, teams = await fetch_reviewer_candidates("owner/repo", runner)

    assert [user.login for user in users] == ["alice"]
    assert [team.slug for team in teams] == ["backend"]
    assert len(calls) == 2
    assert any(
        call == (
            [
                "api",
                "/repos/owner/repo/collaborators?affiliation=all&per_page=100",
                "--paginate",
            ],
            None,
        )
        for call in calls
    )
    assert any(
        call == (
            [
                "api",
                "/repos/owner/repo/teams?per_page=100",
                "--paginate",
            ],
            None,
        )
        for call in calls
    )


@pytest.mark.asyncio
async def test_fetch_assignee_candidates_runs_request_and_parses_result() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps([{"login": "carol"}]) + json.dumps([{"login": "drew"}])

    users = await fetch_assignee_candidates("owner/repo", runner)

    assert [user.login for user in users] == ["carol", "drew"]
    assert calls == [
        (
            [
                "api",
                "/repos/owner/repo/assignees?per_page=100",
                "--paginate",
            ],
            None,
        )
    ]


def test_parse_reviewer_team_candidates_flattens_paginated_json() -> None:
    teams = parse_reviewer_team_candidates(
        json.dumps([{"name": "Backend", "slug": "backend"}])
        + json.dumps([{"name": "Frontend", "slug": "frontend"}])
    )

    assert [team.slug for team in teams] == ["backend", "frontend"]


@pytest.mark.asyncio
async def test_fetch_reviewer_team_candidates_runs_request_and_parses_result() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps([{"name": "Backend", "slug": "backend"}])

    teams = await fetch_reviewer_team_candidates("owner/repo", runner)

    assert [team.slug for team in teams] == ["backend"]
    assert calls == [
        (
            [
                "api",
                "/repos/owner/repo/teams?per_page=100",
                "--paginate",
            ],
            None,
        )
    ]


@pytest.mark.asyncio
async def test_fetch_reviewer_team_candidates_treats_404_as_empty() -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        raise RuntimeError("gh: Not Found (HTTP 404)")

    assert await fetch_reviewer_team_candidates("owner/repo", runner) == []


@pytest.mark.asyncio
async def test_fetch_reviewer_team_candidates_propagates_non_404_errors() -> None:
    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        raise RuntimeError("gh: forbidden (HTTP 403)")

    with pytest.raises(RuntimeError, match="HTTP 403"):
        await fetch_reviewer_team_candidates("owner/repo", runner)
