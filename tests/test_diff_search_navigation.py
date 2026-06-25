import importlib

import pytest

from rit.ui.widgets import diff_search
from rit.ui.widgets import diff_search_navigation
from rit.ui.widgets.diff_search_types import (
    SearchActivationPlacementUpdate,
    SearchActivationUpdate,
)
from rit.ui.widgets.diff_types import DiffSearchMatch


def test_diff_search_reexports_canonical_navigation_helpers() -> None:
    navigation = importlib.import_module("rit.ui.widgets.diff_search_navigation")

    assert diff_search.reveal_match is navigation.reveal_match
    assert diff_search.activate_match is navigation.activate_match
    assert diff_search.jump_match is navigation.jump_match


def test_activate_match_reuses_activation_dirty_lines_without_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dirty_lines = frozenset({2, 4})
    match = DiffSearchMatch(
        row_index=0,
        line_index=4,
        side="auto",
        column=3,
    )

    class View:
        _search_matches: list[DiffSearchMatch] = [match]
        _search_match_index = -1
        visual_mode = False
        invalidated: object = None
        moved: tuple[int, object, int, bool, bool] | None = None
        scrolled = False
        status_updates = 0

        def _invalidate_base_code_content_cache(self, line_indices: object) -> None:
            self.invalidated = line_indices

        def _rows_for_current_mode(self) -> list[object]:
            return []

        def _current_row(self) -> object | None:
            return None

        def _half_page_step(self) -> int:
            return 10

        def _move_cursor(
            self,
            *,
            line: int,
            pane: object,
            column: int,
            scroll_in_visual: bool,
            update_active_pane: bool,
        ) -> None:
            self.moved = (line, pane, column, scroll_in_visual, update_active_pane)

        def _scroll_to_cursor_horizontal(self) -> None:
            self.scrolled = True

        def _update_status_line(self) -> None:
            self.status_updates += 1

    def search_activation_update(*_args: object, **_kwargs: object):
        return SearchActivationUpdate(
            match=match,
            dirty_lines=dirty_lines,
            pane=None,
            update_active_pane=False,
        )

    def search_activation_placement_update(
        *_args: object, **_kwargs: object
    ) -> SearchActivationPlacementUpdate:
        return SearchActivationPlacementUpdate(
            action="move_cursor",
            viewport_offset=0,
            reveal_horizontal=False,
        )

    monkeypatch.setattr(
        diff_search_navigation,
        "search_activation_update",
        search_activation_update,
    )
    monkeypatch.setattr(
        diff_search_navigation,
        "search_activation_placement_update",
        search_activation_placement_update,
    )
    monkeypatch.setattr(
        diff_search_navigation,
        "set",
        lambda _values: (_ for _ in ()).throw(
            AssertionError("search activation should not copy dirty lines")
        ),
        raising=False,
    )

    view = View()

    diff_search_navigation.activate_match(view, 0)

    assert view.invalidated is dirty_lines
    assert view.moved == (4, None, 3, False, False)
    assert view.scrolled is True
    assert view.status_updates == 1
