import pytest

from rit.state.models import PR, PRTeam, PRUser, ReviewRequest
from rit.state.store import PRStore


class FakePRManagementService:
    def __init__(self, summary: PR) -> None:
        self.summary = summary
        self.request_reviewers_calls: list[
            tuple[int, tuple[str, ...], tuple[str, ...]]
        ] = []
        self.remove_reviewers_calls: list[
            tuple[int, tuple[str, ...], tuple[str, ...]]
        ] = []
        self.add_assignees_calls: list[tuple[int, tuple[str, ...]]] = []
        self.remove_assignees_calls: list[tuple[int, tuple[str, ...]]] = []

    async def request_reviewers(
        self,
        pr_number: int,
        *,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> None:
        self.request_reviewers_calls.append(
            (pr_number, tuple(reviewers or ()), tuple(team_reviewers or ()))
        )

    async def remove_requested_reviewers(
        self,
        pr_number: int,
        *,
        reviewers: list[str] | None = None,
        team_reviewers: list[str] | None = None,
    ) -> None:
        self.remove_reviewers_calls.append(
            (pr_number, tuple(reviewers or ()), tuple(team_reviewers or ()))
        )

    async def add_assignees(self, pr_number: int, assignees: list[str]) -> None:
        self.add_assignees_calls.append((pr_number, tuple(assignees)))

    async def remove_assignees(self, pr_number: int, assignees: list[str]) -> None:
        self.remove_assignees_calls.append((pr_number, tuple(assignees)))

    async def get_pr_summary(self, pr_number: int) -> PR:
        return self.summary


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


@pytest.mark.asyncio
async def test_set_requested_reviewers_diffs_users_and_teams() -> None:
    current = _pr(
        reviewers=[
            _request(PRUser(login="alice")),
            _request(PRTeam(name="Backend", slug="backend")),
        ]
    )
    summary = _pr(
        reviewers=[
            _request(PRUser(login="bob")),
            _request(PRTeam(name="Ops", slug="ops")),
        ]
    )
    store = PRStore(pr_number=123)
    store.state.pr = current
    service = FakePRManagementService(summary)
    store._service = service

    changed = await store.set_requested_reviewers(users=["bob"], teams=["ops"])

    assert changed is True
    assert service.remove_reviewers_calls == [(123, ("alice",), ("backend",))]
    assert service.request_reviewers_calls == [(123, ("bob",), ("ops",))]
    assert store.state.pr is not None
    assert [request.display_name for request in store.state.pr.requested_reviewers] == [
        "bob",
        "Ops",
    ]


@pytest.mark.asyncio
async def test_set_requested_reviewers_noops_when_selection_unchanged() -> None:
    current = _pr(reviewers=[_request(PRUser(login="alice"))])
    store = PRStore(pr_number=123)
    store.state.pr = current
    service = FakePRManagementService(current)
    store._service = service

    changed = await store.set_requested_reviewers(users=["alice"], teams=[])

    assert changed is False
    assert service.remove_reviewers_calls == []
    assert service.request_reviewers_calls == []


@pytest.mark.asyncio
async def test_set_assignees_diffs_logins() -> None:
    current = _pr(assignees=[PRUser(login="alice")])
    summary = _pr(assignees=[PRUser(login="bob")])
    store = PRStore(pr_number=123)
    store.state.pr = current
    service = FakePRManagementService(summary)
    store._service = service

    changed = await store.set_assignees(["bob"])

    assert changed is True
    assert service.remove_assignees_calls == [(123, ("alice",))]
    assert service.add_assignees_calls == [(123, ("bob",))]
    assert store.state.pr is not None
    assert [user.login for user in store.state.pr.assignees] == ["bob"]
