from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from rit.state.pr_list import PRListItem, PRListSnapshot

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def item(number: int, **changes: object) -> PRListItem:
    data: dict[str, object] = {
        "number": number,
        "title": f"PR {number}",
        "author": "other",
        "repository": "org/repo",
        "head_repository": "org/repo",
        "base_ref": "main",
        "head_ref": f"branch-{number}",
        "state": "OPEN",
        "draft": False,
        "created_at": NOW - timedelta(days=number),
        "updated_at": NOW,
        "closed_at": None,
        "review_count": 0,
        "request_count": 0,
    }
    data.update(changes)
    return PRListItem.model_validate(data)


def snapshot(*items: PRListItem) -> PRListSnapshot:
    return PRListSnapshot("org", "repo", "viewer", items)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, None),
        ({"requested": True}, "Needs review"),
        ({"review_count": 1}, "Done"),
        ({"review_count": 1, "requested": True}, "Needs review"),
        ({"review_count": 1, "requested": None}, None),
        ({"author": "VIEWER", "requested": True}, None),
        ({"draft": True, "requested": True}, "Needs review"),
        ({"state": "MERGED", "closed_at": NOW, "requested": True}, "Done"),
        (
            {
                "state": "CLOSED",
                "closed_at": NOW - timedelta(days=7),
                "review_count": 1,
            },
            "Done",
        ),
        (
            {
                "state": "CLOSED",
                "closed_at": NOW - timedelta(days=7, seconds=1),
                "review_count": 1,
            },
            None,
        ),
    ],
)
def test_review_membership_and_classification(
    changes: dict, expected: str | None
) -> None:
    assert item(1, **changes).group("review", "viewer", NOW) == expected


@pytest.mark.parametrize(
    ("state", "draft", "expected"),
    [
        ("OPEN", False, "Open"),
        ("OPEN", True, "Draft"),
        ("MERGED", False, "Done"),
        ("CLOSED", False, "Done"),
    ],
)
def test_created_groups(state: str, draft: bool, expected: str) -> None:
    pr = item(1, author="Viewer", state=state, draft=draft, closed_at=NOW)
    assert pr.group("created", "viewer", NOW) == expected
    assert pr.group("review", "viewer", NOW) is None


def test_stacks_preserve_context_without_counting_or_sorting_by_it() -> None:
    parent = item(
        50, author="viewer", state="MERGED", closed_at=NOW - timedelta(days=20)
    )
    child = item(2, base_ref=parent.head_ref, review_count=1)
    grandchild = item(3, base_ref=child.head_ref, requested=True)
    standalone = item(4, requested=True)
    stacks = snapshot(grandchild, standalone, parent, child).stacks("review", now=NOW)
    assert [stack.members[0].number for stack in stacks] == [4, 50]
    stack = stacks[1]
    assert [pr.number for pr in stack.members] == [50, 2, 3]
    assert stack.targets == {2, 3}
    assert stack.group == "Needs review"
    assert stack.created_at == grandchild.created_at


def test_created_stack_prefers_open_over_draft_and_done() -> None:
    prs = (
        item(1, author="viewer", draft=True),
        item(2, author="viewer", base_ref="branch-1"),
        item(3, author="viewer", base_ref="branch-2", state="CLOSED", closed_at=NOW),
    )
    stacks = snapshot(*prs).stacks("created", now=NOW)
    assert len(stacks) == 1
    assert stacks[0].group == "Open"
    assert stacks[0].targets == {1, 2, 3}


def test_fork_branches_and_ambiguous_parents_are_not_linked() -> None:
    child = item(3, base_ref="shared", requested=True)
    for parents in (
        (item(1, head_ref="shared", head_repository="fork/repo"),),
        (item(1, head_ref="shared"), item(2, head_ref="shared")),
    ):
        stacks = snapshot(*parents, child).stacks("review", now=NOW)
        assert len(stacks) == 1
        assert stacks[0].members == (child,)


def test_default_branch_is_a_boundary_not_a_stack_parent() -> None:
    data = snapshot(
        item(1, head_ref="main", base_ref="release", requested=True),
        item(2, requested=True),
    )
    stacks = replace(data, default_branch="main").stacks("review", now=NOW)
    assert len(stacks) == 2
    assert all(len(stack.members) == 1 for stack in stacks)


def test_cycles_are_not_presented_as_a_stack() -> None:
    first = item(1, base_ref="branch-2", requested=True)
    second = item(2, base_ref="branch-1", requested=True)
    stacks = snapshot(first, second).stacks("review", now=NOW)
    assert len(stacks) == 2
    assert all(len(stack.members) == 1 for stack in stacks)
