"""Tests for DiffView planning helpers."""

from rit.core.diff import parse_patch
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
