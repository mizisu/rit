"""Tests for DiffView planning helpers."""

import pytest

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
import rit.ui.widgets.diff_plan as diff_plan_module
from rit.ui.widgets.diff_plan import build_diff_plan


def test_build_diff_plan_indexes_lines_and_rows() -> None:
    patch = """@@ -10,3 +10,3 @@
 context
-old value
+new value
 tail"""
    diff = parse_patch(patch, "test.py")

    plan = build_diff_plan(diff)

    assert len(plan.all_lines) == 3
    assert plan.line_index_by_old_number == {10: 0, 11: 1, 12: 2}
    assert plan.line_index_by_new_number == {10: 0, 11: 1, 12: 2}
    assert plan.hunk_index_by_line == [0, 0, 0]
    assert plan.hunk_line_ranges == [(0, 0, 2)]
    assert plan.modified_line_count == 1
    assert plan.file_change_stats == {"test.py": (1, 1)}

    unified_rows = plan.rendered_rows.rows_unified
    assert [row.side for row in unified_rows] == ["auto", "old", "new", "auto"]
    assert [row.anchor_id for row in unified_rows] == [
        "line-0",
        "line-1-old",
        "line-1-new",
        "line-2",
    ]
    assert plan.rendered_rows.row_lookup_unified[(1, "old")] == 1
    assert plan.rendered_rows.row_lookup_unified[(1, "new")] == 2
    assert plan.rendered_rows.row_lookup_split == {0: 0, 1: 1, 2: 2}


def test_build_rendered_rows_reuses_non_modified_row_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff = FileDiff(
        filename="test.py",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(1, 1, old_content="same", new_content="same")],
            )
        ],
    )
    diff.hunks[0].lines[0].line_index = 0
    calls = 0
    real_row_kind_for_line = diff_plan_module._row_kind_for_line

    def row_kind_for_line(
        line: DiffLine,
        *,
        modified_side: str | None = None,
    ) -> str:
        nonlocal calls
        calls += 1
        return real_row_kind_for_line(line, modified_side=modified_side)

    monkeypatch.setattr(diff_plan_module, "_row_kind_for_line", row_kind_for_line)

    rows = diff_plan_module.build_rendered_rows(diff)

    assert calls == 1
    assert rows.rows_unified[0].kind == "context"
    assert rows.rows_split[0].kind == "context"


def test_build_diff_plan_builds_rendered_rows_during_single_hunk_pass() -> None:
    class SinglePassHunks(list[DiffHunk]):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError(
                    "diff planning should not scan hunks again for rendered rows"
                )
            return super().__iter__()

    diff = FileDiff(
        filename="test.py",
        hunks=SinglePassHunks(
            [
                DiffHunk(
                    old_start=1,
                    old_count=2,
                    new_start=1,
                    new_count=2,
                    lines=[
                        DiffLine(1, 1, old_content="same", new_content="same"),
                        DiffLine(
                            2,
                            2,
                            old_content="old",
                            new_content="new",
                            is_modified=True,
                        ),
                    ],
                )
            ]
        ),
    )

    plan = build_diff_plan(diff)

    assert diff.hunks.iterations == 1
    assert [row.anchor_id for row in plan.rendered_rows.rows_unified] == [
        "line-0",
        "line-1-old",
        "line-1-new",
    ]
    assert [row.anchor_id for row in plan.rendered_rows.rows_split] == [
        "line-0",
        "line-1",
    ]


def test_build_diff_plan_tracks_line_number_widths() -> None:
    diff = FileDiff(
        filename="test.py",
        hunks=[
            DiffHunk(
                old_start=98,
                old_count=3,
                new_start=998,
                new_count=3,
                lines=[
                    DiffLine(98, 998, old_content="a", new_content="a"),
                    DiffLine(100, 1000, old_content="b", new_content="b"),
                ],
            )
        ],
    )

    plan = build_diff_plan(diff)

    assert plan.old_line_number_width == 3
    assert plan.new_line_number_width == 4
