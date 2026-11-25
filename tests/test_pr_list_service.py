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

    def loader(self) -> PRListLoader:
        return PRListLoader("org", "repo", self, viewer="viewer", default_branch="main")

    async def __call__(self, args: list[str], *, input_text: str | None = None) -> str:
        if input_text is None:
            assert args[:3] == ["api", "graphql", "-f"]
            data: dict[str, object] = {
                "viewer": {"login": "viewer"},
                "repository": {
                    "nameWithOwner": "org/repo",
                    "defaultBranchRef": {"name": "main"},
                },
            }
            if "preview=true" in args:
                data["requested"] = {
                    "nodes": [p for p in self.nodes if p["number"] in self.requests][:2]
                }
                data["authored"] = {
                    "nodes": [
                        p for p in self.nodes if p["author"] == {"login": "viewer"}
                    ][:2]
                }
            else:
                assert "preview=false" in args
            return json.dumps({"data": data})
        assert args == ["api", "graphql", "--input", "-"]
        request = json.loads(input_text)
        self.calls.append(request)
        query, variables = request["query"], request["variables"]
        if "search(" in query and "is:closed" in variables["query"]:
            assert variables["query"].startswith(
                "repo:org/repo is:pr is:closed closed:>="
            )
            cutoff = datetime.fromisoformat(
                variables["query"].split("closed:>=", 1)[1].split()[0]
            ).replace(tzinfo=UTC)
            closed = [
                pr
                for pr in self.nodes
                if pr["state"] != "OPEN"
                and pr["closedAt"]
                and datetime.fromisoformat(str(pr["closedAt"])) >= cutoff
            ]
            offset = int(variables["after"] or 0)
            if self.fail_after_first_page and offset:
                raise GitHubError("rate limited")
            has_next = offset + 2 < len(closed)
            return json.dumps(
                {
                    "data": {
                        "search": {
                            "issueCount": self.closed_search_count
                            if self.closed_search_count is not None
                            else len(closed),
                            "nodes": closed[offset : offset + 2],
                            "pageInfo": {
                                "hasNextPage": has_next,
                                "endCursor": str(offset + 2) if has_next else None,
                            },
                        }
                    }
                }
            )
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
        if "branch0" in variables:
            connections = {}
            for i in range(sum(key.startswith("branch") for key in variables)):
                branch = variables[f"branch{i}"]
                head = f"headRefName: $branch{i}," in query
                assert head or f"baseRefName: $branch{i}," in query
                connections[f"pr{i}"] = self.connection(
                    head=branch if head else None, base=None if head else branch
                )
        else:
            connections = {
                "pullRequests": self.connection(
                    states=variables["states"],
                    head=variables["head"],
                    base=variables["base"],
                    offset=int(variables["after"] or 0),
                )
            }
        return json.dumps({"data": {"repository": connections}})

    def connection(
        self,
        *,
        head: str | None,
        base: str | None,
        states: list[str] | None = None,
        offset: int = 0,
    ) -> dict[str, object]:
        nodes = [
            pr
            for pr in self.nodes
            if (not states or pr["state"] in states)
            and (not head or pr["headRefName"] == head)
            and (not base or pr["baseRefName"] == base)
        ]
        if self.fail_after_first_page and offset:
            raise GitHubError("rate limited")
        has_next = offset + 2 < len(nodes)
        return {
            "nodes": nodes[offset : offset + 2],
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": str(offset + 2) if has_next else None,
            },
        }


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
    result = await runner.loader().load()
    assert result.warnings == ()
    stacks = result.stacks("review")
    assert {pr.number for stack in stacks for pr in stack.members} == {1, 2, 3}
    linked = next(stack for stack in stacks if len(stack.members) == 2)
    assert linked.targets == {2}
    assert linked.group == "Needs review"
    assert result.stacks("created")[0].targets == {4}
    assert any(call["variables"].get("after") == "2" for call in runner.calls)


@pytest.mark.parametrize("branch_error", [False, True])
async def test_batched_context_paginates_recurses_and_isolates_errors(
    branch_error: bool,
) -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()

    class BranchRunner(ListRunner):
        async def __call__(
            self, args: list[str], *, input_text: str | None = None
        ) -> str:
            variables = json.loads(input_text or "{}")["variables"]
            if branch_error and (
                ("branch0" in variables and "branch-2" in variables.values())
                or variables.get("base") == "branch-2"
            ):
                raise GitHubError("branch unavailable")
            return await super().__call__(args, input_text=input_text)

    runner = BranchRunner(
        [node(i, reviews={"totalCount": 1}) for i in range(1, 16)]
        + [
            node(i, state="MERGED", closedAt=old, baseRefName="branch-1")
            for i in (101, 102, 103)
        ]
        + [node(104, state="MERGED", closedAt=old, baseRefName="branch-103")]
    )
    result = await runner.loader().load()
    assert {pr.number for pr in result.items} == {*range(1, 16), 101, 102, 103, 104}
    assert len(result.warnings) == int(branch_error)
    if branch_error:
        assert "base:branch-2" in result.warnings[0]
    else:
        batches = [c["variables"] for c in runner.calls if "branch0" in c["variables"]]
        assert sum(k.startswith("branch") for k in batches[0]) == 12
        assert len(batches) < 15
        assert not any(
            c["variables"].get("base") and not c["variables"]["after"]
            for c in runner.calls
        )
    assert any(
        c["variables"].get("after") == "2" and c["variables"].get("base") == "branch-1"
        for c in runner.calls
    )


async def test_active_preview_precedes_history_then_full_inbox_precedes_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    runner = ListRunner(
        [
            node(1, state="MERGED", closedAt=old),
            node(2, baseRefName="branch-1", reviewRequests={"totalCount": 1}),
            node(3, reviews={"totalCount": 1}),
            node(4, author={"login": "viewer"}, isDraft=True),
            node(5, author={"login": "viewer"}),
            node(6, author={"login": "viewer"}),
        ],
        requests=(2,),
    )
    snapshots: list[PRListSnapshot] = []

    def initial(snapshot: PRListSnapshot) -> None:
        snapshots.append(snapshot)
        assert not snapshot.context_complete
        assert not any("branch0" in call["variables"] for call in runner.calls)

    service = GitHubService("org", "repo")
    monkeypatch.setattr(service, "_run_gh", runner)
    result = await service.get_pr_list(on_initial=initial)
    preview, full = snapshots
    assert preview.active_preview and not full.active_preview
    assert {item.number for item in preview.items} == {2, 4, 5}
    assert preview.stacks("review")[0].group == "Needs review"
    assert {stack.group for stack in preview.stacks("created")} == {"Draft", "Open"}
    assert {item.number for item in full.items} == {2, 3, 4, 5, 6}
    assert result.context_complete and not result.warnings
    assert {pr.number for pr in result.items} == {1, 2, 3, 4, 5, 6}


@pytest.mark.parametrize("explicit", [False, True])
async def test_service_bootstraps_preview_in_one_call_then_paginates(
    monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    requested = node(1, reviewRequests={"totalCount": 1}, reviews={"totalCount": 3})
    authored = node(2, author={"login": "viewer"}, isDraft=True)
    done = node(3, reviews={"totalCount": 1})
    runner = ListRunner([requested, authored, done], requests=(1,))
    calls: list[list[str]] = []
    snapshots: list[PRListSnapshot] = []

    async def run(args: list[str], *, input_text: str | None = None) -> str:
        calls.append(args)
        if input_text is not None:
            return await runner(args, input_text=input_text)
        owner, repo, flag = (
            ("org", "repo", "-f") if explicit else ("{owner}", "{repo}", "-F")
        )
        assert args[:3] == ["api", "graphql", "-f"]
        assert "viewer { login }" in args[3] and "requested: search(" in args[3]
        assert args[4:] == [
            "-F",
            "preview=true",
            flag,
            f"owner={owner}",
            flag,
            f"repo={repo}",
            flag,
            f"requested=repo:{owner}/{repo} is:pr is:open review-requested:@me",
            flag,
            f"authored=repo:{owner}/{repo} is:pr is:open author:@me",
        ]
        return json.dumps(
            {
                "data": {
                    "viewer": {"login": "viewer"},
                    "repository": {
                        "nameWithOwner": "org/repo",
                        "defaultBranchRef": {"name": "main"},
                    },
                    "requested": {
                        "nodes": [
                            {k: v for k, v in requested.items() if k != "reviews"}
                        ]
                    },
                    "authored": {
                        "nodes": [{k: v for k, v in authored.items() if k != "reviews"}]
                    },
                }
            }
        )

    def publish(snapshot: PRListSnapshot) -> None:
        if not snapshots:
            assert len(calls) == 1 and snapshot.active_preview
        snapshots.append(snapshot)

    service = GitHubService("org", "repo") if explicit else GitHubService()
    monkeypatch.setattr(service, "_run_gh", run)
    final = await service.get_pr_list(on_initial=publish)
    preview, inbox = snapshots
    assert {item.number for item in preview.items} == {1, 2}
    assert preview.stacks("review")[0].group == "Needs review"
    assert preview.stacks("created")[0].group == "Draft"
    assert not inbox.active_preview and not inbox.context_complete
    assert final.context_complete and not final.warnings
    assert {p.number: p.review_count for p in final.items} == {1: 3, 2: 0, 3: 1}
    assert not any(
        "viewer { login }" in c["query"] or "authored" in c["variables"]
        for c in runner.calls
    )
    before = len(calls)
    assert (await service.get_repo()).full_name == "org/repo"
    assert len(calls) == before


@pytest.mark.parametrize("with_preview", [False, True])
@pytest.mark.parametrize("failure", ["error", "partial", "foreign row"])
async def test_bootstrap_failure_is_reported_without_retry(
    monkeypatch: pytest.MonkeyPatch, failure: str, with_preview: bool
) -> None:
    service = GitHubService()
    commands: list[list[str]] = []
    snapshots: list[PRListSnapshot] = []

    async def run(args: list[str], *, input_text: str | None = None) -> str:
        commands.append(args)
        if failure == "error":
            raise GitHubError("preview unavailable")
        if failure == "partial":
            return json.dumps({"errors": [{"message": "incomplete"}]})
        return json.dumps(
            {
                "data": {
                    "viewer": {"login": "viewer"},
                    "repository": {"nameWithOwner": "other/repo"},
                    "authored": {"nodes": []},
                    "requested": {"nodes": [node(99)]},
                }
            }
        )

    monkeypatch.setattr(service, "_run_gh", run)
    if failure == "foreign row" and not with_preview:
        # A refresh doesn't request rows; validate its explicit repository instead.
        service = GitHubService("org", "repo")
        monkeypatch.setattr(service, "_run_gh", run)
    with pytest.raises(GitHubError):
        await service.get_pr_list(on_initial=snapshots.append if with_preview else None)
    assert len(commands) == 1 and commands[0][:2] == ["api", "graphql"]
    assert not snapshots


async def test_closed_search_is_paginated_and_ignores_recent_updates_to_old_prs() -> (
    None
):
    now = datetime.now(UTC)
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=30)).isoformat()
    runner = ListRunner(
        [
            node(1, state="MERGED", closedAt=recent, reviews={"totalCount": 1}),
            node(2, state="CLOSED", closedAt=recent, reviews={"totalCount": 1}),
            node(3, state="MERGED", closedAt=recent, reviews={"totalCount": 1}),
            node(4, state="MERGED", closedAt=old),
            node(
                5,
                state="CLOSED",
                closedAt=(now - timedelta(days=7, seconds=1)).isoformat(),
            ),
        ]
    )
    result = await runner.loader().load()
    assert {pr.number for pr in result.items} == {1, 2, 3}
    assert not result.warnings
    searches = [
        call
        for call in runner.calls
        if "is:closed" in call["variables"].get("query", "")
    ]
    assert len(searches) >= 2 and searches[1]["variables"]["after"] == "2"
    assert not any(
        call["variables"].get("states") == ["CLOSED", "MERGED"] for call in runner.calls
    )
    runner.closed_search_count = 1001
    result = await runner.loader().load()
    assert any(
        "CLOSED/MERGED" in warning and "1,000" in warning for warning in result.warnings
    )


@pytest.mark.parametrize("refresh", [False, True])
async def test_store_publishes_initial_list_without_replacing_a_complete_refresh(
    monkeypatch: pytest.MonkeyPatch,
    refresh: bool,
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
        *,
        on_initial: Callable[[PRListSnapshot], None] | None = None,
    ) -> PRListSnapshot:
        if refresh:
            assert on_initial is None
        else:
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
    result = await runner.loader().load()
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
    result = await runner.loader().load()
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
