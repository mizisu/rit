"""Overview queries and snapshots must stay outside the diff workspace."""

import asyncio
import json

import pytest

from rit.services.github import GitHubError, GitHubService
from rit.services.pr_checks import fetch_pr_checks, parse_pr_check
from rit.state.models import PR, LoadingState, PRFile
from rit.state.pr_overview import (
    CheckOutcome,
    FileChangeGroup,
    PRCheck,
    PRChecksSnapshot,
    PRFileMetadata,
)
from rit.state.store import PRStore
from rit.ui.components.pr_overview import PRChecks


@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("src/app.py", "Implementation"),
        (".github/workflows/ci.yml", "Implementation"),
        ("tests/test_app.py", "Tests"),
        ("src/app.test.ts", "Tests"),
        ("src/__tests__/README.md", "Tests"),
        ("docs/guide.md", "Documentation"),
        ("README", "Documentation"),
        ("uv.lock", "Generated"),
        ("tests/client.generated.ts", "Generated"),
        ("src/net/url_test.go", "Tests"),
        ("src/service/user_spec.rb", "Tests"),
        ("src/testdata/request.json", "Tests"),
        ("spec/feature_spec.rb", "Tests"),
        ("src/lock/snapshots/lock__tests__missing.snap", "Tests"),
        ("src/main/UserTest.java", "Tests"),
        ("src/main/UserTests.kt", "Tests"),
        ("src/main/UserSpec.scala", "Tests"),
        ("src/main/Contest.java", "Implementation"),
        ("src/user.d.ts", "Implementation"),
        ("assets/logo.png", "Implementation"),
        ("docs/assets/logo.png", "Documentation"),
        ("LICENSE.txt", "Documentation"),
        ("CHANGELOG", "Documentation"),
        ("CONTRIBUTING.rst", "Documentation"),
        ("SECURITY.md", "Documentation"),
        ("go.sum", "Generated"),
        ("Cargo.lock", "Generated"),
        ("src/api.pb.go", "Generated"),
        ("src/api_pb2.py", "Generated"),
        ("src/api_pb2_grpc.py", "Generated"),
        ("src/models.g.dart", "Generated"),
        ("src/models.g.cs", "Generated"),
        ("src/zz_generated.deepcopy.go", "Generated"),
    ],
)
def test_file_categories(path: str, category: str) -> None:
    assert FileChangeGroup.category(path) == category


@pytest.mark.parametrize(
    ("paths", "labels"),
    [
        (("src/app.py", "src/utils.py"), ("app.py", "utils.py")),
        (
            ("pkg_a/config.py", "pkg_b/config.py", "unique.py"),
            ("pkg_a/config.py", "pkg_b/config.py", "unique.py"),
        ),
        (
            ("config.py", "a/config.py", "x/a/config.py"),
            ("config.py", "a/config.py", "x/a/config.py"),
        ),
        (
            ("src/a/nested/app.py", "src/b/nested/app.py", "src/한글.py"),
            ("a/nested/app.py", "b/nested/app.py", "한글.py"),
        ),
    ],
)
def test_file_labels_use_the_shortest_distinguishing_path_suffix(
    paths: tuple[str, ...], labels: tuple[str, ...]
) -> None:
    files = tuple(PRFile(filename=path) for path in paths)
    group = FileChangeGroup.categorize(files)[0]
    assert group.display_paths == labels
    assert len(set(group.display_paths)) == len(files)
    assert tuple(file.filename for file in group.files) == paths


def test_file_groups_preserve_order_and_count_each_file_once() -> None:
    files = tuple(
        PRFile(filename=name, additions=index, deletions=1)
        for index, name in enumerate(
            ("src/z.py", "tests/test_app.py", "src/a.py", "docs/guide.md", "uv.lock"),
            start=1,
        )
    )
    groups = FileChangeGroup.categorize(files)
    assert tuple(group.name for group in groups) == FileChangeGroup.CATEGORIES
    assert [group.name for group in groups] == [
        "Implementation",
        "Tests",
        "Documentation",
        "Generated",
    ]
    assert [file.filename for file in groups[0].files] == ["src/z.py", "src/a.py"]
    assert sum(len(group.files) for group in groups) == len(files)
    assert sum(group.additions for group in groups) == 15
    assert sum(group.deletions for group in groups) == 5


@pytest.mark.parametrize(
    ("status", "conclusion", "outcome"),
    [
        ("IN_PROGRESS", None, "pending"),
        ("QUEUED", "SUCCESS", "pending"),
        ("COMPLETED", "SUCCESS", "success"),
        ("COMPLETED", "FAILURE", "failure"),
        ("COMPLETED", "TIMED_OUT", "failure"),
        ("COMPLETED", "ACTION_REQUIRED", "failure"),
        ("COMPLETED", "CANCELLED", "cancelled"),
        ("COMPLETED", "STALE", "unknown"),
        ("COMPLETED", "SKIPPED", "neutral"),
        ("COMPLETED", "NEUTRAL", "neutral"),
        ("COMPLETED", None, "unknown"),
        ("COMPLETED", "NEW_RESULT", "unknown"),
        ("NEW_STATUS", None, "unknown"),
    ],
)
def test_check_outcomes_do_not_promote_unknown_or_cancelled_results(
    status: str,
    conclusion: str | None,
    outcome: CheckOutcome,
) -> None:
    check = parse_pr_check(
        {
            "__typename": "CheckRun",
            "id": "check",
            "name": "CI",
            "status": status,
            "conclusion": conclusion,
        }
    )
    assert check.outcome == outcome
    snapshot = PRChecksSnapshot("head", "SUCCESS", (check,))
    assert snapshot.outcome == outcome
    assert ("All passed" in PRChecks.summary(snapshot).plain) == (outcome == "success")


@pytest.mark.parametrize(
    ("state", "outcomes", "expected"),
    [
        ("SUCCESS", (), None),
        ("SUCCESS", ("success", "success"), "success"),
        ("SUCCESS", ("success", "neutral"), "success"),
        ("SUCCESS", ("neutral",), "neutral"),
        ("SUCCESS", ("neutral", "neutral"), "neutral"),
        ("SUCCESS", ("success", "cancelled"), "cancelled"),
        ("SUCCESS", ("cancelled", "neutral"), "cancelled"),
        ("SUCCESS", ("cancelled", "unknown"), "unknown"),
        ("SUCCESS", ("cancelled", "pending"), "pending"),
        ("SUCCESS", ("cancelled", "failure"), "failure"),
        ("SUCCESS", ("success", "pending"), "pending"),
        ("PENDING", ("failure", "pending"), "failure"),
        ("FAILURE", ("success",), "failure"),
        ("PENDING", ("success",), "pending"),
        ("SUCCESS", ("success", "unknown"), "unknown"),
        (None, ("success",), "unknown"),
        ("FUTURE_STATE", ("success",), "unknown"),
    ],
)
def test_check_rollup_policy_is_independent_of_ui_rendering(
    state: str | None,
    outcomes: tuple[CheckOutcome, ...],
    expected: CheckOutcome | None,
) -> None:
    checks = tuple(
        PRCheck(str(index), "CI", outcome, "result")
        for index, outcome in enumerate(outcomes)
    )
    assert PRChecksSnapshot("head", state, checks).outcome == expected


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        ("SUCCESS", "success"),
        ("PENDING", "pending"),
        ("EXPECTED", "pending"),
        ("FAILURE", "failure"),
        ("ERROR", "failure"),
        ("FUTURE_STATE", "unknown"),
    ],
)
def test_legacy_commit_status_normalization(state: str, outcome: CheckOutcome) -> None:
    check = parse_pr_check(
        {
            "__typename": "StatusContext",
            "id": "legacy",
            "context": "deploy",
            "state": state,
        }
    )
    assert check.outcome == outcome
    assert check.detail == state


async def test_checks_paginate_mixed_contexts_and_reject_broken_responses() -> None:
    cursors: list[str | None] = []

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        assert input_text is not None
        payload = json.loads(input_text)
        assert payload["variables"]["head"] == "head"
        after = payload["variables"]["after"]
        cursors.append(after)
        node = (
            {
                "__typename": "CheckRun",
                "id": "run",
                "name": "CI",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/check",
            }
            if after is None
            else {
                "__typename": "StatusContext",
                "id": "status",
                "context": "deploy",
                "state": "PENDING",
                "targetUrl": "https://example.com/deploy",
            }
        )
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "object": {
                            "oid": "head",
                            "statusCheckRollup": {
                                "state": "PENDING",
                                "contexts": {
                                    "nodes": [node],
                                    "pageInfo": {
                                        "hasNextPage": after is None,
                                        "endCursor": "next",
                                    },
                                },
                            },
                        }
                    }
                }
            }
        )

    snapshot = await fetch_pr_checks("owner", "repo", "head", runner=runner)
    assert cursors == [None, "next"]
    assert [check.outcome for check in snapshot.checks] == ["success", "pending"]
    assert snapshot.checks[1].url == "https://example.com/deploy"

    async def no_checks(args: list[str], *, input_text: str | None = None) -> str:
        return json.dumps(
            {
                "data": {
                    "repository": {"object": {"oid": "head", "statusCheckRollup": None}}
                }
            }
        )

    empty = await fetch_pr_checks("owner", "repo", "head", runner=no_checks)
    assert empty.state is None and not empty.checks
    assert "All passed" not in PRChecks.summary(empty).plain

    async def broken(args: list[str], *, input_text: str | None = None) -> str:
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "object": {
                            "oid": "head",
                            "statusCheckRollup": {
                                "state": "SUCCESS",
                                "contexts": {
                                    "nodes": [],
                                    "pageInfo": {
                                        "hasNextPage": True,
                                        "endCursor": "same",
                                    },
                                },
                            },
                        }
                    }
                }
            }
        )

    with pytest.raises(ValueError, match="pagination"):
        await fetch_pr_checks("owner", "repo", "head", runner=broken)

    async def missing(args: list[str], *, input_text: str | None = None) -> str:
        return '{"data":{"repository":{"object":null}}}'

    with pytest.raises(ValueError, match="head commit"):
        await fetch_pr_checks("owner", "repo", "head", runner=missing)


@pytest.mark.parametrize("changed_ref", [None, "baseRefOid", "headRefOid"])
async def test_metadata_requests_are_shared_and_validated_before_patch_reuse(
    monkeypatch: pytest.MonkeyPatch,
    changed_ref: str | None,
) -> None:
    service = GitHubService(owner="owner", repo="repo")
    calls: list[int] = []
    change_refs = False
    files = [
        {
            "path": f"file-{index}.py",
            "changeType": "MODIFIED",
            "additions": 1,
            "deletions": 1,
        }
        for index in range(201)
    ]
    patch = "".join(
        f"diff --git a/file-{index}.py b/file-{index}.py\n--- a/file-{index}.py\n+++ b/file-{index}.py\n@@ -1 +1 @@\n-old\n+new\n"
        for index in range(len(files))
    )

    async def runner(args: list[str], *, input_text: str | None = None) -> str:
        if args[1] != "graphql":
            return patch
        assert input_text is not None
        variables = json.loads(input_text)["variables"]
        first = variables["first"]
        calls.append(first)
        start = int(variables["after"] or 0)
        pr = {
            "baseRefOid": "base",
            "headRefOid": "head",
            "files": {
                "nodes": files[start : start + first],
                "pageInfo": {
                    "hasNextPage": start + first < len(files),
                    "endCursor": str(start + first),
                },
            },
        }
        if change_refs and changed_ref:
            pr[changed_ref] = "changed"
        return json.dumps({"data": {"repository": {"pullRequest": pr}}})

    monkeypatch.setattr(service, "_run_gh", runner)
    first, shared = await asyncio.gather(
        service.get_pr_file_metadata(1), service.get_pr_file_metadata(1)
    )
    assert first is shared
    assert calls == [100, 100, 100]
    assert all(not file.patch for file in first.files)
    change_refs = True
    patched = await service.get_pr_files(1)
    assert calls == (
        [100, 100, 100, 1] if changed_ref is None else [100, 100, 100, 1, 100, 100, 100]
    )
    assert len(patched) == 201 and all(file.patch for file in patched)
    assert all(not file.patch for file in first.files)
    assert first.files[0] is not patched[0]
    service._pr_revision = (1, "stale-base", "stale-head")
    with pytest.raises(GitHubError, match="PR changed"):
        await service.get_pr_files(1)


async def test_sidebar_loads_once_without_files_messages_or_workspace_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore(pr_number=1)
    store.state.pr = PR(number=1, base_sha="base", head_sha="head")
    file = PRFile(filename="src/app.py", additions=1)
    calls: list[str] = []
    messages = []
    store.set_message_sink(messages.append)

    async def checks(head: str) -> PRChecksSnapshot:
        calls.append("checks")
        return PRChecksSnapshot(head, None)

    async def metadata(_number: int, **_kwargs: object) -> PRFileMetadata:
        calls.append("files")
        return PRFileMetadata("base", "head", (file,))

    monkeypatch.setattr(store._service, "get_pr_checks", checks)
    monkeypatch.setattr(store._service, "get_pr_file_metadata", metadata)
    await asyncio.gather(store.load_pr_overview(), store.load_pr_overview())
    await store.load_pr_overview()
    assert sorted(calls) == ["checks", "files"]
    summary = store.state.file_summary.loaded_value
    assert summary is not None and summary.groups[0].additions == 1
    assert store.state.files == [] and store.state.file_diffs == {}
    assert store.state.files_loading == LoadingState.IDLE
    assert all(isinstance(message, PRStore.OverviewUpdated) for message in messages)
    await store.load_pr_checks(refresh=True)
    assert calls.count("checks") == 2


async def test_sidebar_snapshots_must_match_the_requested_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore(pr_number=1)
    store.state.pr = PR(number=1, base_sha="base", head_sha="head")

    async def checks(_head: str) -> PRChecksSnapshot:
        return PRChecksSnapshot("other", None)

    async def metadata(_number: int, **_kwargs: object) -> PRFileMetadata:
        return PRFileMetadata("base", "other", ())

    monkeypatch.setattr(store._service, "get_pr_checks", checks)
    monkeypatch.setattr(store._service, "get_pr_file_metadata", metadata)
    await store.load_pr_overview()
    assert store.state.checks.loading == LoadingState.ERROR
    assert store.state.file_summary.loading == LoadingState.ERROR
    assert store.state.checks.loaded_value is None
    assert store.state.file_summary.loaded_value is None
    assert store.state.files_loading == LoadingState.IDLE
    assert store.state.error is None


async def test_superseded_sidebar_responses_and_failures_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore(pr_number=1)
    store.state.pr = PR(number=1, base_sha="base", head_sha="old")
    started = asyncio.Event()
    release = asyncio.Event()

    async def checks(head: str) -> PRChecksSnapshot:
        if head == "old":
            started.set()
            await release.wait()
        return PRChecksSnapshot(head, None)

    async def metadata(_number: int, **_kwargs: object) -> PRFileMetadata:
        assert store.state.pr is not None
        head = store.state.pr.head_sha
        if head == "old":
            await release.wait()
        return PRFileMetadata("base", head, ())

    monkeypatch.setattr(store._service, "get_pr_checks", checks)
    monkeypatch.setattr(store._service, "get_pr_file_metadata", metadata)
    old = asyncio.create_task(store.load_pr_overview())
    await started.wait()
    store.state.pr = PR(number=1, base_sha="base", head_sha="new")
    await store.load_pr_overview()
    release.set()
    await old
    checks_snapshot = store.state.checks.loaded_value
    files_snapshot = store.state.file_summary.loaded_value
    assert checks_snapshot is not None and checks_snapshot.head_sha == "new"
    assert files_snapshot is not None and files_snapshot.metadata.head_sha == "new"

    async def failure(_head: str) -> PRChecksSnapshot:
        raise GitHubError("permission denied")

    monkeypatch.setattr(store._service, "get_pr_checks", failure)
    await store.load_pr_checks(refresh=True)
    assert store.state.checks.loading == LoadingState.ERROR
    assert store.state.checks.error == "permission denied"
    assert store.state.file_summary.loading == LoadingState.LOADED
    assert store.state.error is None
