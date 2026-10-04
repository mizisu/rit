"""Lightweight PR overview snapshots, separate from the diff workspace."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import ClassVar, Literal

from rit.state.models import PRFile

type CheckOutcome = Literal[
    "success", "pending", "failure", "cancelled", "neutral", "unknown"
]


@dataclass(frozen=True)
class PRCheck:
    """One check run or legacy commit status."""

    node_id: str
    name: str
    outcome: CheckOutcome
    detail: str
    url: str = ""


@dataclass(frozen=True)
class PRChecksSnapshot:
    """Checks reported for one immutable head commit."""

    head_sha: str
    state: str | None
    checks: tuple[PRCheck, ...] = ()

    @property
    def outcome(self) -> CheckOutcome | None:
        """Combine the rollup and contexts without promoting missing results."""
        if not self.checks:
            return None
        outcomes = {check.outcome for check in self.checks}
        if self.state in {"FAILURE", "ERROR"} or "failure" in outcomes:
            return "failure"
        if self.state in {"PENDING", "EXPECTED"} or "pending" in outcomes:
            return "pending"
        if "unknown" in outcomes or self.state != "SUCCESS":
            return "unknown"
        if "cancelled" in outcomes:
            return "cancelled"
        return "success" if "success" in outcomes else "neutral"


@dataclass(frozen=True)
class PRFileMetadata:
    """Changed-file metadata without patches or parsed diffs."""

    base_sha: str
    head_sha: str
    files: tuple[PRFile, ...]


@dataclass(frozen=True)
class FileChangeGroup:
    """A path-based category and its change totals."""

    CATEGORIES: ClassVar[tuple[str, ...]] = (
        "Implementation",
        "Tests",
        "Documentation",
        "Generated",
    )

    name: str
    files: tuple[PRFile, ...]
    additions: int
    deletions: int
    display_paths: tuple[str, ...]

    @classmethod
    def categorize(cls, files: tuple[PRFile, ...]) -> tuple[FileChangeGroup, ...]:
        """Classify each file once, preserving GitHub's order within groups."""
        groups: dict[str, list[PRFile]] = {name: [] for name in cls.CATEGORIES}
        for file in files:
            groups[cls.category(file.filename)].append(file)
        return tuple(
            cls(
                name,
                tuple(members),
                sum(file.additions for file in members),
                sum(file.deletions for file in members),
                cls._display_paths(members),
            )
            for name, members in groups.items()
            if members
        )

    @staticmethod
    def _display_paths(files: list[PRFile]) -> tuple[str, ...]:
        parts = [file.filename.split("/") for file in files]
        labels = [path[-1] for path in parts]
        siblings: dict[str, list[int]] = defaultdict(list)
        for index, label in enumerate(labels):
            siblings[label].append(index)
        for indices in siblings.values():
            if len(indices) < 2:
                continue
            pending = set(indices)
            depth = 2
            while pending:
                for index in pending:
                    labels[index] = "/".join(parts[index][-depth:])
                counts = Counter(labels[index] for index in indices)
                pending = {
                    index
                    for index in pending
                    if counts[labels[index]] > 1 and depth < len(parts[index])
                }
                depth += 1
        return tuple(labels)

    @staticmethod
    def category(path: str) -> str:
        """Use conservative filename rules; generated status is heuristic."""
        parts = path.lower().split("/")
        name = parts[-1]
        if (
            name
            in {
                "uv.lock",
                "poetry.lock",
                "pdm.lock",
                "pnpm-lock.yaml",
                "package-lock.json",
                "yarn.lock",
                "cargo.lock",
                "gemfile.lock",
                "composer.lock",
                "bun.lock",
                "bun.lockb",
                "go.sum",
                "pipfile.lock",
                "npm-shrinkwrap.json",
            }
            or ".generated." in name
            or name.startswith("zz_generated.")
            or name.endswith(
                (
                    ".pb.go",
                    ".pb.cc",
                    ".pb.h",
                    "_pb2.py",
                    "_pb2_grpc.py",
                    ".g.dart",
                    ".g.cs",
                )
            )
        ):
            return "Generated"
        if (
            any(
                part
                in {
                    "test",
                    "tests",
                    "__tests__",
                    "spec",
                    "specs",
                    "testdata",
                    "snapshots",
                    "__snapshots__",
                }
                for part in parts[:-1]
            )
            or name.startswith("test_")
            or "_test." in name
            or "_spec." in name
            or ".test." in name
            or ".spec." in name
            or path.rsplit("/", 1)[-1].endswith(
                ("Test.java", "Tests.java", "Test.kt", "Tests.kt", "Spec.scala")
            )
        ):
            return "Tests"
        if (
            any(part in {"doc", "docs", "documentation"} for part in parts[:-1])
            or name.startswith(("readme", "changelog", "contributing"))
            or name.split(".", 1)[0]
            in {"license", "licence", "copying", "security", "code_of_conduct"}
            or name.endswith((".md", ".mdx", ".rst", ".adoc"))
        ):
            return "Documentation"
        return "Implementation"


@dataclass(frozen=True)
class PRFilesSnapshot:
    """Publish file metadata and its prepared groups as one consistent value."""

    metadata: PRFileMetadata
    groups: tuple[FileChangeGroup, ...]

    @classmethod
    def from_metadata(cls, metadata: PRFileMetadata) -> PRFilesSnapshot:
        """Prepare categories without loading patches or diffs."""
        return cls(metadata, FileChangeGroup.categorize(metadata.files))
