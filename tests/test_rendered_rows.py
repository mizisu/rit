"""Packed row snapshots retain navigation semantics without whole-document views."""

import gc
import weakref

import pytest

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.ui.widgets.diff_plan import RenderedRows, build_rendered_rows_from_lines
from rit.ui.widgets.diff_plan_cache import DiffPlanCache
from rit.ui.widgets.diff_search_match_index import build_matches_from_rows
from rit.ui.widgets.diff_types import RenderedRow


def make_diff() -> FileDiff:
    return FileDiff(
        "sample.py",
        hunks=[
            DiffHunk(
                1,
                3,
                1,
                3,
                lines=[
                    DiffLine(1, 1, "same needle", "same needle"),
                    DiffLine(2, None, "old needle", "", is_deleted=True),
                    DiffLine(None, 2, "", "new needle", is_added=True),
                    DiffLine(3, 3, "before needle", "after needle", is_modified=True),
                ],
            ),
            DiffHunk(10, 1, 10, 1, lines=[DiffLine(10, 10, "tail", "tail")]),
        ],
    )


@pytest.mark.parametrize("split", [False, True])
def test_packed_rows_preserve_sequence_and_snapshot_semantics(split: bool) -> None:
    diff = make_diff()
    cache = DiffPlanCache(diff)
    projection = cache.prepare(diff)
    plan = projection.build_rows(split=split)
    rows = plan.rows_split if split else plan.rows_unified
    expected = list(rows)
    assert isinstance(rows, RenderedRows)
    assert rows == expected
    assert expected == rows
    assert rows[-1] == expected[-1]
    assert rows[::-1] == expected[::-1]
    assert rows[1::2] == expected[1::2]
    assert rows[100:] == []
    for index in (len(rows), -len(rows) - 1):
        with pytest.raises(IndexError):
            rows[index]
    with pytest.raises(AttributeError):
        object.__setattr__(rows[0], "line_index", 999)

    diff.hunks[0].lines[0].old_line_no = 2**74
    diff.hunks[0].lines[0].is_deleted = True
    RenderedRows._row.cache_clear()
    assert list(rows) == expected
    updated = cache.prepare(diff).build_rows(split=split)
    updated_rows = updated.rows_split if split else updated.rows_unified
    assert updated_rows[0].old_line_no == 2**74
    assert updated_rows[0].kind == "deleted"
    assert list(rows) == expected


def test_packed_rows_bound_attribute_views_and_copy_primitive_records() -> None:
    rows = RenderedRows()
    for index in range(1000):
        rows.append(
            RenderedRow(
                "unified",
                index,
                index,
                0,
                "context",
                "auto",
                f"line-{index}",
                index,
                index,
            )
        )
    assert all(type(data) is tuple for data in rows.iter_data())
    RenderedRows._row.cache_clear()
    for index in range(len(rows)):
        assert rows[index].line_index == index
    assert RenderedRows._row.cache_info().currsize == 256
    assert rows[998] is rows[998]

    copied = RenderedRows()
    copied.extend(rows)
    assert copied == rows
    assert copied[0] is rows[0]
    copied.extend(rows[:2])
    assert len(rows) == 1000
    assert len(copied) == 1002
    assert copied[-2:] == rows[:2]


@pytest.mark.parametrize("split", [False, True])
def test_search_and_hunk_join_do_not_materialize_row_views(
    split: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    diff = make_diff()
    projection = DiffPlanCache(diff).prepare(diff)
    plan = projection.build_rows(split=split)
    rows = plan.rows_split if split else plan.rows_unified
    expected = build_matches_from_rows(projection.plan.all_lines, list(rows), "needle")
    assert len(expected) == 5

    def reject_materialization(*_args: object) -> RenderedRow:
        raise AssertionError("bulk row operations must not materialize attribute views")

    monkeypatch.setattr(RenderedRows, "_row", staticmethod(reject_materialization))
    fresh = DiffPlanCache(diff).prepare(diff)
    fresh_plan = fresh.build_rows(split=split)
    fresh_rows = fresh_plan.rows_split if split else fresh_plan.rows_unified
    assert (
        build_matches_from_rows(fresh.plan.all_lines, fresh_rows, "needle") == expected
    )
    assert fresh_rows == rows


def test_packed_rows_and_attribute_cache_do_not_retain_source_documents() -> None:
    diff = make_diff()
    source_refs = [weakref.ref(diff), weakref.ref(diff.hunks[0].lines[0])]
    rows = DiffPlanCache(diff).prepare(diff).build_rows(split=False).rows_unified
    assert rows[0].line_index == 0
    del diff
    gc.collect()
    assert all(reference() is None for reference in source_refs)
    assert rows[0].line_index == 0


def test_packed_rows_preserve_large_offsets_and_line_numbers() -> None:
    offset = 2**70
    line = DiffLine(offset, offset + 1, "old", "new", is_modified=True)
    plan = build_rendered_rows_from_lines(
        [line],
        [7],
        split=False,
        line_offset=offset,
        row_offset=offset + 5,
    )
    assert list(plan.rows_unified) == [
        RenderedRow(
            "unified",
            offset + 5,
            offset,
            7,
            "modified-old",
            "old",
            f"line-{offset}-old",
            offset,
            offset + 1,
        ),
        RenderedRow(
            "unified",
            offset + 6,
            offset,
            7,
            "modified-new",
            "new",
            f"line-{offset}-new",
            offset,
            offset + 1,
        ),
    ]
    assert plan.row_lookup_unified == {
        (offset, "old"): offset + 5,
        (offset, "new"): offset + 6,
    }
