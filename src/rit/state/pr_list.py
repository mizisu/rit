"""PR inbox data and branch-linked stack projection."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import AliasPath, AwareDatetime, BaseModel, ConfigDict, Field

PRListTab = Literal["review", "created"]


class PRListItem(BaseModel):
    """The lightweight fields needed to select a PR, without loading its diff."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    number: int = Field(gt=0)
    title: str
    author: str = Field(validation_alias=AliasPath("author", "login"), default="ghost")
    repository: str = Field(
        validation_alias=AliasPath("baseRepository", "nameWithOwner")
    )
    head_repository: str | None = Field(
        validation_alias=AliasPath("headRepository", "nameWithOwner"), default=None
    )
    base_ref: str = Field(alias="baseRefName")
    head_ref: str = Field(alias="headRefName")
    state: Literal["OPEN", "CLOSED", "MERGED"]
    draft: bool = Field(alias="isDraft")
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")
    closed_at: AwareDatetime | None = Field(alias="closedAt")
    review_count: int = Field(validation_alias=AliasPath("reviews", "totalCount"), ge=0)
    request_count: int = Field(
        validation_alias=AliasPath("reviewRequests", "totalCount"), ge=0
    )
    checks: str | None = Field(
        validation_alias=AliasPath(
            "commits", "nodes", 0, "commit", "statusCheckRollup", "state"
        ),
        default=None,
    )
    requested: bool | None = False

    def group(self, tab: PRListTab, viewer: str, now: datetime) -> str | None:
        """Classify a target; context-only and uncertain entries have no group."""
        if self.state != "OPEN" and (
            self.closed_at is None or self.closed_at < now - timedelta(days=7)
        ):
            return None
        if tab == "created":
            if self.author.casefold() != viewer.casefold():
                return None
            return "Done" if self.state != "OPEN" else "Draft" if self.draft else "Open"
        if self.author.casefold() == viewer.casefold():
            return None
        if self.state == "OPEN" and self.requested is None:
            return None
        if not self.requested and not self.review_count:
            return None
        return "Needs review" if self.state == "OPEN" and self.requested else "Done"


@dataclass(frozen=True)
class PRStack:
    """One connected branch component, including non-target context."""

    members: tuple[PRListItem, ...]
    targets: frozenset[int]
    group: str

    @property
    def created_at(self) -> datetime:
        return min(
            item.created_at for item in self.members if item.number in self.targets
        )


@dataclass(frozen=True)
class PRListSnapshot:
    """Repository-scoped inbox, with explicit completeness warnings."""

    owner: str
    repo: str
    viewer: str
    items: tuple[PRListItem, ...]
    warnings: tuple[str, ...] = ()
    default_branch: str = ""
    context_complete: bool = True
    active_preview: bool = False

    def stacks(self, tab: PRListTab, *, now: datetime | None = None) -> list[PRStack]:
        now = now or datetime.now(UTC)
        items = {item.number: item for item in self.items}
        heads: dict[tuple[str, str], list[int]] = {}
        for item in items.values():
            if item.head_repository and item.head_ref:
                key = (item.head_repository.casefold(), item.head_ref)
                heads.setdefault(key, []).append(item.number)
        parents: dict[int, int] = {}
        for item in items.values():
            if item.base_ref == self.default_branch:
                continue
            candidates = heads.get((item.repository.casefold(), item.base_ref), [])
            if len(candidates) == 1 and candidates[0] != item.number:
                parents[item.number] = candidates[0]
        for number in tuple(parents):
            path: set[int] = set()
            current = number
            while current in parents:
                if current in path:
                    for member in path:
                        parents.pop(member, None)
                    break
                path.add(current)
                current = parents[current]
        children: dict[int, list[int]] = {}
        for child, parent in parents.items():
            children.setdefault(parent, []).append(child)
        priorities = (
            ("Needs review", "Done") if tab == "review" else ("Open", "Draft", "Done")
        )
        stacks: list[PRStack] = []
        for root in items:
            if root in parents:
                continue
            members: list[PRListItem] = []
            pending = [root]
            while pending:
                number = pending.pop()
                members.append(items[number])
                pending.extend(
                    sorted(
                        children.get(number, []),
                        key=lambda key: (items[key].created_at, key),
                        reverse=True,
                    )
                )
            groups = {
                item.number: group
                for item in members
                if (group := item.group(tab, self.viewer, now)) is not None
            }
            if groups:
                group = min(groups.values(), key=priorities.index)
                stacks.append(PRStack(tuple(members), frozenset(groups), group))
        return sorted(
            stacks, key=lambda stack: (stack.created_at, stack.members[0].number)
        )
