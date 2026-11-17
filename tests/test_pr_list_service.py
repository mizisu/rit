import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from rit.services.github import GitHubError, GitHubService
from rit.services.pr_list import PRListLoader
from rit.state.pr_list import PRListSnapshot
from rit.state.store import PRListStore


def node(number: int, **changes: object) -> dict[str, object]:
    now = datetime.now(UTC)
    data: dict[str, object] = {
        "number": number,
        "title": f"PR {number}",
        "author": {"login": "other"},
        "baseRepository": {"nameWithOwner": "org/repo"},
        "headRepository": {"nameWithOwner": "org/repo"},
        "baseRefName": "main",
        "headRefName": f"branch-{number}",
        "state": "OPEN",
        "isDraft": False,
        "createdAt": (now - timedelta(days=10)).isoformat(),
        "updatedAt": now.isoformat(),
        "closedAt": None,
        "reviews": {"totalCount": 0},
        "reviewRequests": {"totalCount": 0},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
    }
    data.update(changes)
    return data


class ListRunner:
    def __init__(
        self, nodes: list[dict[str, object]], requests: tuple[int, ...] = ()
    ) -> None:
        self.nodes = nodes
        self.requests = requests
        self.calls: list[dict] = []
        self.fail_after_first_page = False
        self.search_count: int | None = None
        self.closed_search_count: int | None = None

    async def __call__(self, args: list[str], *, input_text: str | None = None) -> str:
        assert args == ["api", "graphql", "--input", "-"]
        request = json.loads(input_text or "{}")
        self.calls.append(request)
        query, variables = request["query"], request["variables"]
        if "viewer { login }" in query:
            return json.dumps(
                {
                    "data": {
                        "viewer": {"login": "viewer"},
                        "repository": {"defaultBranchRef": {"name": "main"}},
                    }
                }
            )
        if "search(" in query and "is:closed" in variables["query"]:
            assert variables["query"].startswith("repo:org/repo is:pr is:closed closed:>=")
            cutoff = datetime.fromisoformat(
                variables["query"].split("closed:>=", 1)[1].split()[0]
            ).replace(tzinfo=UTC)
            closed = [
                pr for pr in self.nodes
                if pr["state"] != "OPEN" and pr["closedAt"]
                and datetime.fromisoformat(str(pr["closedAt"])) >= cutoff
            ]
            offset = int(variables["after"] or 0)
            if self.fail_after_first_page and offset:
                raise GitHubError("rate limited")
            has_next = offset + 2 < len(closed)
            return json.dumps({"data": {"search": {
                "issueCount": self.closed_search_count
                if self.closed_search_count is not None else len(closed),
                "nodes": closed[offset:offset + 2],
                "pageInfo": {
                    "hasNextPage": has_next,
                    "endCursor": str(offset + 2) if has_next else None,
                },
            }}})
        if "search(" in query:
            assert variables["query"] == "repo:org/repo is:pr review-requested:viewer"
            return json.dumps(
                {
                    "data": {
                        "search": {
                            "issueCount": self.search_count
                            if self.search_count is not None
                            else len(self.requests),
                            "nodes": [{"number": number} for number in self.requests],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            )
        assert "states: [APPROVED, COMMENTED, CHANGES_REQUESTED, DISMISSED]" in query
        assert variables["viewer"] == "viewer"
        nodes = [
            pr
            for pr in self.nodes
            if (not variables["states"] or pr["state"] in variables["states"])
            and (not variables["head"] or pr["headRefName"] == variables["head"])
            and (not variables["base"] or pr["baseRefName"] == variables["base"])
        ]
        offset = int(variables["after"] or 0)
        if self.fail_after_first_page and offset:
            raise GitHubError("rate limited")
        page = nodes[offset : offset + 2]
        has_next = offset + 2 < len(nodes)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequests": {
                            "nodes": page,
                            "pageInfo": {
                                "hasNextPage": has_next,
                                "endCursor": str(offset + 2) if has_next else None,
                            },
                        }
                    }
                }
            }
        )


async def test_paginated_inbox_uses_combined_team_search_and_loads_old_stack_context() -> (
    None
):
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    runner = ListRunner(
        [
            node(1, state="MERGED", closedAt=old, updatedAt=old),
            node(2, baseRefName="branch-1", reviewRequests={"totalCount": 1}),
            node(3, reviews={"totalCount": 1}),
            node(4, author={"login": "viewer"}),
        ],
        requests=(2,),
    )
    result = await PRListLoader("org", "repo", runner).load()
    assert result.warnings == ()
    stacks = result.stacks("review")
    assert {pr.number for stack in stacks for pr in stack.members} == {1, 2, 3}
    linked = next(stack for stack in stacks if len(stack.members) == 2)
    assert linked.targets == {2}
    assert linked.group == "Needs review"
    assert result.stacks("created")[0].targets == {4}
    assert any(call["variables"].get("after") == "2" for call in runner.calls)


async def test_context_lookup_is_bounded_parallel_and_keeps_partial_results() -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()

    class ConcurrentRunner(ListRunner):
        def __init__(self) -> None:
            super().__init__(
                [node(i, reviews={"totalCount": 1}) for i in range(1, 9)]
                + [
                    node(101, state="MERGED", closedAt=old, updatedAt=old,
                         baseRefName="branch-1"),
                    node(102, state="MERGED", closedAt=old, updatedAt=old,
                         baseRefName="branch-101"),
                ]
            )
            self.active = 0
            self.peak = 0
            self.gate = asyncio.Event()
            self.branches: list[tuple[str | None, str | None]] = []

        async def __call__(
            self, args: list[str], *, input_text: str | None = None
        ) -> str:
            variables = json.loads(input_text or "{}")["variables"]
            if variables.get("head") or variables.get("base"):
                self.branches.append((variables["head"], variables["base"]))
                self.active += 1
                self.peak = max(self.peak, self.active)
                if self.active == 4:
                    asyncio.get_running_loop().call_soon(self.gate.set)
                try:
                    await self.gate.wait()
                    if variables["base"] == "branch-2":
                        raise GitHubError("branch unavailable")
                    return await super().__call__(args, input_text=input_text)
                finally:
                    self.active -= 1
            return await super().__call__(args, input_text=input_text)

    runner = ConcurrentRunner()
    async with asyncio.timeout(5):
        result = await PRListLoader("org", "repo", runner).load()
    assert runner.peak == 4 and runner.active == 0
    assert len(runner.branches) == len(set(runner.branches))
    assert {pr.number for pr in result.items} == {*range(1, 9), 101, 102}
    assert len(result.warnings) == 1
    assert "branch-2" in result.warnings[0]
    assert "branch unavailable" in result.warnings[0]


async def test_initial_list_waits_for_requests_but_not_stack_context() -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    pages_loaded = asyncio.Event()

    class StagedRunner(ListRunner):
        loaded = 0

        async def __call__(
            self, args: list[str], *, input_text: str | None = None
        ) -> str:
            request = json.loads(input_text or "{}")
            variables = request["variables"]
            query = variables.get("query", "")
            if "review-requested:" in query:
                await pages_loaded.wait()
            result = await super().__call__(args, input_text=input_text)
            if variables.get("states") == ["OPEN"] or "is:closed" in query:
                self.loaded += 1
                if self.loaded == 2:
                    pages_loaded.set()
            return result

    runner = StagedRunner(
        [
            node(1, state="MERGED", closedAt=old, updatedAt=old),
            node(2, baseRefName="branch-1", reviewRequests={"totalCount": 1}),
        ],
        requests=(2,),
    )
    snapshots: list[PRListSnapshot] = []

    def initial(snapshot: PRListSnapshot) -> None:
        snapshots.append(snapshot)
        assert not snapshot.context_complete
        assert [pr.number for pr in snapshot.items] == [2]
        assert snapshot.items[0].requested
        assert not any(
            call["variables"].get("head") or call["variables"].get("base")
            for call in runner.calls
        )

    async with asyncio.timeout(5):
        result = await PRListLoader("org", "repo", runner).load(on_initial=initial)
    assert result.context_complete and not result.warnings
    assert {pr.number for pr in result.items} == {1, 2}
    assert len(snapshots) == 1 and len(snapshots[0].items) == 1


async def test_closed_search_is_paginated_and_ignores_recent_updates_to_old_prs() -> None:
    now = datetime.now(UTC)
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=30)).isoformat()
    runner = ListRunner([
        node(1, state="MERGED", closedAt=recent, reviews={"totalCount": 1}),
        node(2, state="CLOSED", closedAt=recent, reviews={"totalCount": 1}),
        node(3, state="MERGED", closedAt=recent, reviews={"totalCount": 1}),
        node(4, state="MERGED", closedAt=old),
        node(5, state="CLOSED", closedAt=(now - timedelta(days=7, seconds=1)).isoformat()),
    ])
    result = await PRListLoader("org", "repo", runner).load()
    assert {pr.number for pr in result.items} == {1, 2, 3}
    assert not result.warnings
    searches = [
        call for call in runner.calls
        if "is:closed" in call["variables"].get("query", "")
    ]
    assert len(searches) >= 2 and searches[1]["variables"]["after"] == "2"
    assert not any(
        call["variables"].get("states") == ["CLOSED", "MERGED"]
        for call in runner.calls
    )
    runner.closed_search_count = 1001
    result = await PRListLoader("org", "repo", runner).load()
    assert any("CLOSED/MERGED" in warning and "1,000" in warning for warning in result.warnings)


@pytest.mark.parametrize("refresh", [False, True])
async def test_store_publishes_initial_list_without_replacing_a_complete_refresh(
    monkeypatch: pytest.MonkeyPatch, refresh: bool,
) -> None:
    initial = PRListSnapshot("org", "repo", "viewer", (), context_complete=False)
    complete = PRListSnapshot("org", "repo", "viewer", ())
    old = PRListSnapshot("org", "repo", "viewer", ()) if refresh else None
    published = asyncio.Event()
    finish = asyncio.Event()
    store = PRListStore("org", "repo")
    store.snapshot = old
    messages: list[object] = []
    store.set_message_sink(messages.append)

    async def fetch(
        *, on_initial: Callable[[PRListSnapshot], None] | None = None,
    ) -> PRListSnapshot:
        assert on_initial is not None
        on_initial(initial)
        published.set()
        await finish.wait()
        return complete

    monkeypatch.setattr(store._service, "get_pr_list", fetch)
    async with asyncio.timeout(5):
        task = asyncio.create_task(store.load())
        try:
            await published.wait()
            assert store.loading
            assert store.snapshot is (old if refresh else initial)
            assert len(messages) == (0 if refresh else 1)
        finally:
            finish.set()
            await task
    assert store.snapshot is complete and not store.loading


async def test_partial_pages_are_reported_instead_of_claiming_a_complete_list() -> None:
    runner = ListRunner([node(i) for i in range(1, 4)])
    runner.fail_after_first_page = True
    result = await PRListLoader("org", "repo", runner).load()
    assert len(result.items) == 2
    assert "rate limited" in result.warnings[0]


async def test_search_limit_does_not_turn_unknown_requests_into_completed_reviews() -> (
    None
):
    runner = ListRunner(
        [
            node(1, reviews={"totalCount": 1}, reviewRequests={"totalCount": 1}),
            node(2, reviews={"totalCount": 1}),
        ]
    )
    runner.search_count = 1001
    result = await PRListLoader("org", "repo", runner).load()
    assert result.warnings
    assert [stack.targets for stack in result.stacks("review")] == [frozenset({2})]
    assert next(pr for pr in result.items if pr.number == 1).requested is None


def test_invalid_or_repeated_pagination_is_rejected() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        PRListLoader._next_cursor({}, set())
    with pytest.raises(ValueError, match="advance"):
        PRListLoader._next_cursor(
            {"pageInfo": {"hasNextPage": True, "endCursor": "same"}}, {"same"}
        )


async def test_service_resolves_repository_and_store_retains_snapshot_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = ListRunner([node(1, reviews={"totalCount": 1})])
    service = GitHubService("org", "repo")
    monkeypatch.setattr(service, "_run_gh", runner)
    result = await service.get_pr_list()
    assert result.owner == "org" and result.repo == "repo"
    store = PRListStore("org", "repo")
    store.snapshot = result

    async def fail(*, on_initial: object) -> PRListSnapshot:
        raise GitHubError("permission denied")

    monkeypatch.setattr(store._service, "get_pr_list", fail)
    await store.load()
    assert store.snapshot is result
    assert store.error == "permission denied"
    assert store.attempted and not store.loading
