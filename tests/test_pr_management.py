from rit.state.models import PR, PRTeam, PRUser, ReviewRequest
from rit.state.pr_management import (
    plan_assignee_selection,
    plan_reviewer_selection,
)


def _pr(
    *,
    reviewers: list[ReviewRequest] | None = None,
    assignees: list[PRUser] | None = None,
) -> PR:
    return PR.model_validate(
        {
            "number": 123,
            "author": PRUser(login="author"),
            "reviewRequests": {"nodes": reviewers or []},
            "assignees": {"nodes": assignees or []},
        }
    )


def _request(reviewer: PRUser | PRTeam) -> ReviewRequest:
    return ReviewRequest.model_validate({"requestedReviewer": reviewer})


def test_reviewer_selection_plan_diffs_sanitized_users_and_teams() -> None:
    pr = _pr(
        reviewers=[
            _request(PRUser(login="alice")),
            _request(PRTeam(name="Backend", slug="backend")),
        ],
    )

    plan = plan_reviewer_selection(
        pr,
        users=[" bob ", "author", ""],
        teams=[" ops ", ""],
    )

    assert plan.add_users == ("bob",)
    assert plan.add_teams == ("ops",)
    assert plan.remove_users == ("alice",)
    assert plan.remove_teams == ("backend",)
    assert plan.has_changes is True


def test_reviewer_selection_plan_handles_team_name_without_slug() -> None:
    pr = _pr(reviewers=[_request(PRTeam(name="Security"))])

    plan = plan_reviewer_selection(pr, users=[], teams=["security"])

    assert plan.add_teams == ("security",)
    assert plan.remove_teams == ("Security",)


def test_reviewer_selection_plan_reports_unchanged_selection() -> None:
    pr = _pr(
        reviewers=[
            _request(PRUser(login="alice")),
            _request(PRTeam(name="Backend", slug="backend")),
        ],
    )

    plan = plan_reviewer_selection(
        pr,
        users=["alice"],
        teams=["backend"],
    )

    assert plan.has_changes is False


def test_assignee_selection_plan_diffs_sanitized_logins() -> None:
    pr = _pr(assignees=[PRUser(login="alice"), PRUser(login="carol")])

    plan = plan_assignee_selection(pr, [" bob ", "carol", ""])

    assert plan.add_logins == ("bob",)
    assert plan.remove_logins == ("alice",)
    assert plan.has_changes is True


def test_assignee_selection_plan_reports_unchanged_selection() -> None:
    pr = _pr(assignees=[PRUser(login="alice")])

    plan = plan_assignee_selection(pr, ["alice"])

    assert plan.has_changes is False
