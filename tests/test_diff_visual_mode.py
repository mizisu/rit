from rit.ui.widgets import diff_visual_mode as visual_mode
from rit.ui.widgets.diff_visual_mode import (
    VisualAnchorUIUpdate,
    VisualModeState,
    VisualModeUIUpdate,
    VisualTypeUIUpdate,
    allows_column_motion,
    enter_visual_mode,
    exit_visual_mode,
    toggle_visual_mode,
    visual_line_selection_role,
    visual_anchor_ui_update,
    visual_mode_ui_update,
    visual_mode_sub_title,
    visual_type_ui_update,
)


def test_enter_visual_mode_starts_selection_at_cursor() -> None:
    state = enter_visual_mode(
        visual_type="char",
        current_visual_mode=False,
        current_visual_anchor_line=None,
        current_visual_anchor_column=None,
        cursor_line=4,
        cursor_column=7,
    )

    assert state == VisualModeState(
        visual_mode=True,
        visual_type="char",
        visual_anchor_line=4,
        visual_anchor_column=7,
    )


def test_enter_visual_mode_preserves_existing_anchor_when_switching_type() -> None:
    state = enter_visual_mode(
        visual_type="char",
        current_visual_mode=True,
        current_visual_anchor_line=2,
        current_visual_anchor_column=5,
        cursor_line=8,
        cursor_column=1,
    )

    assert state == VisualModeState(
        visual_mode=True,
        visual_type="char",
        visual_anchor_line=2,
        visual_anchor_column=5,
    )


def test_exit_visual_mode_clears_anchor_and_preserves_type() -> None:
    state = exit_visual_mode(current_visual_type="line")

    assert state == VisualModeState(
        visual_mode=False,
        visual_type="line",
        visual_anchor_line=None,
        visual_anchor_column=None,
    )


def test_toggle_visual_mode_exits_when_same_type_is_active() -> None:
    state = toggle_visual_mode(
        requested_visual_type="line",
        current_visual_mode=True,
        current_visual_type="line",
        current_visual_anchor_line=3,
        current_visual_anchor_column=9,
        cursor_line=5,
        cursor_column=4,
    )

    assert state == VisualModeState(
        visual_mode=False,
        visual_type="line",
        visual_anchor_line=None,
        visual_anchor_column=None,
    )


def test_toggle_visual_mode_switches_type_without_resetting_anchor() -> None:
    state = toggle_visual_mode(
        requested_visual_type="char",
        current_visual_mode=True,
        current_visual_type="line",
        current_visual_anchor_line=3,
        current_visual_anchor_column=9,
        cursor_line=5,
        cursor_column=4,
    )

    assert state == VisualModeState(
        visual_mode=True,
        visual_type="char",
        visual_anchor_line=3,
        visual_anchor_column=9,
    )


def test_visual_line_selection_role_returns_none_for_char_mode() -> None:
    assert (
        visual_line_selection_role(
            line_index=3,
            visual_type="char",
            visual_anchor_line=3,
        )
        == "none"
    )


def test_visual_line_selection_role_returns_anchor_for_line_anchor() -> None:
    assert (
        visual_line_selection_role(
            line_index=3,
            visual_type="line",
            visual_anchor_line=3,
        )
        == "anchor"
    )


def test_visual_line_selection_role_returns_selected_for_non_anchor_line() -> None:
    assert (
        visual_line_selection_role(
            line_index=4,
            visual_type="line",
            visual_anchor_line=3,
        )
        == "selected"
    )


def test_allows_column_motion_in_normal_mode() -> None:
    assert allows_column_motion(visual_mode=False, visual_type="line") is True


def test_allows_column_motion_in_visual_char_mode() -> None:
    assert allows_column_motion(visual_mode=True, visual_type="char") is True


def test_allows_column_motion_blocks_visual_line_mode() -> None:
    assert allows_column_motion(visual_mode=True, visual_type="line") is False


def test_visual_mode_sub_title_is_empty_outside_visual_mode() -> None:
    assert visual_mode_sub_title(visual_mode=False, visual_type="char") == ""


def test_visual_mode_sub_title_names_char_visual_mode() -> None:
    assert visual_mode_sub_title(visual_mode=True, visual_type="char") == "-- VISUAL --"


def test_visual_mode_sub_title_names_line_visual_mode() -> None:
    assert (
        visual_mode_sub_title(visual_mode=True, visual_type="line")
        == "-- VISUAL LINE --"
    )


def test_visual_mode_ui_update_refreshes_cursor_line_when_entering() -> None:
    assert visual_mode_ui_update(
        visual_mode=True,
        visual_type="char",
        cursor_line=4,
    ) == VisualModeUIUpdate(
        sub_title="-- VISUAL --",
        selection_refresh_lines=frozenset({4}),
        clear_selection=False,
        update_status_line=True,
    )


def test_visual_mode_ui_update_uses_line_subtitle_for_line_mode() -> None:
    assert visual_mode_ui_update(
        visual_mode=True,
        visual_type="line",
        cursor_line=2,
    ) == VisualModeUIUpdate(
        sub_title="-- VISUAL LINE --",
        selection_refresh_lines=frozenset({2}),
        clear_selection=False,
        update_status_line=True,
    )


def test_visual_mode_ui_update_clears_selection_when_exiting() -> None:
    assert visual_mode_ui_update(
        visual_mode=False,
        visual_type="char",
        cursor_line=4,
    ) == VisualModeUIUpdate(
        sub_title="",
        selection_refresh_lines=frozenset(),
        clear_selection=True,
        update_status_line=True,
    )


def test_visual_ui_updates_reuse_empty_line_set_without_allocation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        visual_mode,
        "frozenset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty visual line sets should be reused")
        ),
        raising=False,
    )

    assert visual_mode_ui_update(
        visual_mode=False,
        visual_type="char",
        cursor_line=4,
    ).selection_refresh_lines == frozenset()
    assert visual_type_ui_update(
        visual_mode=False,
        visual_type="line",
        cursor_line=5,
    ).selection_dirty_lines == frozenset()
    assert visual_anchor_ui_update(
        visual_mode=False,
        cursor_line=6,
    ).selection_dirty_lines == frozenset()


def test_visual_ui_updates_build_singleton_line_sets_without_intermediate_sets(
    monkeypatch,
) -> None:
    original_frozenset = frozenset
    calls: list[tuple[int, ...]] = []

    def recording_frozenset(values=()):
        assert not isinstance(values, set)
        calls.append(tuple(values))
        return original_frozenset(values)

    monkeypatch.setattr(
        visual_mode,
        "frozenset",
        recording_frozenset,
        raising=False,
    )

    assert visual_mode_ui_update(
        visual_mode=True,
        visual_type="char",
        cursor_line=4,
    ).selection_refresh_lines == original_frozenset({4})
    assert visual_type_ui_update(
        visual_mode=True,
        visual_type="line",
        cursor_line=5,
    ).selection_dirty_lines == original_frozenset({5})
    assert visual_anchor_ui_update(
        visual_mode=True,
        cursor_line=6,
    ).selection_dirty_lines == original_frozenset({6})

    assert calls == [(4,), (5,), (6,)]


def test_visual_type_ui_update_marks_line_dirty_in_visual_mode() -> None:
    assert visual_type_ui_update(
        visual_mode=True,
        visual_type="line",
        cursor_line=5,
    ) == VisualTypeUIUpdate(
        sub_title="-- VISUAL LINE --",
        selection_dirty_lines=frozenset({5}),
        update_status_line=True,
    )


def test_visual_type_ui_update_uses_char_subtitle_in_visual_mode() -> None:
    assert visual_type_ui_update(
        visual_mode=True,
        visual_type="char",
        cursor_line=5,
    ) == VisualTypeUIUpdate(
        sub_title="-- VISUAL --",
        selection_dirty_lines=frozenset({5}),
        update_status_line=True,
    )


def test_visual_type_ui_update_only_updates_status_outside_visual_mode() -> None:
    assert visual_type_ui_update(
        visual_mode=False,
        visual_type="line",
        cursor_line=5,
    ) == VisualTypeUIUpdate(
        sub_title=None,
        selection_dirty_lines=frozenset(),
        update_status_line=True,
    )


def test_visual_anchor_ui_update_marks_cursor_line_dirty_in_visual_mode() -> None:
    assert visual_anchor_ui_update(
        visual_mode=True,
        cursor_line=6,
    ) == VisualAnchorUIUpdate(selection_dirty_lines=frozenset({6}))


def test_visual_anchor_ui_update_is_empty_outside_visual_mode() -> None:
    assert visual_anchor_ui_update(
        visual_mode=False,
        cursor_line=6,
    ) == VisualAnchorUIUpdate(selection_dirty_lines=frozenset())


def test_visual_queue_update_maps_visual_type_dirty_line_and_status() -> None:
    update = visual_type_ui_update(
        visual_mode=True,
        visual_type="line",
        cursor_line=5,
    )

    assert visual_mode.visual_queue_update(update) == visual_mode.VisualQueueUpdate(
        selection_dirty_lines=frozenset({5}),
        update_status_line=True,
    )


def test_visual_queue_update_omits_empty_dirty_lines() -> None:
    update = visual_anchor_ui_update(
        visual_mode=False,
        cursor_line=6,
    )

    assert visual_mode.visual_queue_update(update) == visual_mode.VisualQueueUpdate(
        selection_dirty_lines=None,
        update_status_line=False,
    )
