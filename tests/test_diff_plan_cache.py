"""Source mutation, projection parity, and bounded planning-cache coverage."""

from copy import deepcopy
from dataclasses import fields, replace

import pytest

from rit.core.types import DiffHunk, DiffLine, FileDiff, InlineSegment, SegmentType
from rit.ui.widgets import diff_highlight, diff_plan
from rit.ui.widgets.diff_folding import build_viewed_file_fold_diff
from rit.ui.widgets.diff_plan_cache import DiffPlanCache, publish_line_metadata
from rit.ui.widgets.diff_view import DiffView


def _source() -> FileDiff:
    return FileDiff(
        filename="All files",
        hunks=[
            DiffHunk(
                1,
                2,
                1,
                2,
                starts_file=True,
                file_path="one.py",
                lines=[
                    DiffLine(1, 1, "old 界", "new 界", is_modified=True),
                    DiffLine(None, 2, new_content="\tadded", is_added=True),
                ],
            ),
            DiffHunk(
                10000,
                2,
                10000,
                1,
                file_path="one.py",
                lines=[
                    DiffLine(10000, None, old_content="removed", is_deleted=True),
                    DiffLine(10001, 10000, "context", "context"),
                ],
            ),
            DiffHunk(
                1,
                1,
                1,
                1,
                starts_file=True,
                file_path="two.py",
                file_old_path="before.py",
                file_status="renamed",
                lines=[DiffLine(1, 1, "second", "second")],
            ),
            DiffHunk(0, 0, 0, 0, starts_file=True, file_path="empty.bin"),
        ],
    )


def _assert_projection(cache: DiffPlanCache, visible: FileDiff, *, split: bool) -> None:
    source_lines = [line for hunk in cache.source.hunks for line in hunk.lines]
    metadata = [(line.line_index, line.file_path) for line in source_lines]
    projection = cache.prepare(visible)
    rows = projection.build_rows(split=split)
    assert [(line.line_index, line.file_path) for line in source_lines] == metadata
    expected = diff_plan.build_diff_plan(deepcopy(visible))
    for attribute in fields(expected):
        if attribute.name not in {"all_lines", "rendered_rows"}:
            assert getattr(projection.plan, attribute.name) == getattr(
                expected, attribute.name
            ), attribute.name
    visible_lines = [line for hunk in visible.hunks for line in hunk.lines]
    assert len(projection.plan.all_lines) == len(visible_lines)
    assert all(
        actual is source
        for actual, source in zip(projection.plan.all_lines, visible_lines)
    )
    if split:
        assert rows.rows_split == expected.rendered_rows.rows_split
        assert rows.row_lookup_split == expected.rendered_rows.row_lookup_split
        assert not rows.rows_unified and not rows.row_lookup_unified
    else:
        assert rows.rows_unified == expected.rendered_rows.rows_unified
        assert rows.row_lookup_unified == expected.rendered_rows.row_lookup_unified
        assert not rows.rows_split and not rows.row_lookup_split
    publish_line_metadata(visible)
    assert projection.plan.all_lines == expected.all_lines


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("initially_folded", [frozenset(), frozenset({"one.py"})])
def test_cached_projection_matches_fresh_plan(
    split: bool, initially_folded: frozenset[str]
) -> None:
    source = _source()
    cache = DiffPlanCache(source)
    for collapsed in [
        initially_folded,
        {"one.py"},
        {"two.py", "empty.bin"},
        {"one.py", "two.py", "empty.bin"},
        set(),
        {"one.py"},
        set(),
    ]:
        visible, _ = build_viewed_file_fold_diff(
            source, is_collapsed=collapsed.__contains__
        )
        _assert_projection(cache, visible, split=split)


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("shape", ["empty", "single", "no_headers"])
def test_cache_handles_empty_and_single_file_plans(split: bool, shape: str) -> None:
    source = _source()
    if shape == "empty":
        source.hunks = []
    elif shape == "single":
        source.filename = "one.py"
        source.hunks = source.hunks[:1]
    else:
        for hunk in source.hunks:
            hunk.starts_file = False
    cache = DiffPlanCache(source)
    _assert_projection(cache, source, split=split)
    _assert_projection(cache, source, split=split)


def test_initial_planning_populates_reusable_hunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    cache = DiffPlanCache(source)
    calls: list[int] = []
    original = diff_plan.build_diff_plan

    def counted(diff: FileDiff, **kwargs):
        calls.append(len(diff.hunks[0].lines))
        return original(diff, **kwargs)

    monkeypatch.setattr(diff_plan, "build_diff_plan", counted)
    initial = cache.prepare(source).build_rows(split=False)
    assert len(calls) == len(source.hunks)
    publish_line_metadata(source)
    for _ in range(4):
        folded, _ = build_viewed_file_fold_diff(
            source, is_collapsed=lambda path: path == "two.py"
        )
        projected = cache.prepare(folded).build_rows(split=False)
        assert projected.rows_unified[0] is initial.rows_unified[0]
        cache.prepare(source).build_rows(split=False)
    assert calls == [2, 2, 1, 0, 1]
    assert len(cache._hunks) == len(source.hunks) + 1
    assert all(len(hunk.placements) <= 2 for hunk in cache._hunks.values())
    for number in range(10, 20):
        source.hunks[2].lines[0].new_line_no = number
        folded, _ = build_viewed_file_fold_diff(
            source, is_collapsed=lambda path: path == "two.py"
        )
        _assert_projection(cache, folded, split=False)
    assert len(cache._hunks) == len(source.hunks) + 1


@pytest.mark.parametrize(
    "mutation",
    [
        "text",
        "number",
        "kind",
        "line",
        "list",
        "hunk",
        "path",
        "order",
        "segments",
        "metadata",
        "highlight_policy",
    ],
)
def test_cache_invalidates_mutated_source_inputs(mutation: str) -> None:
    source = _source()
    cache = DiffPlanCache(source)
    _assert_projection(cache, source, split=False)
    revision = cache.revision
    cache.has_source_changes = False
    hunk = source.hunks[0]
    if mutation == "text":
        hunk.lines[0].new_content = "界" * 90
    elif mutation == "number":
        hunk.lines[0].new_line_no = 999999
    elif mutation == "kind":
        hunk.lines[0].is_modified = False
    elif mutation == "line":
        hunk.lines[0] = replace(hunk.lines[0], new_content="replacement")
    elif mutation == "list":
        hunk.lines = [*hunk.lines, DiffLine(None, 3, new_content="tail", is_added=True)]
    elif mutation == "hunk":
        source.hunks[0] = replace(hunk, lines=list(reversed(hunk.lines)))
    elif mutation == "path":
        hunk.file_path = "renamed.py"
    elif mutation == "segments":
        hunk.lines[0].new_segments.append(InlineSegment("new", SegmentType.ADDED))
    elif mutation == "metadata":
        hunk.header = "new context"
    elif mutation == "highlight_policy":
        hunk.lines[0].syntax_highlighting_disabled = True
    else:
        hunk.lines.reverse()
    _assert_projection(cache, source, split=False)
    _assert_projection(cache, source, split=True)
    assert cache.revision > revision and cache.has_source_changes


def test_highlight_policy_change_invalidates_shared_fold_projections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    folded, _ = build_viewed_file_fold_diff(
        source, is_collapsed=lambda filename: filename == "one.py"
    )
    view = DiffView()
    view.current_file = source.filename
    view._source_diff = source
    view._diff = folded
    view._hl_state.cache = {(id(source), True, True), (id(folded), True, True)}
    monkeypatch.setattr(diff_highlight, "_use_windowed_highlight_strategy", lambda *_: True)
    assert view._reset_current_diff_highlight_state()
    assert not view._hl_state.cache
