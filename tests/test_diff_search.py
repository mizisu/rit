import builtins
from typing import Literal

import pytest
from textual.content import Content

from rit.core.types import DiffLine
from rit.ui.widgets import diff_search
from rit.ui.widgets import diff_search_bar
from rit.ui.widgets import diff_search_display
from rit.ui.widgets import diff_blocks as diff_blocks_module
from rit.ui.widgets import diff_search_matching
from rit.ui.widgets import diff_search_match_index
from rit.ui.widgets import diff_search_policy
from rit.ui.widgets.diff_search_types import SearchRefreshUpdate
from rit.ui.widgets.diff_types import DiffSearchMatch, RenderedRow


def _spans(content: Content) -> list[tuple[int, int, str]]:
    return [(span.start, span.end, str(span.style)) for span in content.spans]


def test_search_sides_for_line_uses_rendered_side_in_unified_mode() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="unified",
        row_side="old",
        line_is_modified=True,
        line_is_deleted=False,
        line_is_added=False,
    ) == ("old",)


def test_search_sides_for_line_searches_both_sides_for_split_modified_line() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="old",
        line_is_modified=True,
        line_is_deleted=False,
        line_is_added=False,
    ) == ("old", "new")


def test_search_sides_for_line_uses_existing_side_for_split_single_sided_lines() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="old",
        line_is_modified=False,
        line_is_deleted=True,
        line_is_added=False,
    ) == ("old",)
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="new",
        line_is_modified=False,
        line_is_deleted=False,
        line_is_added=True,
    ) == ("new",)


def test_search_sides_for_line_uses_auto_for_split_context_line() -> None:
    assert diff_search.search_sides_for_line(
        row_mode="split",
        row_side="auto",
        line_is_modified=False,
        line_is_deleted=False,
        line_is_added=False,
    ) == ("auto",)


def test_search_sides_for_row_does_not_import_rendered_row_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        _all_lines = [DiffLine(old_line_no=1, new_line_no=1)]

    row = RenderedRow(
        mode="unified",
        row_index=0,
        line_index=0,
        hunk_index=0,
        kind="context",
        side="auto",
        anchor_id="line-0",
        old_line_no=1,
        new_line_no=1,
    )
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "rit.ui.widgets.diff_types":
            raise AssertionError("search row side lookup should not import per row")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    assert diff_search_match_index.search_sides_for_row(View(), row) == ("auto",)


def test_build_matches_appends_casefolded_matches_without_tuple_return_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        RenderedRow(
            mode="unified",
            row_index=index,
            line_index=index,
            hunk_index=0,
            kind="context",
            side="auto",
            anchor_id=f"line-{index}",
            old_line_no=index + 1,
            new_line_no=index + 1,
        )
        for index in range(3)
    ]

    class View:
        _all_lines = [
            DiffLine(old_line_no=1, new_line_no=1, new_content="Needle one"),
            DiffLine(old_line_no=2, new_line_no=2, new_content="Needle two"),
            DiffLine(old_line_no=3, new_line_no=3, new_content="Needle three"),
        ]

        def _rows_for_current_mode(self):
            return rows

        def _get_line_text(self, line: DiffLine, _side: str) -> str:
            return line.new_content

    seen_queries: list[str] = []

    def append_matches(matches: list[DiffSearchMatch], **kwargs) -> None:
        assert matches == []
        seen_queries.append(kwargs["query"])
        assert kwargs["query"] == "needle"

    def fail_tuple_helper(**_kwargs):
        raise AssertionError("build_matches should append without per-row tuples")

    monkeypatch.setattr(
        diff_search_match_index,
        "append_search_matches_for_text_casefolded",
        append_matches,
        raising=False,
    )
    monkeypatch.setattr(
        diff_search_match_index,
        "search_matches_for_text_casefolded",
        fail_tuple_helper,
        raising=False,
    )

    assert diff_search_match_index.build_matches(View(), "Needle") == []
    assert seen_queries == ["needle", "needle", "needle"]


def test_search_match_columns_finds_case_insensitive_non_overlapping_matches() -> None:
    assert diff_search.search_match_columns("Alpha alpha ALPHA", "alpha") == (0, 6, 12)


def test_search_match_columns_advances_by_query_length() -> None:
    assert diff_search.search_match_columns("aaaa", "aa") == (0, 2)


def test_search_match_columns_single_match_avoids_tuple_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diff_search_matching,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single search column should not call tuple()")
        ),
        raising=False,
    )

    assert diff_search_matching.search_match_columns("alpha beta", "beta") == (6,)


def test_search_match_columns_returns_empty_for_empty_query_or_text() -> None:
    assert diff_search.search_match_columns("alpha", "") == ()
    assert diff_search.search_match_columns("", "alpha") == ()


def test_search_match_style_uses_stronger_warning_for_active_match() -> None:
    assert diff_search.search_match_style(
        match_index=2,
        active_match_index=2,
    ) == "on $warning 45%"


def test_search_match_style_uses_softer_warning_for_inactive_match() -> None:
    assert diff_search.search_match_style(
        match_index=1,
        active_match_index=2,
    ) == "on $warning 25%"


def test_search_matches_for_text_builds_matches_with_row_metadata() -> None:
    assert diff_search.search_matches_for_text(
        text="foo and Foo",
        query="foo",
        row_index=7,
        line_index=3,
        side="new",
    ) == (
        DiffSearchMatch(row_index=7, line_index=3, side="new", column=0),
        DiffSearchMatch(row_index=7, line_index=3, side="new", column=8),
    )


def test_search_matches_for_text_returns_empty_without_matches() -> None:
    assert (
        diff_search.search_matches_for_text(
            text="alpha",
            query="z",
            row_index=7,
            line_index=3,
            side="new",
        )
        == ()
    )


def test_search_matches_for_text_casefolded_builds_matches_without_column_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diff_search_matching,
        "_search_match_columns_casefolded",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("match helper should build matches directly")
        ),
    )

    assert diff_search_matching.search_matches_for_text_casefolded(
        text="foo and foo",
        query="foo",
        row_index=7,
        line_index=3,
        side="new",
    ) == (
        DiffSearchMatch(row_index=7, line_index=3, side="new", column=0),
        DiffSearchMatch(row_index=7, line_index=3, side="new", column=8),
    )


def test_search_reveal_update_ignores_missing_target_row() -> None:
    assert diff_search.search_reveal_update(
        target_exists=False,
        has_target_widget=False,
        target_visible=False,
    ) == diff_search.SearchRevealUpdate(
        action="ignore",
        viewport_offset=0,
    )


def test_search_reveal_update_scrolls_to_target_widget_first() -> None:
    assert diff_search.search_reveal_update(
        target_exists=True,
        has_target_widget=True,
        target_visible=True,
    ) == diff_search.SearchRevealUpdate(
        action="scroll_widget",
        viewport_offset=0,
    )


def test_search_reveal_update_ignores_visible_row_without_widget() -> None:
    assert diff_search.search_reveal_update(
        target_exists=True,
        has_target_widget=False,
        target_visible=True,
    ) == diff_search.SearchRevealUpdate(
        action="ignore",
        viewport_offset=0,
    )


def test_search_reveal_update_scrolls_hidden_row_without_widget() -> None:
    assert diff_search.search_reveal_update(
        target_exists=True,
        has_target_widget=False,
        target_visible=False,
    ) == diff_search.SearchRevealUpdate(
        action="scroll_row",
        viewport_offset=0,
    )


def test_search_highlight_spans_filters_line_side_and_marks_active_match() -> None:
    matches = [
        _match(0, 1, "new", 2),
        _match(0, 1, "old", 3),
        _match(0, 1, "new", 8),
        _match(1, 2, "new", 0),
    ]

    assert diff_search.search_highlight_spans(
        matches,
        line_index=1,
        side="new",
        query_length=4,
        active_match_index=2,
    ) == (
        diff_search.SearchHighlightSpan(
            start=2,
            end=6,
            style="on $warning 25%",
        ),
        diff_search.SearchHighlightSpan(
            start=8,
            end=12,
            style="on $warning 45%",
        ),
    )


def test_search_highlight_spans_returns_empty_without_line_side_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diff_search_matching,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty highlight spans should not call tuple()")
        ),
        raising=False,
    )

    assert (
        diff_search.search_highlight_spans(
            [_match(0, 1, "old", 2)],
            line_index=1,
            side="new",
            query_length=4,
            active_match_index=0,
        )
        == ()
    )


def test_search_highlight_spans_returns_single_match_without_tuple_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diff_search_matching,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single highlight span should not call tuple()")
        ),
        raising=False,
    )

    assert diff_search.search_highlight_spans(
        [_match(0, 1, "new", 2)],
        line_index=1,
        side="new",
        query_length=4,
        active_match_index=0,
    ) == (
        diff_search.SearchHighlightSpan(
            start=2,
            end=6,
            style="on $warning 45%",
        ),
    )


def test_apply_search_highlights_reuses_cached_line_side_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_match = _match(3, 5, "new", 6)
    matches = [
        _match(0, 1, "new", 0),
        _match(1, 2, "old", 2),
        _match(2, 5, "old", 4),
        target_match,
    ]

    class View:
        _search_query = "beta"
        _search_matches = matches
        _search_match_index = 3
        _search_matches_by_line_side = {
            (5, "new"): ((3, target_match),),
        }
        _search_matches_by_line_side_source = (id(matches), len(matches))

    monkeypatch.setattr(
        diff_search_match_index,
        "search_highlight_spans",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("search highlight should use the line-side match index")
        ),
        raising=False,
    )

    result = diff_search.apply_search_highlights(
        View(),
        Content("alpha beta gamma"),
        5,
        "new",
    )

    assert result.plain == "alpha beta gamma"
    assert _spans(result) == [(6, 10, "on $warning 45%")]


def test_search_match_line_side_index_keeps_singleton_buckets_without_tuple_conversion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matches = [
        _match(0, 0, "auto", 0),
        _match(1, 1, "auto", 2),
        _match(2, 2, "new", 4),
    ]

    monkeypatch.setattr(
        diff_search_match_index,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("singleton search buckets should not call tuple()")
        ),
        raising=False,
    )

    index = diff_search_match_index._build_search_matches_by_line_side(matches)

    assert index == {
        (0, "auto"): ((0, matches[0]),),
        (1, "auto"): ((1, matches[1]),),
        (2, "new"): ((2, matches[2]),),
    }


def test_search_match_line_side_index_single_match_skips_iteration() -> None:
    class SingleMatchList(list):
        def __iter__(self):
            raise AssertionError("single search match should not be iterated")

    match = _match(7, 3, "new", 4)

    index = diff_search_match_index._build_search_matches_by_line_side(
        SingleMatchList([match])
    )

    assert index == {(3, "new"): ((0, match),)}


def test_search_refresh_update_marks_current_and_previous_match_lines_dirty() -> None:
    matches = [
        _match(0, 1, "auto", 0),
        _match(2, 3, "auto", 5),
    ]

    assert diff_search.search_refresh_update(
        matches,
        previous_match_lines={3, 9},
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=frozenset({1, 3, 9}),
        previous_match_lines=frozenset({1, 3}),
    )


def test_search_refresh_update_keeps_previous_lines_dirty_when_matches_clear() -> None:
    assert diff_search.search_refresh_update(
        [],
        previous_match_lines={3, 9},
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=frozenset({3, 9}),
        previous_match_lines=frozenset(),
    )


def test_search_refresh_update_single_match_avoids_generator_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_frozenset = frozenset
    calls: list[tuple[int, ...]] = []

    def recording_frozenset(values=()):
        assert not hasattr(values, "gi_code")
        calls.append(tuple(values))
        return original_frozenset(values)

    monkeypatch.setattr(
        diff_search_policy,
        "frozenset",
        recording_frozenset,
        raising=False,
    )

    assert diff_search.search_refresh_update(
        [_match(0, 4, "auto", 0)],
        previous_match_lines=(),
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=original_frozenset({4}),
        previous_match_lines=original_frozenset({4}),
    )

    assert calls == [(4,)]


def test_search_refresh_update_two_matches_avoids_generator_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_frozenset = frozenset
    calls: list[tuple[int, ...]] = []

    def recording_frozenset(values=()):
        assert not hasattr(values, "gi_code")
        calls.append(tuple(values))
        return original_frozenset(values)

    monkeypatch.setattr(
        diff_search_policy,
        "frozenset",
        recording_frozenset,
        raising=False,
    )

    assert diff_search.search_refresh_update(
        [_match(0, 4, "auto", 0), _match(1, 7, "auto", 2)],
        previous_match_lines=(),
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=original_frozenset({4, 7}),
        previous_match_lines=original_frozenset({4, 7}),
    )

    assert calls == [(4, 7)]


def test_search_refresh_update_reuses_current_lines_when_previous_lines_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_frozenset = frozenset
    previous_lines = original_frozenset({4})
    calls: list[tuple[int, ...]] = []

    def recording_frozenset(values=()):
        calls.append(tuple(values))
        return original_frozenset(values)

    monkeypatch.setattr(
        diff_search_policy,
        "frozenset",
        recording_frozenset,
        raising=False,
    )

    assert diff_search.search_refresh_update(
        [_match(0, 4, "auto", 0)],
        previous_match_lines=previous_lines,
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=original_frozenset({4}),
        previous_match_lines=original_frozenset({4}),
    )

    assert calls == [(4,)]


def test_search_refresh_update_empty_state_reuses_empty_current_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_frozenset = frozenset
    monkeypatch.setattr(
        diff_search_policy,
        "frozenset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty search refresh line sets should be reused")
        ),
        raising=False,
    )

    assert diff_search.search_refresh_update(
        [],
        previous_match_lines=(),
    ) == diff_search.SearchRefreshUpdate(
        dirty_lines=original_frozenset(),
        previous_match_lines=original_frozenset(),
    )


def test_refresh_search_display_reuses_policy_line_sets_without_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dirty_lines = frozenset({1, 3})
    previous_lines = frozenset({1})

    class View:
        _search_matches: list[DiffSearchMatch] = []
        _prev_search_match_lines = frozenset({9})
        invalidated: object = None

        def _invalidate_base_code_content_cache(self, line_indices: object) -> None:
            self.invalidated = line_indices

        def _update_line_cursor(self, _line_idx: int) -> None:
            raise AssertionError("grouped refresh should handle dirty lines")

    view = View()
    refreshed: list[object] = []

    def search_refresh_update(*_args: object, **_kwargs: object) -> SearchRefreshUpdate:
        return SearchRefreshUpdate(
            dirty_lines=dirty_lines,
            previous_match_lines=previous_lines,
        )

    monkeypatch.setattr(
        diff_search_display,
        "search_refresh_update",
        search_refresh_update,
    )
    monkeypatch.setattr(
        diff_search_display,
        "set",
        lambda _values: (_ for _ in ()).throw(
            AssertionError("search display should not copy match line sets")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        diff_blocks_module,
        "_refresh_grouped_blocks_for_lines",
        lambda _view, line_indices: refreshed.append(line_indices) or True,
    )

    diff_search_display.refresh_search_display(view)

    assert view.invalidated is dirty_lines
    assert refreshed == [dirty_lines]
    assert view._prev_search_match_lines is previous_lines


def test_search_activation_update_marks_new_and_previous_match_lines_dirty() -> None:
    matches = [
        _match(0, 1, "auto", 0),
        _match(2, 3, "new", 5),
    ]

    assert diff_search.search_activation_update(
        matches,
        old_index=0,
        target_index=1,
    ) == diff_search.SearchActivationUpdate(
        match=matches[1],
        dirty_lines=frozenset({1, 3}),
        pane="new",
        update_active_pane=True,
    )


def test_search_activation_update_keeps_auto_match_pane_neutral() -> None:
    matches = [_match(0, 1, "auto", 0)]

    assert diff_search.search_activation_update(
        matches,
        old_index=-1,
        target_index=0,
    ) == diff_search.SearchActivationUpdate(
        match=matches[0],
        dirty_lines=frozenset({1}),
        pane=None,
        update_active_pane=False,
    )


def test_search_activation_update_returns_none_for_invalid_target() -> None:
    assert (
        diff_search.search_activation_update(
            [_match(0, 1, "auto", 0)],
            old_index=-1,
            target_index=3,
        )
        is None
    )


def test_search_activation_placement_update_jumps_when_target_row_is_hidden() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=False,
        has_current_row=True,
        row_distance=0,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="jump_anchor",
        viewport_offset=0,
        reveal_horizontal=True,
    )


def test_search_activation_placement_update_jumps_without_current_row() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=True,
        has_current_row=False,
        row_distance=0,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="jump_anchor",
        viewport_offset=0,
        reveal_horizontal=True,
    )


def test_search_activation_placement_update_jumps_when_match_is_far() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=True,
        has_current_row=True,
        row_distance=11,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="jump_anchor",
        viewport_offset=0,
        reveal_horizontal=True,
    )


def test_search_activation_placement_update_moves_cursor_when_match_is_near() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=True,
        target_row_visible=True,
        has_current_row=True,
        row_distance=10,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="move_cursor",
        viewport_offset=0,
        reveal_horizontal=False,
    )


def test_search_activation_placement_update_moves_cursor_without_target_row() -> None:
    assert diff_search.search_activation_placement_update(
        has_target_row=False,
        target_row_visible=False,
        has_current_row=True,
        row_distance=99,
        half_page_step=10,
    ) == diff_search.SearchActivationPlacementUpdate(
        action="move_cursor",
        viewport_offset=0,
        reveal_horizontal=False,
    )


def _match(
    row_index: int,
    line_index: int,
    side: Literal["old", "new", "auto"],
    column: int,
) -> DiffSearchMatch:
    return DiffSearchMatch(
        row_index=row_index,
        line_index=line_index,
        side=side,
        column=column,
    )


def test_search_match_index_at_cursor_finds_exact_line_side_and_column() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "old", 4),
        _match(1, 1, "new", 4),
    ]

    assert (
        diff_search.search_match_index_at_cursor(
            matches,
            current_line=1,
            current_side="new",
            current_column=4,
        )
        == 2
    )


def test_search_match_index_at_cursor_uses_indexed_lookup_without_prefix_scan() -> None:
    class IndexedMatches:
        def __init__(self, matches: list[DiffSearchMatch]) -> None:
            self._matches = matches

        def __len__(self) -> int:
            return len(self._matches)

        def __getitem__(self, index: int) -> DiffSearchMatch:
            return self._matches[index]

        def __iter__(self):
            raise AssertionError("cursor match lookup should not scan match prefix")

    matches = IndexedMatches(
        [_match(i, i, "auto", 0) for i in range(1000)]
        + [_match(1000, 1000, "old", 4)]
    )

    assert (
        diff_search.search_match_index_at_cursor(
            matches,
            current_line=1000,
            current_side="old",
            current_column=4,
        )
        == 1000
    )


def test_search_match_index_at_cursor_returns_minus_one_without_exact_match() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "old", 4),
    ]

    assert (
        diff_search.search_match_index_at_cursor(
            matches,
            current_line=1,
            current_side="new",
            current_column=4,
        )
        == -1
    )


def test_search_match_refresh_clears_matches_without_query() -> None:
    matches = [_match(0, 0, "auto", 2)]

    assert diff_search.search_match_refresh(
        query="",
        matches=matches,
        current_line=0,
        current_side="auto",
        current_column=2,
    ) == diff_search.SearchMatchRefresh(
        matches=[],
        match_index=-1,
    )


def test_search_match_refresh_sets_index_from_cursor_for_query() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "new", 4),
    ]

    assert diff_search.search_match_refresh(
        query="needle",
        matches=matches,
        current_line=1,
        current_side="new",
        current_column=4,
    ) == diff_search.SearchMatchRefresh(
        matches=matches,
        match_index=1,
    )


def test_search_change_update_clears_state_for_blank_input() -> None:
    assert diff_search.search_change_update(
        "  ",
        matches=[_match(0, 0, "auto", 2)],
        cursor_target_index=0,
    ) == diff_search.SearchChangeUpdate(
        query="",
        matches=[],
        match_index=-1,
        reveal_index=None,
    )


def test_search_change_update_uses_next_cursor_target_for_search_input() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "new", 4),
    ]

    assert diff_search.search_change_update(
        "  needle  ",
        matches=matches,
        cursor_target_index=1,
    ) == diff_search.SearchChangeUpdate(
        query="needle",
        matches=matches,
        match_index=1,
        reveal_index=1,
    )


def test_search_change_update_omits_reveal_without_matches() -> None:
    assert diff_search.search_change_update(
        "needle",
        matches=[],
        cursor_target_index=-1,
    ) == diff_search.SearchChangeUpdate(
        query="needle",
        matches=[],
        match_index=-1,
        reveal_index=None,
    )


def test_next_search_match_index_prefers_later_row_then_later_column() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(1, 1, "auto", 4),
        _match(2, 2, "auto", 0),
    ]

    assert (
        diff_search.next_search_match_index(
            matches,
            current_row_index=1,
            current_side="auto",
            current_column=2,
        )
        == 1
    )
    assert (
        diff_search.next_search_match_index(
            matches,
            current_row_index=1,
            current_side="auto",
            current_column=4,
        )
        == 2
    )


def test_next_search_match_index_uses_indexed_lookup_without_scanning_prefix() -> None:
    class IndexedMatches:
        def __init__(self, matches: list[DiffSearchMatch]) -> None:
            self._matches = matches

        def __bool__(self) -> bool:
            return bool(self._matches)

        def __len__(self) -> int:
            return len(self._matches)

        def __getitem__(self, index: int) -> DiffSearchMatch:
            return self._matches[index]

        def __iter__(self):
            raise AssertionError("next search lookup should not scan match prefix")

    matches = IndexedMatches([_match(i, i, "auto", 0) for i in range(1000)])

    assert (
        diff_search.next_search_match_index(
            matches,
            current_row_index=900,
            current_side="auto",
            current_column=0,
        )
        == 901
    )


def test_next_search_match_index_wraps_when_cursor_is_after_last_match() -> None:
    matches = [
        _match(0, 0, "auto", 2),
        _match(2, 2, "auto", 4),
    ]

    assert (
        diff_search.next_search_match_index(
            matches,
            current_row_index=9,
            current_side="auto",
            current_column=0,
        )
        == 0
    )


def test_next_search_match_index_returns_minus_one_without_matches() -> None:
    assert (
        diff_search.next_search_match_index(
            [],
            current_row_index=0,
            current_side="auto",
            current_column=0,
        )
        == -1
    )


def test_search_jump_target_index_moves_relative_to_active_match() -> None:
    assert (
        diff_search.search_jump_target_index(
            current_match_index=1,
            match_count=3,
            cursor_target_index=0,
            direction=1,
        )
        == 2
    )
    assert (
        diff_search.search_jump_target_index(
            current_match_index=0,
            match_count=3,
            cursor_target_index=0,
            direction=-1,
        )
        == 2
    )


def test_search_jump_target_index_uses_cursor_when_no_match_is_active() -> None:
    assert (
        diff_search.search_jump_target_index(
            current_match_index=-1,
            match_count=3,
            cursor_target_index=1,
            direction=1,
        )
        == 1
    )
    assert (
        diff_search.search_jump_target_index(
            current_match_index=-1,
            match_count=3,
            cursor_target_index=1,
            direction=-1,
        )
        == 2
    )


def test_search_jump_target_index_returns_minus_one_without_matches() -> None:
    assert (
        diff_search.search_jump_target_index(
            current_match_index=-1,
            match_count=0,
            cursor_target_index=0,
            direction=1,
        )
        == -1
    )


def test_search_jump_update_reports_inactive_search() -> None:
    assert diff_search.search_jump_update(
        query="",
        match_count=0,
        current_match_index=-1,
        cursor_target_index=-1,
        direction=1,
    ) == diff_search.SearchJumpUpdate(
        action="inactive",
        target_index=-1,
        flash_message="No active search",
        flash_style="warning",
        update_status=False,
    )


def test_search_jump_update_reports_active_query_without_matches() -> None:
    assert diff_search.search_jump_update(
        query="needle",
        match_count=0,
        current_match_index=-1,
        cursor_target_index=-1,
        direction=1,
    ) == diff_search.SearchJumpUpdate(
        action="no_matches",
        target_index=-1,
        flash_message="No matches: needle",
        flash_style="warning",
        update_status=True,
    )


def test_search_jump_update_activates_next_target() -> None:
    assert diff_search.search_jump_update(
        query="needle",
        match_count=3,
        current_match_index=1,
        cursor_target_index=0,
        direction=1,
    ) == diff_search.SearchJumpUpdate(
        action="activate",
        target_index=2,
        flash_message=None,
        flash_style=None,
        update_status=False,
    )


def test_search_submission_request_ignores_missing_query() -> None:
    assert diff_search.search_submission_request(None) == diff_search.SearchSubmissionRequest(
        action="ignore",
        query="",
    )


def test_search_submission_request_clears_blank_query() -> None:
    assert diff_search.search_submission_request("  \t ") == diff_search.SearchSubmissionRequest(
        action="clear",
        query="",
    )


def test_search_submission_request_normalizes_search_query() -> None:
    assert diff_search.search_submission_request("  needle  ") == diff_search.SearchSubmissionRequest(
        action="search",
        query="needle",
    )


def test_search_submit_update_ignores_missing_query() -> None:
    assert diff_search.search_submit_update(
        None,
        matches=[],
        cursor_target_index=-1,
    ) == diff_search.SearchSubmitUpdate(
        action="ignore",
        query="",
        match_index=-1,
        flash_message=None,
        flash_style=None,
    )


def test_search_submit_update_clears_blank_query() -> None:
    assert diff_search.search_submit_update(
        "  ",
        matches=[_match(0, 0, "auto", 2)],
        cursor_target_index=0,
    ) == diff_search.SearchSubmitUpdate(
        action="clear",
        query="",
        match_index=-1,
        flash_message="Search cleared",
        flash_style=None,
    )


def test_search_submit_update_reports_no_matches() -> None:
    assert diff_search.search_submit_update(
        " needle ",
        matches=[],
        cursor_target_index=-1,
    ) == diff_search.SearchSubmitUpdate(
        action="no_matches",
        query="needle",
        match_index=-1,
        flash_message="No matches: needle",
        flash_style="warning",
    )


def test_search_submit_update_activates_cursor_target_for_matches() -> None:
    matches = [_match(0, 0, "auto", 2), _match(1, 1, "auto", 4)]

    assert diff_search.search_submit_update(
        "needle",
        matches=matches,
        cursor_target_index=1,
    ) == diff_search.SearchSubmitUpdate(
        action="activate",
        query="needle",
        match_index=1,
        flash_message=None,
        flash_style=None,
    )


def test_handle_submitted_input_closes_bar_focuses_view_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str]] = []

    class SearchBar:
        display = True

    class View:
        _search_bar_widget = SearchBar()
        focused = False

        def focus(self) -> None:
            self.focused = True

    def handle_submitted(view: object, query: str | None) -> None:
        assert query is not None
        calls.append((view, query))

    monkeypatch.setattr(diff_search_bar, "handle_submitted", handle_submitted)

    view = View()
    diff_search.handle_submitted_input(view, "needle")

    assert view._search_bar_widget.display is False
    assert view.focused is True
    assert calls == [(view, "needle")]


def test_search_submitted_input_update_closes_existing_bar_and_submits_value() -> None:
    assert diff_search.search_submitted_input_update(
        has_bar=True,
        value="needle",
    ) == diff_search.SearchSubmittedInputUpdate(
        close_bar=True,
        focus_view=True,
        submit_query="needle",
    )


def test_search_submitted_input_update_submits_without_bar() -> None:
    assert diff_search.search_submitted_input_update(
        has_bar=False,
        value="needle",
    ) == diff_search.SearchSubmittedInputUpdate(
        close_bar=False,
        focus_view=True,
        submit_query="needle",
    )


def test_search_start_update_opens_with_current_query() -> None:
    assert diff_search.search_start_update(
        has_bar=True,
        has_input=True,
        query="needle",
    ) == diff_search.SearchStartUpdate(
        action="open",
        input_value="needle",
        focus_input=True,
    )


def test_search_start_update_ignores_missing_widgets() -> None:
    assert diff_search.search_start_update(
        has_bar=False,
        has_input=True,
        query="needle",
    ) == diff_search.SearchStartUpdate(
        action="ignore",
        input_value="",
        focus_input=False,
    )


def test_search_close_update_ignores_missing_or_hidden_bar() -> None:
    assert diff_search.search_close_update(
        has_bar=True,
        bar_displayed=False,
        clear_query=True,
    ) == diff_search.SearchCloseUpdate(
        action="ignore",
        clear_state=False,
        refresh_display=False,
        update_status=False,
        focus_view=False,
    )


def test_search_close_update_closes_visible_bar_with_requested_clear_state() -> None:
    assert diff_search.search_close_update(
        has_bar=True,
        bar_displayed=True,
        clear_query=True,
    ) == diff_search.SearchCloseUpdate(
        action="close",
        clear_state=True,
        refresh_display=True,
        update_status=True,
        focus_view=True,
    )


def test_start_search_opens_bar_restores_query_and_focuses_input() -> None:
    class SearchBar:
        display = False

    class SearchInput:
        value = ""
        focused = False

        def focus(self) -> None:
            self.focused = True

    class View:
        _search_bar_widget = SearchBar()
        _search_input_widget = SearchInput()
        _search_query = "needle"

    view = View()
    diff_search.start_search(view)

    assert view._search_bar_widget.display is True
    assert view._search_input_widget.value == "needle"
    assert view._search_input_widget.focused is True


def test_start_search_ignores_missing_widgets() -> None:
    class View:
        _search_bar_widget = None
        _search_input_widget = None
        _search_query = "needle"

    diff_search.start_search(View())


def test_close_search_closes_visible_bar_clears_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class SearchBar:
        display = True

    class View:
        _search_bar_widget = SearchBar()

        def focus(self) -> None:
            calls.append("focus")

        def _update_status_line(self) -> None:
            calls.append("status")

    def clear_state(view: object) -> None:
        calls.append("clear")

    def refresh_search_display(view: object) -> None:
        calls.append("refresh")

    monkeypatch.setattr(diff_search_bar, "clear_state", clear_state)
    monkeypatch.setattr(diff_search_bar, "refresh_search_display", refresh_search_display)

    view = View()
    diff_search.close_search(view, clear_query=True)

    assert view._search_bar_widget.display is False
    assert calls == ["clear", "refresh", "status", "focus"]


def test_close_search_ignores_missing_or_hidden_bar() -> None:
    class HiddenView:
        class SearchBar:
            display = False

        _search_bar_widget = SearchBar()

        def focus(self) -> None:
            raise AssertionError("hidden search should not refocus")

    diff_search.close_search(HiddenView(), clear_query=True)
