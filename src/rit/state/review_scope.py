"""Immutable comparison endpoints and commit selection rules."""

from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from rit.state.models import PRReview


class PRCommit(BaseModel):
    """A PR commit with its actual first parent, not its list neighbour."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    sha: str = Field(validation_alias="oid", pattern=r"^[0-9a-f]{40}$")
    title: str = Field(validation_alias="messageHeadline")
    parent_sha: str = Field(
        default="", validation_alias=AliasPath("parents", "nodes", 0, "oid")
    )
    parent_count: int = Field(
        default=0, validation_alias=AliasPath("parents", "totalCount")
    )


@dataclass(frozen=True)
class ReviewScope:
    """A selected diff, with frozen endpoints for non-PR comparisons."""

    kind: Literal["all", "since", "commit", "range"] = "all"
    base_sha: str = ""
    head_sha: str = ""
    title: str = ""
    commit_shas: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        if self.kind == "all":
            return "All changes"
        if self.kind == "since":
            return "Since last review"
        if self.kind == "commit":
            return f"Commit {self.head_sha[:7]}"
        return f"{len(self.commit_shas)} commits"

    @property
    def detail(self) -> str:
        if self.kind == "all":
            return "Full pull request"
        endpoints = f"{self.base_sha[:7]} → {self.head_sha[:7]}"
        return f"{self.title} · {endpoints} · Read-only"


@dataclass(frozen=True)
class ReviewHistory:
    """Complete commit metadata and the viewer's latest submitted review."""

    base_sha: str
    head_sha: str
    commits: tuple[PRCommit, ...]
    last_review: PRReview | None = None

    def since_review(self) -> ReviewScope:
        review = self.last_review
        if review is None:
            raise ValueError("No submitted review by you")
        if not review.commit_sha:
            raise ValueError("Your latest review has no available commit SHA")
        index = next((i for i, commit in enumerate(self.commits) if commit.sha == review.commit_sha), None)
        selected = self.commits[index + 1:] if index is not None else ()
        title = "Your last submitted review"
        if review.submitted_at is not None:
            title += f" · {review.submitted_at:%Y-%m-%d %H:%M %Z}"
        if any(commit.parent_count > 1 for commit in selected):
            title += " · Includes merge changes"
        return ReviewScope(
            "since", review.commit_sha, self.head_sha, title,
            tuple(commit.sha for commit in selected),
        )

    def select_commits(self, first: str, last: str | None = None) -> ReviewScope:
        indexes = {commit.sha: index for index, commit in enumerate(self.commits)}
        if first not in indexes or (last is not None and last not in indexes):
            raise ValueError(
                "Commit is no longer in this pull request; refresh commits"
            )
        start, end = sorted((indexes[first], indexes[last or first]))
        selected = self.commits[start : end + 1]
        first_commit, last_commit = self.commits[start], self.commits[end]
        if not first_commit.parent_sha:
            raise ValueError("This commit has no available parent to compare")
        if any(
            current.parent_sha != previous.sha
            for previous, current in pairwise(selected)
        ):
            raise ValueError("Select a contiguous first-parent range")
        title = first_commit.title
        if len(selected) > 1:
            title = f"{first_commit.sha[:7]} … {last_commit.sha[:7]}"
        if any(commit.parent_count > 1 for commit in selected):
            title += " · Includes merge changes (first parent)"
        return ReviewScope(
            "commit" if len(selected) == 1 else "range",
            first_commit.parent_sha,
            last_commit.sha,
            title,
            tuple(commit.sha for commit in selected),
        )

    def adjacent(self, scope: ReviewScope, direction: int) -> ReviewScope | None:
        if scope.kind != "commit":
            return None
        for index, commit in enumerate(self.commits):
            if commit.sha == scope.head_sha:
                target = index + direction
                if 0 <= target < len(self.commits):
                    return self.select_commits(self.commits[target].sha)
                break
        return None
