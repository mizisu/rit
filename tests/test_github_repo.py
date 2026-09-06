import asyncio
import json

from rit.services.github import GitHubService
from rit.services.github_repo import (
    GitHubRepo,
    fetch_repo_view,
    parse_repo_view_response,
    repo_view_request,
)


def test_repo_full_name_joins_owner_and_repo_name() -> None:
    assert GitHubRepo(owner="owner", name="repo").full_name == "owner/repo"


def test_repo_view_request_asks_for_owner_and_name() -> None:
    assert repo_view_request() == ("repo", "view", "--json", "owner,name")


def test_parse_repo_view_response_uses_owner_login_and_name() -> None:
    repo = parse_repo_view_response(
        json.dumps({"owner": {"login": "owner"}, "name": "repo"})
    )

    assert repo == GitHubRepo(owner="owner", name="repo")


async def test_fetch_repo_view_runs_request_and_parses_repo() -> None:
    calls: list[tuple[list[str], str | None]] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        calls.append((args, input_text))
        return json.dumps({"owner": {"login": "owner"}, "name": "repo"})

    repo = await fetch_repo_view(runner)

    assert repo == GitHubRepo(owner="owner", name="repo")
    assert calls == [(["repo", "view", "--json", "owner,name"], None)]


async def test_github_service_coalesces_concurrent_repo_detection(monkeypatch) -> None:
    calls = 0
    service = GitHubService()

    async def runner(_args: list[str], *, input_text: str | None = None) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return json.dumps({"owner": {"login": "owner"}, "name": "repo"})

    monkeypatch.setattr(service, "_run_gh", runner)

    repos = await asyncio.gather(*(service.get_repo() for _ in range(4)))

    assert calls == 1
    assert repos == [GitHubRepo(owner="owner", name="repo")] * 4
