from rit.ui.widgets.diff_selection_range import (
    visible_selection_line_range,
    visual_selection_bounds,
    visual_selection_delta,
    visual_selection_specs_for_visible_lines,
    visual_selection_specs_with_dirty_lines,
    visual_selection_spec_for_line,
)


def test_visual_selection_spec_ignores_inactive_or_unrendered_lines() -> None:
    assert (
        visual_selection_spec_for_line(
            1,
            visual_mode=False,
            visual_anchor_line=1,
            visual_anchor_column=2,
            cursor_line=1,
            cursor_column=4,
            visual_type="char",
            has_lines=True,
            line_is_rendered=True,
        )
        is None
    )
    assert (
        visual_selection_spec_for_line(
            1,
            visual_mode=True,
            visual_anchor_line=None,
            visual_anchor_column=2,
            cursor_line=1,
            cursor_column=4,
            visual_type="char",
            has_lines=True,
            line_is_rendered=True,
        )
        is None
    )
    assert (
        visual_selection_spec_for_line(
            1,
            visual_mode=True,
            visual_anchor_line=1,
            visual_anchor_column=2,
            cursor_line=1,
            cursor_column=4,
            visual_type="char",
            has_lines=False,
            line_is_rendered=True,
        )
        is None
    )
    assert (
        visual_selection_spec_for_line(
            1,
            visual_mode=True,
            visual_anchor_line=1,
            visual_anchor_column=2,
            cursor_line=1,
            cursor_column=4,
            visual_type="char",
            has_lines=True,
            line_is_rendered=False,
        )
        is None
    )


def test_visual_selection_spec_returns_line_mode_for_lines_in_range() -> None:
    assert visual_selection_spec_for_line(
        2,
        visual_mode=True,
        visual_anchor_line=1,
        visual_anchor_column=3,
        cursor_line=3,
        cursor_column=5,
        visual_type="line",
        has_lines=True,
        line_is_rendered=True,
    ) == (0, None, "line")
    assert (
        visual_selection_spec_for_line(
            4,
            visual_mode=True,
            visual_anchor_line=1,
            visual_anchor_column=3,
            cursor_line=3,
            cursor_column=5,
            visual_type="line",
            has_lines=True,
            line_is_rendered=True,
        )
        is None
    )


def test_visual_selection_spec_normalizes_same_line_char_selection() -> None:
    assert visual_selection_spec_for_line(
        2,
        visual_mode=True,
        visual_anchor_line=2,
        visual_anchor_column=6,
        cursor_line=2,
        cursor_column=1,
        visual_type="char",
        has_lines=True,
        line_is_rendered=True,
    ) == (1, 6, "char")


def test_visual_selection_spec_handles_forward_multiline_char_selection() -> None:
    common = dict(
        visual_mode=True,
        visual_anchor_line=1,
        visual_anchor_column=3,
        cursor_line=3,
        cursor_column=5,
        visual_type="char",
        has_lines=True,
        line_is_rendered=True,
    )

    assert visual_selection_spec_for_line(1, **common) == (3, None, "char")
    assert visual_selection_spec_for_line(2, **common) == (0, None, "char")
    assert visual_selection_spec_for_line(3, **common) == (0, 5, "char")


def test_visual_selection_spec_handles_backward_multiline_char_selection() -> None:
    common = dict(
        visual_mode=True,
        visual_anchor_line=3,
        visual_anchor_column=5,
        cursor_line=1,
        cursor_column=2,
        visual_type="char",
        has_lines=True,
        line_is_rendered=True,
    )

    assert visual_selection_spec_for_line(1, **common) == (2, None, "char")
    assert visual_selection_spec_for_line(2, **common) == (0, None, "char")
    assert visual_selection_spec_for_line(3, **common) == (0, 5, "char")


def test_visual_selection_bounds_defaults_missing_anchor_column() -> None:
    bounds = visual_selection_bounds(
        visual_anchor_line=2,
        visual_anchor_column=None,
        cursor_line=2,
        cursor_column=5,
    )

    assert bounds.start_line == 2
    assert bounds.end_line == 2
    assert bounds.first_line_col == 0
    assert bounds.last_line_col == 5


def test_visual_selection_bounds_preserves_forward_columns() -> None:
    bounds = visual_selection_bounds(
        visual_anchor_line=1,
        visual_anchor_column=3,
        cursor_line=4,
        cursor_column=7,
    )

    assert bounds.start_line == 1
    assert bounds.end_line == 4
    assert bounds.first_line_col == 3
    assert bounds.last_line_col == 7


def test_visual_selection_bounds_preserves_backward_columns() -> None:
    bounds = visual_selection_bounds(
        visual_anchor_line=4,
        visual_anchor_column=7,
        cursor_line=1,
        cursor_column=3,
    )

    assert bounds.start_line == 1
    assert bounds.end_line == 4
    assert bounds.first_line_col == 3
    assert bounds.last_line_col == 7


def test_visible_selection_line_range_ignores_inactive_or_empty_state() -> None:
    assert (
        visible_selection_line_range(
            visual_mode=False,
            visual_anchor_line=1,
            cursor_line=3,
            has_lines=True,
            rendered_start=0,
            rendered_end=4,
        )
        is None
    )
    assert (
        visible_selection_line_range(
            visual_mode=True,
            visual_anchor_line=None,
            cursor_line=3,
            has_lines=True,
            rendered_start=0,
            rendered_end=4,
        )
        is None
    )
    assert (
        visible_selection_line_range(
            visual_mode=True,
            visual_anchor_line=1,
            cursor_line=3,
            has_lines=False,
            rendered_start=0,
            rendered_end=4,
        )
        is None
    )


def test_visible_selection_line_range_clips_to_rendered_window() -> None:
    assert visible_selection_line_range(
        visual_mode=True,
        visual_anchor_line=2,
        cursor_line=8,
        has_lines=True,
        rendered_start=4,
        rendered_end=6,
    ) == (4, 6)


def test_visible_selection_line_range_normalizes_backward_selection() -> None:
    assert visible_selection_line_range(
        visual_mode=True,
        visual_anchor_line=8,
        cursor_line=2,
        has_lines=True,
        rendered_start=4,
        rendered_end=6,
    ) == (4, 6)


def test_visible_selection_line_range_returns_none_without_overlap() -> None:
    assert (
        visible_selection_line_range(
            visual_mode=True,
            visual_anchor_line=1,
            cursor_line=3,
            has_lines=True,
            rendered_start=4,
            rendered_end=8,
        )
        is None
    )


def test_visual_selection_specs_for_visible_lines_clips_and_skips_unrendered_lines() -> None:
    specs = visual_selection_specs_for_visible_lines(
        visual_mode=True,
        visual_anchor_line=2,
        visual_anchor_column=4,
        cursor_line=7,
        cursor_column=3,
        visual_type="char",
        has_lines=True,
        rendered_start=3,
        rendered_end=6,
        line_is_rendered=lambda line_idx: line_idx != 5,
    )

    assert specs == {
        3: (0, None, "char"),
        4: (0, None, "char"),
        6: (0, None, "char"),
    }


def test_visual_selection_specs_for_visible_lines_returns_empty_without_overlap() -> None:
    specs = visual_selection_specs_for_visible_lines(
        visual_mode=True,
        visual_anchor_line=1,
        visual_anchor_column=4,
        cursor_line=2,
        cursor_column=3,
        visual_type="char",
        has_lines=True,
        rendered_start=5,
        rendered_end=8,
        line_is_rendered=lambda _: True,
    )

    assert specs == {}


def test_visual_selection_delta_clears_removed_specs_and_applies_changed_specs() -> None:
    old_specs = {
        1: (0, None, "line"),
        2: (0, 3, "char"),
        3: (0, None, "char"),
    }
    new_specs = {
        2: (0, 4, "char"),
        3: (0, None, "char"),
        4: (0, None, "line"),
    }

    delta = visual_selection_delta(old_specs, new_specs)

    assert delta.lines_to_clear == frozenset({1})
    assert delta.lines_to_apply == frozenset({2, 4})


def test_visual_selection_delta_reapplies_dirty_lines_with_same_spec() -> None:
    specs = {2: (0, None, "line")}

    delta = visual_selection_delta(specs, specs, dirty_lines={2})

    assert delta.lines_to_clear == frozenset()
    assert delta.lines_to_apply == frozenset({2})


def test_visual_selection_delta_clears_dirty_lines_that_leave_selection() -> None:
    delta = visual_selection_delta(
        {2: (0, None, "line")},
        {},
        dirty_lines={2},
    )

    assert delta.lines_to_clear == frozenset({2})
    assert delta.lines_to_apply == frozenset()


def test_visual_selection_specs_with_dirty_lines_updates_and_removes_specs() -> None:
    old_specs = {
        1: (0, None, "line"),
        2: (0, 3, "char"),
    }

    updated = visual_selection_specs_with_dirty_lines(
        old_specs,
        {
            1: None,
            3: (2, None, "char"),
        },
    )

    assert updated == {
        2: (0, 3, "char"),
        3: (2, None, "char"),
    }
    assert old_specs == {
        1: (0, None, "line"),
        2: (0, 3, "char"),
    }


def test_visual_selection_specs_with_dirty_lines_returns_copy_for_empty_dirty_specs() -> None:
    old_specs = {2: (0, None, "line")}

    updated = visual_selection_specs_with_dirty_lines(old_specs, {})

    assert updated == old_specs
    assert updated is not old_specs
