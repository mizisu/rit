"""Fetch a repository inbox and its branch-linked context through GraphQL."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Self

from rit.services.gh_request import GitHubInputRunner
from rit.services.github_repo import GitHubRepo
from rit.services.graphql_request import GraphQLRequestError, mapping, run_graphql
from rit.state.pr_list import PRListItem, PRListSnapshot

_ROW_FIELDS = """
  number title state isDraft createdAt updatedAt closedAt
  author { login }
  baseRepository { nameWithOwner }
  headRepository { nameWithOwner }
  baseRefName headRefName
  reviewRequests(first: 1) { totalCount }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
"""
_FIELDS = (
    _ROW_FIELDS
    + """
  reviews(author: $viewer, first: 1,
    states: [APPROVED, COMMENTED, CHANGES_REQUESTED, DISMISSED]) { totalCount }
"""
)
_CONNECTION = "pageInfo { hasNextPage endCursor } nodes {" + _FIELDS + "}"
_BRANCH_BATCH_SIZE = 12
_BOOTSTRAP_QUERY = (
    """
query($owner: String!, $repo: String!, $requested: String!, $authored: String!, $preview: Boolean!) {
  viewer { login }
  repository(owner: $owner, name: $repo) { nameWithOwner defaultBranchRef { name } }
  requested: search(type: ISSUE, query: $requested, first: 100) @include(if: $preview) { ...activePRs }
  authored: search(type: ISSUE, query: $authored, first: 100) @include(if: $preview) { ...activePRs }
}
fragment activePRs on SearchResultItemConnection {
  nodes { ... on PullRequest {
"""
    + _ROW_FIELDS
    + "} } }"
)

_LIST_QUERY = (
    """
query($owner: String!, $repo: String!, $viewer: String!, $after: String,
      $states: [PullRequestState!], $head: String, $base: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(first: 100, after: $after, states: $states,
      headRefName: $head, baseRefName: $base,
      orderBy: {field: UPDATED_AT, direction: DESC}) {
"""
    + _CONNECTION
    + """
    }
  }
}
"""
)
_REQUESTS_QUERY = """
query($query: String!, $after: String) {
  search(query: $query, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number } }
  }
}
"""
_CLOSED_QUERY = (
    """
query($query: String!, $viewer: String!, $after: String) {
  search(query: $query, type: ISSUE, first: 100, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest {
"""
    + _FIELDS
    + """
    } }
  }
}
"""
)


class PRListLoader:
    """Load list-sized data without fetching discussions, files, or diffs."""

    def __init__(
        self,
        owner: str,
        repo: str,
        runner: GitHubInputRunner,
        *,
        viewer: str,
        default_branch: str,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.runner = runner
        self.viewer = viewer
        self.default_branch = default_branch
        self.items: dict[int, PRListItem] = {}
        self.requested: set[int] = set()
        self.requests_complete = True
        self.warnings: list[str] = []

    @classmethod
    async def bootstrap(
        cls,
        repo: GitHubRepo | None,
        runner: GitHubInputRunner,
        *,
        on_initial: Callable[[PRListSnapshot], None] | None = None,
    ) -> Self:
        """Resolve the repository, viewer and active rows in one gh round trip."""
        owner, name = (repo.owner, repo.name) if repo else ("{owner}", "{repo}")
        scope = f"repo:{owner}/{name} is:pr is:open"
        # Only the implicit repository uses gh placeholder expansion. Explicit
        # names must stay literal (including names resembling numbers or booleans).
        flag = "-f" if repo else "-F"
        args = [
            "api",
            "graphql",
            "-f",
            f"query={_BOOTSTRAP_QUERY}",
            "-F",
            "preview=false" if on_initial is None else "preview=true",
        ]
        for field in (
            f"owner={owner}",
            f"repo={name}",
            f"requested={scope} review-requested:@me",
            f"authored={scope} author:@me",
        ):
            args.extend((flag, field))
        response = mapping(json.loads(await runner(args)))
        if response.get("errors"):
            raise GraphQLRequestError("GitHub did not return a complete inbox preview")
        data = mapping(response.get("data"))
        full_name = mapping(data.get("repository")).get("nameWithOwner")
        if (
            not isinstance(full_name, str)
            or full_name.count("/") != 1
            or not all(full_name.split("/"))
        ):
            raise GraphQLRequestError("GitHub did not return a repository name")
        if repo and repo.full_name.casefold() != full_name.casefold():
            raise GraphQLRequestError("GitHub returned a different repository")
        owner, name = full_name.split("/")
        login = mapping(data.get("viewer")).get("login")
        if not isinstance(login, str) or not login:
            raise GraphQLRequestError("GitHub did not return the viewer")
        default = mapping(mapping(data.get("repository")).get("defaultBranchRef")).get(
            "name"
        )
        loader = cls(
            owner,
            name,
            runner,
            viewer=login,
            default_branch=default if isinstance(default, str) else "",
        )
        if on_initial is not None:
            on_initial(loader._active_snapshot(data))
        return loader

    async def load(
        self, *, on_initial: Callable[[PRListSnapshot], None] | None = None
    ) -> PRListSnapshot:
        now = datetime.now(UTC)
        results = await asyncio.gather(
            self._load_requests(),
            self._load_open(),
            self._load_recent_closed(now),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        if on_initial is not None:
            on_initial(self._snapshot(context_complete=False))
        try:
            await self._load_context(now)
        except (ValueError, RuntimeError) as error:
            self.warnings.append(f"Partial stack context: {error}")
        return self._snapshot()

    def _snapshot(self, *, context_complete: bool = True) -> PRListSnapshot:
        return PRListSnapshot(
            self.owner,
            self.repo,
            self.viewer,
            tuple(self._with_request(item) for item in self.items.values()),
            tuple(self.warnings),
            self.default_branch,
            context_complete=context_complete,
        )

    def _active_snapshot(self, data: Mapping[str, object]) -> PRListSnapshot:
        items: dict[int, PRListItem] = {}
        for key in ("requested", "authored"):
            nodes = mapping(data.get(key)).get("nodes")
            if not isinstance(nodes, list):
                raise GraphQLRequestError("GitHub did not return active PRs")
            for node in nodes:
                # Review history is irrelevant to the active groups. Only the
                # authoritative paginated inbox populates it for the Done group.
                item = self._parse_item({**mapping(node), "review_count": 0})
                if item.state != "OPEN":
                    continue
                mine = item.author.casefold() == self.viewer.casefold()
                if key == "requested" and not mine and item.request_count:
                    items[item.number] = item.model_copy(update={"requested": True})
                elif key == "authored" and mine:
                    items[item.number] = item
        return PRListSnapshot(
            self.owner,
            self.repo,
            self.viewer,
            tuple(items.values()),
            default_branch=self.default_branch,
            context_complete=False,
            active_preview=True,
        )

    async def _load_open(self) -> None:
        try:
            async for page in self._pages(states=["OPEN"]):
                self.items.update((item.number, item) for item in page)
        except (ValueError, RuntimeError) as error:
            self.warnings.append(f"Partial OPEN list: {error}")

    async def _load_recent_closed(self, now: datetime) -> None:
        cutoff = now - timedelta(days=7)
        after: str | None = None
        seen: set[str] = set()
        fetched = 0
        try:
            while True:
                response = await run_graphql(
                    _CLOSED_QUERY,
                    {
                        "query": (
                            f"repo:{self.owner}/{self.repo} is:pr is:closed "
                            f"closed:>={cutoff.date().isoformat()} sort:updated-desc"
                        ),
                        "viewer": self.viewer,
                        "after": after,
                    },
                    runner=self.runner,
                )
                connection = mapping(mapping(response.get("data")).get("search"))
                nodes = connection.get("nodes")
                count = connection.get("issueCount")
                if not isinstance(nodes, list) or not isinstance(count, int):
                    raise GraphQLRequestError("GitHub did not return closed PRs")
                for node in nodes:
                    item = self._parse_item(node)
                    if (
                        item.state != "OPEN"
                        and item.closed_at
                        and item.closed_at >= cutoff
                    ):
                        self.items[item.number] = item
                fetched += len(nodes)
                after = self._next_cursor(connection, seen)
                if after is None or fetched >= 1000:
                    if count > 1000:
                        self.warnings.append(
                            "Partial CLOSED/MERGED list: GitHub search is limited "
                            "to 1,000 results."
                        )
                    return
        except (ValueError, RuntimeError) as error:
            self.warnings.append(f"Partial CLOSED/MERGED list: {error}")

    @staticmethod
    def _next_cursor(connection: object, seen: set[str]) -> str | None:
        page = mapping(mapping(connection).get("pageInfo"))
        has_next = page.get("hasNextPage")
        if not isinstance(has_next, bool):
            raise GraphQLRequestError("GitHub returned incomplete PR pagination")
        if not has_next:
            return None
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise GraphQLRequestError("GitHub PR pagination did not advance")
        seen.add(cursor)
        return cursor

    async def _load_requests(self) -> None:
        after: str | None = None
        seen: set[str] = set()
        while True:
            response = await run_graphql(
                _REQUESTS_QUERY,
                {
                    "query": f"repo:{self.owner}/{self.repo} is:pr review-requested:{self.viewer}",
                    "after": after,
                },
                runner=self.runner,
            )
            connection = mapping(mapping(response.get("data")).get("search"))
            nodes = connection.get("nodes")
            count = connection.get("issueCount")
            if not isinstance(nodes, list) or not isinstance(count, int):
                raise GraphQLRequestError("GitHub did not return review requests")
            for node in nodes:
                number = mapping(node).get("number")
                if not isinstance(number, int) or number <= 0:
                    raise GraphQLRequestError("GitHub returned an invalid requested PR")
                self.requested.add(number)
            after = self._next_cursor(connection, seen)
            if after is None or len(self.requested) >= 1000:
                self.requests_complete = len(self.requested) >= count
                if not self.requests_complete:
                    self.warnings.append(
                        "Partial review requests: GitHub search is limited to 1,000 results; "
                        "uncertain PRs are not classified as done."
                    )
                return

    async def _pages(
        self,
        *,
        states: list[str] | None = None,
        head: str | None = None,
        base: str | None = None,
        first_connection: object = None,
    ) -> AsyncIterator[list[PRListItem]]:
        after: str | None = None
        seen: set[str] = set()
        while True:
            if first_connection is not None:
                connection = mapping(first_connection)
                first_connection = None
            else:
                response = await run_graphql(
                    _LIST_QUERY,
                    {
                        "owner": self.owner,
                        "repo": self.repo,
                        "viewer": self.viewer,
                        "states": states,
                        "head": head,
                        "base": base,
                        "after": after,
                    },
                    runner=self.runner,
                )
                repository = mapping(mapping(response.get("data")).get("repository"))
                connection = mapping(repository.get("pullRequests"))
            nodes = connection.get("nodes")
            if not isinstance(nodes, list):
                raise GraphQLRequestError("GitHub did not return a PR connection")
            yield [self._parse_item(node) for node in nodes]
            after = self._next_cursor(connection, seen)
            if after is None:
                return

    def _parse_item(self, node: object) -> PRListItem:
        item = PRListItem.model_validate(node)
        if item.repository.casefold() != f"{self.owner}/{self.repo}".casefold():
            raise GraphQLRequestError(
                "GitHub returned a PR from a different repository"
            )
        return item

    def _with_request(self, item: PRListItem) -> PRListItem:
        requested: bool | None = (
            item.number in self.requested and item.request_count > 0
        )
        if (
            not self.requests_complete
            and item.number not in self.requested
            and item.request_count
        ):
            requested = None
        return item.model_copy(update={"requested": requested})

    async def _load_context(self, now: datetime) -> None:
        pending = [
            item.number
            for item in self._snapshot().items
            if item.group("review", self.viewer, now)
            or item.group("created", self.viewer, now)
        ]
        visited: set[int] = set()
        branches: set[tuple[str, str]] = set()
        while pending:
            lookups: dict[tuple[str, str], None] = {}
            for number in pending:
                if number in visited:
                    continue
                visited.add(number)
                item = self.items[number]
                if item.base_ref and item.base_ref != self.default_branch:
                    lookups["head", item.base_ref] = None
                if (
                    item.head_ref
                    and item.head_repository
                    and item.head_repository.casefold() == item.repository.casefold()
                ):
                    lookups["base", item.head_ref] = None
            pending = []
            for batch in batched(
                (key for key in lookups if key not in branches), _BRANCH_BATCH_SIZE
            ):
                branches.update(batch)
                async for related in self._branch_batch(batch):
                    self.items[related.number] = related
                    pending.append(related.number)

    async def _branch_batch(
        self, batch: tuple[tuple[str, str], ...]
    ) -> AsyncIterator[PRListItem]:
        variables = {"owner": self.owner, "repo": self.repo, "viewer": self.viewer}
        arguments = ["$owner: String!", "$repo: String!", "$viewer: String!"]
        fields: list[str] = []
        for i, (kind, branch) in enumerate(batch):
            variables[f"branch{i}"] = branch
            arguments.append(f"$branch{i}: String!")
            fields.append(
                f"pr{i}: pullRequests(first: 100, {kind}RefName: $branch{i}, "
                "orderBy: {field: UPDATED_AT, direction: DESC}) {" + _CONNECTION + "}"
            )
        query = (
            "query("
            + ", ".join(arguments)
            + ") { repository(owner: $owner, name: $repo) {"
            + "\n".join(fields)
            + "} }"
        )
        try:
            response = await run_graphql(query, variables, runner=self.runner)
            repository = mapping(mapping(response.get("data")).get("repository"))
        except ValueError, RuntimeError:
            # gh rejects the whole response even when only one alias failed.
            # Retry branches individually so healthy siblings and later pages survive.
            repository = {}
        for i, (kind, branch) in enumerate(batch):
            for item in await self._load_branch(kind, branch, repository.get(f"pr{i}")):
                yield item

    async def _load_branch(
        self, kind: str, branch: str, first_connection: object
    ) -> list[PRListItem]:
        items: list[PRListItem] = []
        try:
            async for page in self._pages(
                head=branch if kind == "head" else None,
                base=branch if kind == "base" else None,
                first_connection=first_connection,
            ):
                items.extend(
                    item
                    for item in page
                    if kind != "head"
                    or (item.head_repository or "").casefold()
                    == f"{self.owner}/{self.repo}".casefold()
                )
        except (ValueError, RuntimeError) as error:
            self.warnings.append(f"Partial stack context ({kind}:{branch}): {error}")
        return items
