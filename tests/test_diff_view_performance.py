"""Tests for DiffView split rendering and performance-oriented behavior."""

import asyncio
import threading

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.geometry import Region
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PendingReviewComment, PRComment, PRFile, ReviewThread
from rit.state.store import PRStore
from rit.ui.widgets import diff_search as _search_mod
from rit.ui.widgets import diff_blocks as _blocks_mod
from rit.ui.widgets import diff_comments as _comments_mod
from rit.ui.widgets import diff_cursor as _cursor_mod
from rit.ui.widgets import diff_highlight as _hl_mod
from rit.ui.widgets import diff_plan as _plan_mod
from rit.ui.widgets import diff_render as _render_mod
from rit.ui.widgets import diff_selection as _selection_mod
from rit.ui.widgets import diff_types as _diff_types_mod
from rit.ui.widgets import diff_virtual as _virtual_mod
from rit.ui.widgets import diff_visual as _visual_mod
from rit.ui.widgets.diff_types import CursorUIState, RenderedRow
from rit.ui.widgets.diff_view import DiffView, SplitDiffBlock, UnifiedDiffBlock
from rit.ui.widgets.diff_visual import LineContent
from tests.conftest import wait_until


def _as_plain(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


def _diff_view_render_idle(diff_view: DiffView) -> bool:
    return (
        not diff_view._hl_state.window_worker_active
        and diff_view._hl_state.window_inflight is None
        and diff_view._hl_state.queued_window is None
        and not diff_view._cursor_ui.flush_pending
        and not diff_view._virt.render_pending
    )


def test_missing_side_hatch_text_uses_default_step_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _visual_mod,
        "range",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("default hatch placeholder should not loop by column")
        ),
        raising=False,
    )

    text = _visual_mod.missing_side_hatch_text(8, row_index=5)

    assert text == _visual_mod.MISSING_SIDE_HATCH * 8


def test_line_annotations_render_line_reuses_cached_number_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    annotations = _visual_mod.LineAnnotations([Content("1"), Content("1000")])
    monkeypatch.setattr(
        _visual_mod,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("render_line should not rescan all annotation widths")
        ),
        raising=False,
    )

    strip = annotations.render_line(0)

    assert strip.text == "1   "


def test_line_annotations_single_number_width_avoids_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _visual_mod,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single annotation width should not call max")
        ),
        raising=False,
    )

    annotations = _visual_mod.LineAnnotations([Content("1000")])

    assert annotations.number_width == 4


def test_line_content_get_optimal_width_reuses_cached_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = LineContent([Content("1"), Content("1000")], ["", ""])
    monkeypatch.setattr(
        _visual_mod,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("get_optimal_width should not rescan code lines")
        ),
        raising=False,
    )

    assert content.get_optimal_width({}, 80) == 4


def test_line_content_single_line_width_avoids_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _visual_mod,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single code line width should not call max")
        ),
        raising=False,
    )

    content = LineContent([Content("1000")], [""])

    assert content.get_optimal_width({}, 80) == 4


def test_two_cursor_repaint_lines_avoid_sorted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _cursor_mod,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("two-line cursor repaint should not call sorted")
        ),
        raising=False,
    )

    assert tuple(_cursor_mod._cursor_lines_for_repaint({4, 2})) == (2, 4)


def test_line_number_width_reuses_number_index_keys_without_tuple_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        show_line_numbers = True
        _line_index_by_old_number = {1: 0, 120: 1}
        _line_index_by_new_number = {1: 0, 9000: 1}

    monkeypatch.setattr(
        _render_mod,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("line number width should reuse number index keys")
        ),
        raising=False,
    )

    assert _render_mod._old_line_number_width(View()) == 3
    assert _render_mod._new_line_number_width(View()) == 4


def test_line_number_width_uses_planned_values_without_scanning_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        show_line_numbers = True
        _old_line_number_width_value = 3
        _new_line_number_width_value = 4
        _line_index_by_old_number = {1: 0, 120: 1}
        _line_index_by_new_number = {1: 0, 9000: 1}

    monkeypatch.setattr(
        _render_mod._layout,
        "line_number_width_for_layout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("planned line number widths should be reused")
        ),
    )

    assert _render_mod._old_line_number_width(View()) == 3
    assert _render_mod._new_line_number_width(View()) == 4


def test_code_content_without_cursor_skips_line_text_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = DiffLine(None, "content", line_index=2)
    base_content = Content("content")

    class View:
        def _get_line_text(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("non-cursor content should not read line text")

    monkeypatch.setattr(
        _render_mod,
        "_base_code_content",
        lambda *_args, **_kwargs: base_content,
    )
    monkeypatch.setattr(
        _render_mod._search,
        "apply_search_highlights",
        lambda _view, content, _line_idx, _side: content,
    )

    content = _render_mod._build_code_content_with_cursor(
        View(),
        line,
        has_cursor=False,
        cursor_col=None,
    )

    assert content is base_content


def test_unified_block_row_without_selection_skips_line_text_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = DiffLine(1, 1, old_content="content", new_content="content", line_index=2)
    annotation = Content("1 ")
    base_content = Content("content")

    class View:
        cursor_column = 0

        def _diff_line_cursor_active(self, _line_index: int) -> bool:
            return False

        def _compute_selection_spec_for_line(self, _line_index: int) -> None:
            return None

        def _get_line_text(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("unselected block rows should not read line text")

        def _build_code_content_with_cursor(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> Content:
            return base_content

    monkeypatch.setattr(
        _blocks_mod,
        "_unified_block_static_rows",
        lambda _view, _line: (
            _diff_types_mod.UnifiedBlockRowStaticData(
                annotation=annotation,
                line_style="line-style",
                side="auto",
            ),
        ),
    )

    annotations, code_lines, line_styles = _blocks_mod._build_unified_block_row_data(
        View(),
        line,
    )

    assert annotations == [annotation]
    assert code_lines == [base_content]
    assert line_styles == ["line-style"]


def test_unified_block_row_skips_selection_spec_when_visual_mode_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = DiffLine(1, 1, old_content="content", new_content="content", line_index=2)
    base_content = Content("content")

    class View:
        visual_mode = False
        cursor_column = 0

        def _diff_line_cursor_active(self, _line_index: int) -> bool:
            return False

        def _compute_selection_spec_for_line(self, _line_index: int) -> None:
            raise AssertionError("visual-mode-off rows should not compute selection")

        def _build_code_content_with_cursor(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> Content:
            return base_content

    monkeypatch.setattr(
        _blocks_mod,
        "_unified_block_static_rows",
        lambda _view, _line: (
            _diff_types_mod.UnifiedBlockRowStaticData(
                annotation=Content("1 "),
                line_style="line-style",
                side="auto",
            ),
        ),
    )

    _blocks_mod._build_unified_block_row_data(View(), line)


def test_non_block_refresh_skips_selection_spec_when_visual_mode_off() -> None:
    line = DiffLine(1, 1, old_content="content", new_content="content", line_index=2)
    base_content = Content("content")

    class Widget:
        updated: Content | None = None

        def has_class(self, _class_name: str) -> bool:
            return False

        def update(self, content: Content) -> None:
            self.updated = content

        def remove_class(self, _class_name: str) -> None:
            pass

    widget = Widget()

    class View:
        visual_mode = False
        cursor_column = 0
        _all_lines = [line]

        def _get_code_widgets(self, _line_idx: int) -> tuple[Widget]:
            return (widget,)

        def _compute_selection_spec_for_line(self, _line_idx: int) -> None:
            raise AssertionError("visual-mode-off refresh should not compute selection")

        def _diff_line_cursor_active(self, _line_idx: int) -> bool:
            return False

        def _get_line_side_for_widget(
            self,
            _line: DiffLine,
            _widget: Widget,
        ) -> str:
            return "auto"

        def _widget_matches_cursor_side(
            self,
            _line: DiffLine,
            _widget: Widget,
        ) -> bool:
            return False

        def _base_code_content(self, *_args: object, **_kwargs: object) -> Content:
            return base_content

    _blocks_mod._refresh_non_block_line_content(View(), 0)

    assert widget.updated is base_content


def test_split_code_content_without_selection_skips_line_content_lookup() -> None:
    base_content = Content("content")

    class Line:
        line_index = 2
        has_old_side = True
        has_new_side = True

        @property
        def old_content(self) -> str:
            raise AssertionError("unselected split rows should not read line text")

    class View:
        cursor_column = 0

        def _compute_selection_spec_for_line(self, _line_index: int) -> None:
            return None

        def _diff_line_cursor_active(self, _line_index: int) -> bool:
            return False

        def _build_code_content_with_cursor(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> Content:
            return base_content

    content = _render_mod._build_split_code_content(
        View(),
        Line(),
        side="old",
        placeholder_when_missing=False,
    )

    assert content is base_content


def test_split_code_content_skips_selection_spec_when_visual_mode_off() -> None:
    base_content = Content("content")

    class Line:
        line_index = 2
        has_old_side = True
        has_new_side = True

    class View:
        visual_mode = False
        cursor_column = 0

        def _compute_selection_spec_for_line(self, _line_index: int) -> None:
            raise AssertionError("visual-mode-off split rows should not compute selection")

        def _diff_line_cursor_active(self, _line_index: int) -> bool:
            return False

        def _build_code_content_with_cursor(
            self,
            *_args: object,
            **_kwargs: object,
        ) -> Content:
            return base_content

    content = _render_mod._build_split_code_content(
        View(),
        Line(),
        side="old",
        placeholder_when_missing=False,
    )

    assert content is base_content


def test_unified_diff_block_reuses_tuple_line_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line_indices = (1, 2)
    monkeypatch.setattr(
        _diff_types_mod,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("tuple line indices should be reused")
        ),
        raising=False,
    )

    block = UnifiedDiffBlock(line_indices)

    assert block.line_indices is line_indices


def test_split_diff_block_reuses_tuple_line_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line_indices = (3, 4)
    monkeypatch.setattr(
        _diff_types_mod,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("tuple line indices should be reused")
        ),
        raising=False,
    )

    block = SplitDiffBlock(line_indices)

    assert block.line_indices is line_indices


def test_cursor_ui_flush_transfers_pending_line_sets_without_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NonCopyablePendingLines:
        def __bool__(self) -> bool:
            return False

        def __iter__(self):
            raise AssertionError("cursor flush should not copy pending line sets")

        def clear(self) -> None:
            raise AssertionError("cursor flush should transfer pending line sets")

    pending_cursor_lines = NonCopyablePendingLines()
    pending_selection_lines = NonCopyablePendingLines()

    class View:
        is_mounted = True
        visual_mode = False
        _unified_blocks_by_line = {}
        _split_blocks_by_line = {}

        def __init__(self) -> None:
            self._cursor_ui = CursorUIState()
            self._cursor_ui.flush_pending = True
            self._cursor_ui.dirty_lines = pending_cursor_lines  # type: ignore[assignment]
            self._cursor_ui.selection_dirty = pending_selection_lines  # type: ignore[assignment]

        def _update_line_cursor(self, _line_idx: int) -> None:
            raise AssertionError("no line repaint expected")

        def _update_selection_highlighting(self, _dirty_lines=None) -> None:
            raise AssertionError("no selection repaint expected")

        def _update_status_line(self) -> None:
            raise AssertionError("no status repaint expected")

    seen: dict[str, object] = {}

    def cursor_lines_for_flush(**kwargs):
        seen.update(kwargs)
        return set()

    monkeypatch.setattr(
        _cursor_mod._cursor_update,
        "cursor_lines_for_flush",
        cursor_lines_for_flush,
    )

    view = View()

    _cursor_mod._flush_queued_cursor_ui_updates(view)

    assert seen["cursor_lines"] is pending_cursor_lines
    assert seen["selection_dirty_lines"] is pending_selection_lines
    assert view._cursor_ui.flush_pending is False
    assert view._cursor_ui.dirty_lines == set()
    assert view._cursor_ui.selection_dirty == set()


def test_cursor_ui_flush_skips_sort_for_single_dirty_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        is_mounted = True
        visual_mode = False
        _unified_blocks_by_line = {}
        _split_blocks_by_line = {}

        def __init__(self) -> None:
            self._cursor_ui = CursorUIState()
            self._cursor_ui.flush_pending = True
            self._cursor_ui.dirty_lines = {4}
            self._cursor_ui.selection_dirty = set()
            self.updated_lines: list[int] = []

        def _update_line_cursor(self, line_idx: int) -> None:
            self.updated_lines.append(line_idx)

        def _update_selection_highlighting(self, _dirty_lines=None) -> None:
            raise AssertionError("no selection repaint expected")

        def _update_status_line(self) -> None:
            raise AssertionError("no status repaint expected")

    monkeypatch.setattr(
        _cursor_mod._blocks,
        "_refresh_grouped_blocks_for_lines",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        _cursor_mod,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single dirty line should not sort")
        ),
        raising=False,
    )

    view = View()

    _cursor_mod._flush_queued_cursor_ui_updates(view)

    assert view.updated_lines == [4]


def test_mounted_block_row_bounds_does_not_copy_line_indices() -> None:
    class NoListLineIndices:
        def __init__(self, values: tuple[int, ...]) -> None:
            self._values = values

        def __iter__(self):
            return iter(self._values)

        def __len__(self) -> int:
            raise AssertionError("block row bounds should not copy line indices")

        def index(self, value: int) -> int:
            return self._values.index(value)

    class Block:
        is_mounted = True
        line_indices = NoListLineIndices((3, 4, 5))
        region = Region(0, 20, 20, 3)

    class ScrollableContentRegion:
        y = 5

    class View:
        split = False
        scroll_y = 10
        scrollable_content_region = ScrollableContentRegion()
        _unified_blocks_by_line = {5: Block()}
        _split_blocks_by_line = {}
        _all_lines = [
            DiffLine(None, None, line_index=index, is_modified=index == 4)
            for index in range(6)
        ]

    row = RenderedRow(
        mode="unified",
        row_index=5,
        line_index=5,
        hunk_index=0,
        kind="context",
        side="auto",
        anchor_id="line-5",
        old_line_no=5,
        new_line_no=5,
    )

    assert _cursor_mod._mounted_block_row_vertical_bounds(View(), row) == (28, 29)


def test_mounted_block_row_bounds_finds_row_in_single_pass() -> None:
    class NoIndexLineIndices:
        def __init__(self, values: tuple[int, ...]) -> None:
            self._values = values

        def __iter__(self):
            return iter(self._values)

        def index(self, _value: int) -> int:
            raise AssertionError("block row bounds should not rescan line indices")

    class Block:
        is_mounted = True
        line_indices = NoIndexLineIndices((3, 4, 5))
        region = Region(0, 20, 20, 3)

    class ScrollableContentRegion:
        y = 5

    class View:
        split = False
        scroll_y = 10
        scrollable_content_region = ScrollableContentRegion()
        _unified_blocks_by_line = {5: Block()}
        _split_blocks_by_line = {}
        _all_lines = [
            DiffLine(None, None, line_index=index, is_modified=index == 4)
            for index in range(6)
        ]

    row = RenderedRow(
        mode="unified",
        row_index=5,
        line_index=5,
        hunk_index=0,
        kind="context",
        side="auto",
        anchor_id="line-5",
        old_line_no=5,
        new_line_no=5,
    )

    assert _cursor_mod._mounted_block_row_vertical_bounds(View(), row) == (28, 29)


def test_build_comment_map_reuses_planned_file_paths_without_line_scan() -> None:
    """Comment mapping should not rescan all diff lines to rediscover file paths."""

    class NoIterLines(list):
        def __iter__(self):
            raise AssertionError("comment map should reuse planned file paths")

    diff_view = DiffView(store=PRStore())
    diff_view.current_file = "large.py"
    diff_view._diff_file_paths = {"large.py"}
    diff_view._all_lines = NoIterLines()

    _comments_mod.build_comment_map(diff_view)

    assert diff_view._comment_line_indices == []


def test_file_path_lookup_reuses_planned_container_without_copy() -> None:
    """Comment mapping should use planned file path membership without copying."""

    class NoIterFilePaths:
        def __bool__(self) -> bool:
            return True

        def __iter__(self):
            raise AssertionError("planned file paths should not be copied")

    diff_view = DiffView(store=PRStore())
    diff_view.current_file = "large.py"
    diff_view._diff_file_paths = NoIterFilePaths()

    assert _comments_mod._file_paths_for_current_diff(
        diff_view
    ) is diff_view._diff_file_paths


@pytest.mark.asyncio
async def test_show_diff_reuses_filename_index_without_store_file_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoIterFiles(list[PRFile]):
        def __iter__(self):
            raise AssertionError("show_diff should reuse the filename index")

    file = PRFile(filename="large.py", additions=1)
    store = PRStore()
    store.state.files = NoIterFiles([file])
    store.state.files_by_filename = {"large.py": file}
    diff_view = DiffView(store=store)
    diff = parse_patch("@@ -1 +1 @@\n-old\n+new", "large.py")

    monkeypatch.setattr(diff_view, "watch_visual_mode", lambda *_args: None)
    monkeypatch.setattr(diff_view, "watch_visual_type", lambda *_args: None)
    monkeypatch.setattr(_selection_mod, "_exit_visual_mode", lambda _view: None)
    monkeypatch.setattr(_comments_mod, "build_comment_map", lambda _view: None)
    monkeypatch.setattr(_render_mod, "_update_split_state", lambda _view: None)
    monkeypatch.setattr(
        _render_mod,
        "_ensure_rendered_rows_for_mode",
        lambda _view, *, split: None,
    )
    monkeypatch.setattr(_virtual_mod, "_rebuild_virtual_layout", lambda _view: None)
    monkeypatch.setattr(_virtual_mod, "_configure_virtual_window", lambda _view: None)
    monkeypatch.setattr(_hl_mod, "_has_highlighted_diff", lambda _view, _diff: True)
    monkeypatch.setattr(_hl_mod, "_highlight_diff_sync", lambda _view, _diff: None)

    async def skip_render(_request_token: int) -> None:
        return None

    monkeypatch.setattr(diff_view, "_run_render_diff_for_request", skip_render)

    await diff_view.show_diff("large.py", diff)

    assert diff_view._file is file


def test_build_comment_map_merges_line_indices_without_temporary_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diff_view = DiffView(store=PRStore())
    diff_view.current_file = "large.py"
    diff_view._diff_file_paths = {"large.py"}
    diff_view._line_index_by_new_number = {1: 4, 2: 9}
    diff_view.store.state.pending_review_comments = [
        PendingReviewComment(
            path="large.py",
            line=1,
            side="RIGHT",
            body="draft",
        )
    ]
    diff_view.store.state.review_threads = [
        ReviewThread.model_validate(
            {
                "id": "thread-1",
                "path": "large.py",
                "line": 2,
                "comments": {
                    "nodes": [
                        PRComment.model_validate(
                            {
                                "databaseId": 1,
                                "path": "large.py",
                                "line": 2,
                                "body": "comment",
                                "side": "RIGHT",
                            }
                        )
                    ]
                },
            }
        )
    ]

    monkeypatch.setattr(
        _comments_mod,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("comment line index merge should not build temporary sets")
        ),
        raising=False,
    )

    _comments_mod.build_comment_map(diff_view)

    assert diff_view._comment_line_indices == [4, 9]


def test_active_comment_widget_indexes_groups_without_combining() -> None:
    """Selecting one comment widget should not materialize all widgets first."""

    class IndexedWidgets:
        def __init__(self, *widgets: Static) -> None:
            self._widgets = widgets

        def __len__(self) -> int:
            return len(self._widgets)

        def __getitem__(self, index: int) -> Static:
            return self._widgets[index]

        def __iter__(self):
            raise AssertionError("active comment lookup should index widget groups")

    draft = Static("draft")
    thread = Static("thread")

    class View:
        _comment_cursor_index = 2
        _pending_comment_widgets_by_line = {4: IndexedWidgets(draft)}
        _comment_widgets_by_line = {4: IndexedWidgets(thread)}

    assert _comments_mod.active_comment_widget(View(), 4) is thread


def test_clear_cursor_highlight_iterates_widget_groups_without_copy() -> None:
    """Clearing comment highlight should not build a combined widget list."""

    class NoLengthWidgets:
        def __init__(self, *widgets: Static) -> None:
            self._widgets = widgets

        def __iter__(self):
            return iter(self._widgets)

        def __len__(self) -> int:
            raise AssertionError("highlight clearing should not copy widget groups")

    draft = Static("draft")
    thread = Static("thread")
    draft.add_class("--cursor-line")
    thread.add_class("--cursor-line")

    class View:
        _pending_comment_widgets_by_line = {9: NoLengthWidgets(draft)}
        _comment_widgets_by_line = {9: NoLengthWidgets(thread)}

    _comments_mod._clear_cursor_line_class(View(), 9)

    assert "--cursor-line" not in draft.classes
    assert "--cursor-line" not in thread.classes


def test_exiting_visual_mode_clears_selection_without_copying_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoListSelectionSpecs:
        def __iter__(self):
            return iter((2, 4, 6))

        def __len__(self) -> int:
            raise AssertionError("visual mode exit should not copy selection specs")

    class AppState:
        sub_title = "-- VISUAL --"

    class View:
        app = AppState()
        visual_type = "char"
        cursor_line = 4
        _visual_selection_specs = NoListSelectionSpecs()
        status_updates = 0

        def _update_status_line(self) -> None:
            self.status_updates += 1

    cleared: list[int] = []
    monkeypatch.setattr(
        _selection_mod,
        "_clear_line_selection",
        lambda _view, line_idx: cleared.append(line_idx),
    )

    view = View()

    DiffView.watch_visual_mode(view, True, False)

    assert cleared == [2, 4, 6]
    assert view._visual_selection_specs == {}
    assert view.app.sub_title == ""
    assert view.status_updates == 1


def test_highlight_refresh_reuses_range_order_without_sort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        is_mounted = True
        _unified_blocks_by_line: dict[int, object] = {}
        _split_blocks_by_line: dict[int, object] = {}
        invalidated: set[int] | None = None

        def _is_line_rendered(self, line_idx: int) -> bool:
            return line_idx != 3

        def _invalidate_base_code_content_cache(self, line_indices: set[int]) -> None:
            self.invalidated = line_indices

    refreshed: list[int] = []
    monkeypatch.setattr(
        _hl_mod,
        "sorted",
        lambda _values: (_ for _ in ()).throw(
            AssertionError("highlight refresh should preserve range order")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_grouped_blocks_for_lines",
        lambda _view, _dirty_lines: None,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_non_block_line_content",
        lambda _view, line_idx: refreshed.append(line_idx),
    )

    view = View()

    _hl_mod._refresh_rendered_highlight_range(view, 1, 5)

    assert view.invalidated == {1, 2, 4, 5}
    assert refreshed == [1, 2, 4, 5]


def test_unified_block_render_passes_lazy_line_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAnnotations:
        class Styles:
            width = 0

        styles = Styles()

    class FakeUnifiedBlock:
        _annotations = FakeAnnotations()

        def __init__(self, line_indices, *, classes: str) -> None:
            assert not isinstance(line_indices, list)
            self.line_indices = tuple(line_indices)

        def update_block(self, **_kwargs: object) -> None:
            pass

    class Container:
        mounted: list[object] = []

        def mount(self, widget: object, **_kwargs: object) -> None:
            self.mounted.append(widget)

    class View:
        _showing_full_file = False
        _unified_code_width = 80
        _unified_blocks_by_line: dict[int, object] = {}

        def _register_line_widget(self, _line_index: int, _widget: object) -> None:
            pass

    monkeypatch.setattr(_blocks_mod, "UnifiedDiffBlock", FakeUnifiedBlock)
    monkeypatch.setattr(
        _blocks_mod,
        "_build_unified_block_row_data",
        lambda _view, _line: ([], [], []),
    )
    lines = [
        DiffLine(None, None, line_index=10),
        DiffLine(None, None, line_index=11),
    ]

    _blocks_mod._render_unified_line_block(View(), Container(), lines)

    assert View._unified_blocks_by_line[10].line_indices == (10, 11)


def test_split_block_render_passes_lazy_line_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeScroll:
        def set_on_scroll_x(self, _callback: object) -> None:
            pass

    class FakeSplitBlock:
        _left_scroll = FakeScroll()
        _right_scroll = FakeScroll()

        def __init__(self, line_indices, *, classes: str) -> None:
            assert not isinstance(line_indices, list)
            self.line_indices = tuple(line_indices)

        def update_block(self, **_kwargs: object) -> None:
            pass

    class Container:
        mounted: list[object] = []

        def mount(self, widget: object, **_kwargs: object) -> None:
            self.mounted.append(widget)

    class View:
        _split_blocks_by_line: dict[int, object] = {}
        _split_old_code_width = 40
        _split_new_code_width = 40

        def _sync_split_horizontal_scroll(self, _scroll_x: int) -> None:
            pass

        def _register_line_widget(self, _line_index: int, _widget: object) -> None:
            pass

        def _register_split_scroll_widgets(
            self,
            _line_index: int,
            *_widgets: object,
        ) -> None:
            pass

    monkeypatch.setattr(_blocks_mod, "SplitDiffBlock", FakeSplitBlock)
    monkeypatch.setattr(
        _blocks_mod,
        "_build_split_block_row_data",
        lambda _view, _line: (None, "", None, "", None, "", None, ""),
    )
    lines = [
        DiffLine(None, None, line_index=20),
        DiffLine(None, None, line_index=21),
    ]

    _blocks_mod._render_split_line_block(View(), Container(), lines)

    assert View._split_blocks_by_line[20].line_indices == (20, 21)


def test_split_horizontal_scroll_noop_source_event_skips_widget_scan() -> None:
    class NoValuesRegistry:
        def values(self):
            raise AssertionError("unchanged source scroll should not scan widgets")

    class View:
        _split_horizontal_scroll_x = 8.0
        _syncing_split_scroll = False
        _split_scroll_widgets_by_line = NoValuesRegistry()
        _hunk_header_widgets = NoValuesRegistry()

    view = View()

    DiffView._sync_split_horizontal_scroll(view, 8.0, source=object())

    assert view._split_horizontal_scroll_x == 8.0
    assert view._syncing_split_scroll is False


def test_unified_block_refresh_streams_block_lines_without_materializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class Lines:
        def __getitem__(self, index: int) -> DiffLine:
            events.append(("get", index))
            return DiffLine(None, None, line_index=index)

    class Block:
        is_mounted = True
        line_indices = (0, 1)

        def update_block(self, **_kwargs: object) -> None:
            pass

    class View:
        _all_lines = Lines()
        _unified_blocks_by_line = {0: Block(), 1: Block()}
        _unified_code_width = 80

    def build_row_data(_view: View, line: DiffLine):
        events.append(("build", line.line_index))
        return ([], [], [])

    monkeypatch.setattr(_blocks_mod, "_build_unified_block_row_data", build_row_data)

    assert _blocks_mod._refresh_unified_blocks_for_lines(View(), {0}) is True
    assert events == [("get", 0), ("build", 0), ("get", 1), ("build", 1)]


def test_unified_block_refresh_updates_blocks_as_they_are_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []

    class Lines:
        def __getitem__(self, index: int) -> DiffLine:
            events.append(("get", index))
            return DiffLine(None, None, line_index=index)

    class BlocksByLine(dict[int, object]):
        def get(self, key: int, default: object = None) -> object:
            events.append(("lookup", key))
            return super().get(key, default)

    class Block:
        is_mounted = True

        def __init__(self, block_id: int, line_index: int) -> None:
            self.block_id = block_id
            self.line_indices = (line_index,)

        def update_block(self, **_kwargs: object) -> None:
            events.append(("update", self.block_id))

    block_one = Block(1, 0)
    block_two = Block(2, 10)

    class View:
        _all_lines = Lines()
        _unified_blocks_by_line = BlocksByLine({0: block_one, 10: block_two})
        _unified_code_width = 80

    monkeypatch.setattr(
        _blocks_mod,
        "_build_unified_block_row_data",
        lambda _view, _line: ([], [], []),
    )

    assert _blocks_mod._refresh_unified_blocks_for_lines(View(), (0, 10)) is True
    assert events == [
        ("lookup", 0),
        ("get", 0),
        ("update", 1),
        ("lookup", 10),
        ("get", 10),
        ("update", 2),
    ]


def test_single_unified_block_refresh_avoids_seen_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Block:
        pass

    block = Block()

    class View:
        _unified_blocks_by_line = {4: block}

    refreshed: list[Block] = []

    monkeypatch.setattr(
        _blocks_mod,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single-line unified refresh should not allocate seen set")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_unified_block",
        lambda _view, refreshed_block: refreshed.append(refreshed_block),
    )

    assert _blocks_mod._refresh_unified_blocks_for_lines(View(), (4,)) is True
    assert refreshed == [block]


def test_single_split_block_refresh_avoids_seen_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Block:
        pass

    block = Block()

    class View:
        _split_blocks_by_line = {6: block}

    refreshed: list[Block] = []

    monkeypatch.setattr(
        _blocks_mod,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single-line split refresh should not allocate seen set")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_split_block",
        lambda _view, refreshed_block: refreshed.append(refreshed_block),
    )

    assert _blocks_mod._refresh_split_blocks_for_lines(View(), (6,)) is True
    assert refreshed == [block]


def test_grouped_block_refresh_skips_empty_unified_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        _unified_blocks_by_line: dict[int, object] = {}
        _split_blocks_by_line = {4: object()}

    def refresh_unified(_view: View, _line_indices) -> bool:
        raise AssertionError("empty unified block map should not be refreshed")

    split_calls: list[tuple[int, ...]] = []

    def refresh_split(_view: View, line_indices) -> bool:
        split_calls.append(tuple(line_indices))
        return True

    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_unified_blocks_for_lines",
        refresh_unified,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_split_blocks_for_lines",
        refresh_split,
    )

    assert _blocks_mod._refresh_grouped_blocks_for_lines(View(), (4,)) is True
    assert split_calls == [(4,)]


def test_grouped_block_refresh_skips_empty_split_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        _unified_blocks_by_line = {7: object()}
        _split_blocks_by_line: dict[int, object] = {}

    unified_calls: list[tuple[int, ...]] = []

    def refresh_unified(_view: View, line_indices) -> bool:
        unified_calls.append(tuple(line_indices))
        return True

    def refresh_split(_view: View, _line_indices) -> bool:
        raise AssertionError("empty split block map should not be refreshed")

    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_unified_blocks_for_lines",
        refresh_unified,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_split_blocks_for_lines",
        refresh_split,
    )

    assert _blocks_mod._refresh_grouped_blocks_for_lines(View(), (7,)) is True
    assert unified_calls == [(7,)]


def test_grouped_block_refresh_skips_when_no_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        _unified_blocks_by_line = {1: object()}
        _split_blocks_by_line = {1: object()}

    def refresh_blocks(_view: View, _line_indices) -> bool:
        raise AssertionError("empty grouped refresh should not dispatch")

    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_unified_blocks_for_lines",
        refresh_blocks,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_split_blocks_for_lines",
        refresh_blocks,
    )

    assert _blocks_mod._refresh_grouped_blocks_for_lines(View(), ()) is False


def test_grouped_block_refresh_skips_when_no_block_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View:
        _unified_blocks_by_line: dict[int, object] = {}
        _split_blocks_by_line: dict[int, object] = {}

    def refresh_blocks(_view: View, _line_indices) -> bool:
        raise AssertionError("grouped refresh without block maps should not dispatch")

    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_unified_blocks_for_lines",
        refresh_blocks,
    )
    monkeypatch.setattr(
        _blocks_mod,
        "_refresh_split_blocks_for_lines",
        refresh_blocks,
    )

    assert _blocks_mod._refresh_grouped_blocks_for_lines(View(), (1,)) is False


@pytest.mark.asyncio
async def test_split_mode_renders_old_and_new_panes_for_modified_line() -> None:
    """Split mode should render side-by-side panes with old/new content."""

    patch = """@@ -1,3 +1,3 @@
-old content here
+new content here
 line2
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        old_code = diff_view.query_one("#line-0-old .code-content", Static)
        new_code = diff_view.query_one("#line-0-new .code-content", Static)

        assert "old content here" in _as_plain(old_code)
        assert "new content here" in _as_plain(new_code)


@pytest.mark.asyncio
async def test_show_diff_reuses_highlight_cache_for_same_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Showing the same diff repeatedly should not re-highlight every time."""

    patch = """@@ -1,2 +1,2 @@
-old
+new"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    calls = {"count": 0}
    original = diff_highlight_module.highlight_lines_for_diff

    def counted_highlight(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff", counted_highlight
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        assert calls["count"] == 1


@pytest.mark.asyncio
async def test_comment_line_jump_uses_row_lookup_without_row_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comment navigation should jump by row lookup instead of scanning all rows."""

    patch = "@@ -1,4 +1,4 @@\n line1\n line2\n line3\n line4"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class NoIterRows(list):
        def __iter__(self):
            raise AssertionError("comment jump should not scan rendered rows")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        diff_view._rows_unified = NoIterRows(diff_view._rows_unified)
        jumped_rows = []
        monkeypatch.setattr(
            diff_view,
            "_jump_to_row_with_anchor",
            lambda row, **_kwargs: jumped_rows.append(row),
        )

        _comments_mod._jump_to_comment_line(diff_view, 2)

        assert [row.line_index for row in jumped_rows] == [2]


@pytest.mark.asyncio
async def test_show_diff_renders_plain_first_then_applies_highlight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache miss should render plain content first, then refresh highlighted content in place."""

    patch = """@@ -1,1 +1,1 @@
-old_value
+new_value"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    original = diff_highlight_module.highlight_lines_for_diff

    def blocking_highlight(*args, **kwargs):
        started.set()
        unblock.wait(timeout=1.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff", blocking_highlight
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        assert started.wait(timeout=1.0) is True
        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        modified = diff.hunks[0].lines[0]
        assert modified.highlighted_old_content is None
        assert modified.highlighted_new_content is None

        old_code = diff_view.query_one("#line-0-old .code-content", Static)
        new_code = diff_view.query_one("#line-0-new .code-content", Static)
        assert "old_value" in _as_plain(old_code)
        assert "new_value" in _as_plain(new_code)

        unblock.set()
        await wait_until(
            lambda: (
                not diff_view._hl_state.full_worker_active
                and modified.highlighted_old_content is not None
                and modified.highlighted_new_content is not None
            ),
            timeout=5.0,
        )

        assert render_calls["count"] == baseline_calls
        assert any(
            cache_key[:2] == (id(diff), diff_view.word_diff_enabled)
            for cache_key in diff_view._hl_state.cache
        )


@pytest.mark.asyncio
async def test_rapid_show_diff_coalesces_full_highlight_to_latest_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid file switches should skip stale full-diff highlight jobs."""

    patch = "@@ -1,2 +1,2 @@\n-old\n+new"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    calls: list[str] = []
    original = diff_highlight_module.highlight_lines_for_diff

    def blocking_highlight(diff, *args, **kwargs):
        calls.append(diff_names[id(diff)])
        if len(calls) == 1:
            started.set()
            unblock.wait(timeout=1.0)
        return original(diff, *args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff", blocking_highlight
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff1 = parse_patch(patch, "one.py")
        diff2 = parse_patch(patch, "two.py")
        diff3 = parse_patch(patch, "three.py")

        diff_names = {
            id(diff1): "one.py",
            id(diff2): "two.py",
            id(diff3): "three.py",
        }

        await diff_view.show_diff("one.py", diff1)
        await pilot.pause()
        assert started.wait(timeout=1.0) is True

        await diff_view.show_diff("two.py", diff2)
        await diff_view.show_diff("three.py", diff3)
        await pilot.pause()

        unblock.set()
        await pilot.pause()
        await pilot.pause()

        assert calls[0] == "one.py"
        assert "three.py" in calls
        assert "two.py" not in calls


@pytest.mark.asyncio
async def test_rapid_windowed_highlight_coalesces_to_latest_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid range-highlight requests should skip intermediate queued windows."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 221))
    patch = f"@@ -1,220 +1,220 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    calls: list[tuple[int, int]] = []
    original = diff_highlight_module.highlight_lines_for_diff_range

    def blocking_range_highlight(diff, start_line, end_line, *args, **kwargs):
        calls.append((start_line, end_line))
        if len(calls) == 1:
            started.set()
            unblock.wait(timeout=1.0)
        return original(diff, start_line, end_line, *args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module,
        "highlight_lines_for_diff_range",
        blocking_range_highlight,
    )

    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "medium.py")

        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()
        assert started.wait(timeout=1.0) is True

        _hl_mod._queue_highlight_diff_range(diff_view, "medium.py", diff, 80, 110)
        _hl_mod._queue_highlight_diff_range(diff_view, "medium.py", diff, 150, 180)
        await pilot.pause()

        unblock.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert calls[-1] == (150, 180)
        assert (80, 110) not in calls


@pytest.mark.asyncio
async def test_windowed_highlight_refreshes_grouped_blocks_without_full_rerender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible window highlighting should refresh grouped blocks without full `_render_diff()`."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    original = diff_highlight_module.highlight_lines_for_diff_range

    def blocking_range_highlight(*args, **kwargs):
        started.set()
        unblock.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module,
        "highlight_lines_for_diff_range",
        blocking_range_highlight,
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        try:
            assert started.wait(timeout=5.0) is True
            baseline_calls = render_calls["count"]
            assert baseline_calls >= 1
            assert diff.hunks[0].lines[0].highlighted_old_content is None

            unblock.set()
            await wait_until(
                lambda: (
                    not diff_view._hl_state.window_worker_active
                    and diff_view._hl_state.window_inflight is None
                    and diff_view._hl_state.queued_window is None
                ),
                timeout=20.0,
            )
        finally:
            unblock.set()

        assert render_calls["count"] == baseline_calls
        assert diff.hunks[0].lines[0].highlighted_old_content is not None


@pytest.mark.asyncio
async def test_dynamic_virtual_window_radius_shrinks_for_modified_heavy_diff() -> None:
    """Default adaptive windowing should use a smaller radius for modified-heavy diffs."""

    simple_patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f" line{i}" for i in range(1, 201)
    )
    modified_patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        simple_diff = parse_patch(simple_patch, "simple.py")
        await diff_view.show_diff("simple.py", simple_diff)
        await pilot.pause()
        simple_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

        modified_diff = parse_patch(modified_patch, "modified.py")
        await diff_view.show_diff("modified.py", modified_diff)
        await pilot.pause()
        modified_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

        assert modified_radius < simple_radius


@pytest.mark.asyncio
async def test_dynamic_virtual_window_radius_respects_rendered_row_height() -> None:
    """Unified modified-heavy diffs should use a smaller radius than split ones."""

    modified_patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class UnifiedApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class SplitApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    unified_app = UnifiedApp()
    async with unified_app.run_test(size=(100, 24)) as pilot:
        diff_view = unified_app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        diff = parse_patch(modified_patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        unified_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

    split_app = SplitApp()
    async with split_app.run_test(size=(100, 24)) as pilot:
        diff_view = split_app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        diff = parse_patch(modified_patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        split_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

    assert unified_radius < split_radius


@pytest.mark.asyncio
async def test_dynamic_virtual_window_shift_margin_scales_with_adaptive_radius() -> (
    None
):
    """Adaptive shift margins should derive from the effective adaptive radius."""

    patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        diff = parse_patch(patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        radius = _virtual_mod._effective_virtual_window_radius(diff_view)
        assert _virtual_mod._effective_virtual_window_shift_margin(diff_view) == max(
            1, radius // diff_view.DYNAMIC_WINDOW_SHIFT_DIVISOR
        )


@pytest.mark.asyncio
async def test_custom_virtual_window_radius_override_is_respected() -> None:
    """Instance overrides should bypass adaptive radius tuning."""

    patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 7

        diff = parse_patch(patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        assert _virtual_mod._effective_virtual_window_radius(diff_view) == 7


@pytest.mark.asyncio
async def test_large_diff_uses_windowed_rendering_and_shifts_with_cursor() -> None:
    """Large diffs should render in a moving window instead of mounting all lines."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,41 @@\n{context_lines}\n+added_line"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        # Lower thresholds for deterministic test behavior.
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")

        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is True
        assert len(diff_view.query(".-virtual-buffer")) > 0

        # Move cursor deep into the file, which should shift the virtual window.
        diff_view.cursor_line = 20
        await pilot.pause()
        await pilot.pause()

        assert diff_view._virt.window_start > 0
        assert diff_view._virt.window_start <= 20 <= diff_view._virt.window_end

        rendered_line = diff_view._get_line_container(20)
        assert rendered_line is not None


@pytest.mark.asyncio
async def test_cursor_movement_uses_cached_widgets_after_render() -> None:
    """Cursor movement should not need fresh DOM queries after render completes."""

    patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        def fail_query(*args, **kwargs):
            raise AssertionError("cursor movement should use cached widgets")

        diff_view.query = fail_query  # type: ignore[method-assign]
        diff_view.query_one = fail_query  # type: ignore[method-assign]

        await pilot.press("j")
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.active_pane == "new"
        assert diff_view.cursor_column == 1


@pytest.mark.asyncio
async def test_horizontal_cursor_move_updates_only_active_widget() -> None:
    """Horizontal motions should not rebuild the inactive split-pane widget."""

    patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("j")
        await pilot.pause()

        old_code = diff_view.query_one("#line-1-old .code-content", Static)
        new_code = diff_view.query_one("#line-1-new .code-content", Static)

        old_updates = {"count": 0}
        new_updates = {"count": 0}
        original_old_update = old_code.update
        original_new_update = new_code.update

        def counted_old_update(*args, **kwargs):
            old_updates["count"] += 1
            return original_old_update(*args, **kwargs)

        def counted_new_update(*args, **kwargs):
            new_updates["count"] += 1
            return original_new_update(*args, **kwargs)

        old_code.update = counted_old_update  # type: ignore[method-assign]
        new_code.update = counted_new_update  # type: ignore[method-assign]

        await pilot.press("l")
        await pilot.pause()

        assert diff_view.active_pane == "new"
        assert old_updates["count"] == 0
        assert new_updates["count"] == 1


@pytest.mark.asyncio
async def test_next_word_cross_line_uses_single_batched_cursor_pipeline() -> None:
    """`w` across lines should update cursor rendering once per affected line."""

    patch = """@@ -1,2 +1,2 @@
 line1
 line2"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_column = len("line1") - 1
        await pilot.pause()

        updates = {"count": 0}
        original_update_line_cursor = diff_view._update_line_cursor

        def counted_update_line_cursor(line_idx: int) -> None:
            updates["count"] += 1
            original_update_line_cursor(line_idx)

        diff_view._update_line_cursor = counted_update_line_cursor  # type: ignore[method-assign]

        await pilot.press("w")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.cursor_column == 0
        assert updates["count"] == 2


@pytest.mark.asyncio
async def test_next_word_cross_line_does_not_copy_row_slices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`w` should scan following rows without copying the rendered row list."""

    patch = """@@ -1,2 +1,2 @@
 alpha
 beta"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class NoSliceRows(list):
        def __getitem__(self, index):
            if isinstance(index, slice):
                raise AssertionError("word motion should not copy row slices")
            return super().__getitem__(index)

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        rows = NoSliceRows(diff_view._rows_unified)
        monkeypatch.setattr(diff_view, "_rows_for_current_mode", lambda: rows)
        diff_view.cursor_column = len("alpha") - 1

        _cursor_mod._next_word_once(diff_view)

        assert diff_view.cursor_line == 1
        assert diff_view.cursor_column == 0


@pytest.mark.asyncio
async def test_visual_selection_cache_tracks_rendered_window_only() -> None:
    """Visual selection cache should only contain currently rendered line indices."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 51))
    patch = f"@@ -1,50 +1,50 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("V")
        await pilot.pause()

        if diff_view._visual_selection_specs:
            assert (
                min(diff_view._visual_selection_specs) >= diff_view._virt.window_start
            )
            assert max(diff_view._visual_selection_specs) <= diff_view._virt.window_end

        diff_view.cursor_line = 20
        await pilot.pause()
        await pilot.pause()

        await pilot.press("j")
        await pilot.pause()

        if diff_view._visual_selection_specs:
            assert (
                min(diff_view._visual_selection_specs) >= diff_view._virt.window_start
            )
            assert max(diff_view._visual_selection_specs) <= diff_view._virt.window_end


@pytest.mark.asyncio
async def test_medium_unified_diff_uses_blocks_without_virtualization() -> None:
    """Medium unified diffs should use grouped blocks before full virtualization kicks in."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 151))
    patch = f"@@ -1,150 +1,150 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view.query(".diff-block .code-content")) < 150


@pytest.mark.asyncio
async def test_medium_split_diff_uses_blocks_without_virtualization() -> None:
    """Medium split diffs should use grouped blocks before full virtualization kicks in."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 151))
    patch = f"@@ -1,150 +1,150 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view.query(".split-block .code-content")) < 300


@pytest.mark.asyncio
async def test_medium_unified_diff_uses_hunk_sized_blocks_without_chunk_splitting() -> (
    None
):
    """Medium unified diffs should group an entire hunk into one block."""

    first_hunk = "\n".join(f" line{i}" for i in range(1, 151))
    second_hunk = "\n".join(f" line{i}" for i in range(300, 450))
    patch = f"@@ -1,150 +1,150 @@\n{first_hunk}\n@@ -300,150 +300,150 @@\n{second_hunk}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        assert len(diff_view.query(".diff-block")) == 2


@pytest.mark.asyncio
async def test_medium_split_diff_uses_hunk_sized_blocks_without_chunk_splitting() -> (
    None
):
    """Medium split diffs should group an entire hunk into one split block."""

    first_hunk = "\n".join(f" line{i}" for i in range(1, 151))
    second_hunk = "\n".join(f" line{i}" for i in range(300, 450))
    patch = f"@@ -1,150 +1,150 @@\n{first_hunk}\n@@ -300,150 +300,150 @@\n{second_hunk}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        split_blocks = list(diff_view.query(".split-block"))
        hunk_headers = list(diff_view.query(".hunk-header"))
        assert len(split_blocks) == 2
        assert len(hunk_headers) == 2

        first_block = split_blocks[0]
        second_header = hunk_headers[1]
        second_block = split_blocks[1]

        assert first_block.region.height == 150
        assert (
            second_header.region.y == first_block.region.y + first_block.region.height
        )
        assert (
            second_block.region.y
            == second_header.region.y + second_header.region.height
        )


@pytest.mark.asyncio
async def test_virtualized_unified_mode_groups_simple_lines_into_blocks() -> None:
    """Large unified diffs should group simple visible lines into block widgets."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view._unified_blocks_by_line) == rendered_line_count
        assert len(diff_view.query(".diff-block .code-content")) < rendered_line_count
        assert len(diff_view.query(".diff-block-anchor")) == 0


@pytest.mark.asyncio
async def test_virtualized_unified_mode_groups_modified_lines_into_blocks() -> None:
    """Large unified diffs should group modified lines into unified block widgets."""

    patch = "@@ -1,40 +1,40 @@\n" + "\n".join(f"-old{i}\n+new{i}" for i in range(1, 41))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view._unified_blocks_by_line) == rendered_line_count

        block = diff_view._unified_blocks_by_line[rendered_start]
        assert isinstance(block, UnifiedDiffBlock)
        assert "-" in block._annotations.numbers[0].plain
        assert "+" in block._annotations.numbers[1].plain

        visual = block._code._render()
        assert isinstance(visual, LineContent)
        old_line = visual.code_lines[0]
        new_line = visual.code_lines[1]
        assert old_line is not None
        assert new_line is not None
        assert "old" in old_line.plain
        assert "new" in new_line.plain
        assert len(visual.code_lines) == rendered_line_count * 2
        assert len(block._annotations.numbers) == rendered_line_count * 2


@pytest.mark.asyncio
async def test_virtualized_split_mode_groups_simple_lines_into_blocks() -> None:
    """Large split diffs should group simple visible lines into split block widgets."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view._split_blocks_by_line) == rendered_line_count
        assert (
            len(diff_view.query(".split-block .code-content")) < rendered_line_count * 2
        )
        assert len(diff_view.query(".diff-block-anchor")) == 0


@pytest.mark.asyncio
async def test_virtualized_split_mode_groups_modified_lines_into_blocks() -> None:
    """Large split diffs should group modified lines into split block widgets."""

    patch = "@@ -1,40 +1,40 @@\n" + "\n".join(f"-old{i}\n+new{i}" for i in range(1, 41))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view._split_blocks_by_line) == rendered_line_count

        block = diff_view._split_blocks_by_line[rendered_start]
        assert isinstance(block, SplitDiffBlock)
        assert "-" in block._left_annotations.numbers[0].plain
        assert "+" in block._right_annotations.numbers[0].plain

        left_visual = block._left_code._render()
        right_visual = block._right_code._render()
        assert isinstance(left_visual, LineContent)
        assert isinstance(right_visual, LineContent)
        left_line = left_visual.code_lines[0]
        right_line = right_visual.code_lines[0]
        assert left_line is not None
        assert right_line is not None
        assert "old" in left_line.plain
        assert "new" in right_line.plain


@pytest.mark.asyncio
async def test_grouped_block_cursor_scroll_uses_row_offsets_without_line_anchors() -> (
    None
):
    """Cursor scrolling within a grouped block should not depend on per-line anchor widgets."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 121))
    patch = f"@@ -1,120 +1,120 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 40
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert len(diff_view.query(".diff-block-anchor")) == 0
        assert diff_view.scroll_y == 0

        diff_view.cursor_line = 20
        await pilot.pause()
        await pilot.pause()

        assert diff_view.scroll_y > 0


@pytest.mark.asyncio
async def test_virtualized_auto_mode_groups_simple_lines_into_unified_blocks() -> None:
    """Large auto-mode diffs should use unified blocks when layout resolves unified."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 40)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert diff_view.split is False
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view._unified_blocks_by_line) == rendered_line_count
        assert len(diff_view.query(".diff-block .code-content")) < rendered_line_count


@pytest.mark.asyncio
async def test_virtualized_auto_mode_groups_simple_lines_into_split_blocks() -> None:
    """Large auto-mode diffs should use split blocks when layout resolves split."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert diff_view.split is True
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view._split_blocks_by_line) == rendered_line_count
        assert (
            len(diff_view.query(".split-block .code-content")) < rendered_line_count * 2
        )


@pytest.mark.asyncio
async def test_grouped_unified_virtual_window_shift_preserves_surviving_block_identity() -> (
    None
):
    """Small grouped unified shifts should keep unaffected middle blocks mounted."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 301))
    patch = f"@@ -1,300 +1,300 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 80
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 10

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        original_block = diff_view._unified_blocks_by_line[130]

        diff_view.cursor_line = 151
        await pilot.pause()
        await pilot.pause()

        assert diff_view._unified_blocks_by_line[130] is original_block


@pytest.mark.asyncio
async def test_grouped_split_virtual_window_shift_preserves_surviving_block_identity() -> (
    None
):
    """Small grouped split shifts should keep unaffected middle blocks mounted."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 301))
    patch = f"@@ -1,300 +1,300 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 80
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 10

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        original_block = diff_view._split_blocks_by_line[130]

        diff_view.cursor_line = 151
        await pilot.pause()
        await pilot.pause()

        assert diff_view._split_blocks_by_line[130] is original_block


@pytest.mark.asyncio
async def test_grouped_unified_virtual_window_shift_avoids_full_rerender() -> None:
    """Grouped unified window shifts should avoid falling back to full `_render_diff()`."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1
        assert len(diff_view.query(".diff-block")) >= 1

        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls
        assert diff_view._virt.rendered_start == diff_view._virt.window_start


@pytest.mark.asyncio
async def test_grouped_split_virtual_window_shift_avoids_full_rerender() -> None:
    """Grouped split window shifts should avoid falling back to full `_render_diff()`."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1
        assert len(diff_view.query(".split-block")) >= 1

        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls
        assert diff_view._virt.rendered_start == diff_view._virt.window_start


@pytest.mark.asyncio
async def test_cursor_ui_flush_coalesces_multiple_requests_in_same_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor-side UI refresh requests should merge into one flush."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")
        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        flush_calls = {"count": 0}
        grouped_calls: list[set[int]] = []
        search_calls = {"count": 0}
        status_calls = {"count": 0}

        original_flush = diff_view._flush_queued_cursor_ui_updates
        original_grouped = _blocks_mod._refresh_grouped_blocks_for_lines
        original_search = _search_mod.sync_match_index_to_cursor
        original_status = diff_view._update_status_line

        def counted_flush() -> None:
            flush_calls["count"] += 1
            original_flush()

        def counted_grouped(view, lines: set[int]) -> bool:
            grouped_calls.append(set(lines))
            return original_grouped(view, lines)

        def counted_search(view: DiffView) -> None:
            search_calls["count"] += 1
            original_search(view)

        def counted_status() -> None:
            status_calls["count"] += 1
            original_status()

        diff_view._flush_queued_cursor_ui_updates = counted_flush  # type: ignore[method-assign]
        monkeypatch.setattr(
            _blocks_mod, "_refresh_grouped_blocks_for_lines", counted_grouped
        )
        monkeypatch.setattr(_search_mod, "sync_match_index_to_cursor", counted_search)
        diff_view._update_status_line = counted_status  # type: ignore[method-assign]

        diff_view._queue_cursor_ui_flush(
            cursor_lines={0},
            sync_search_match=True,
            update_status_line=True,
        )
        diff_view._queue_cursor_ui_flush(
            cursor_lines={1},
            sync_search_match=True,
            update_status_line=True,
        )

        assert diff_view._cursor_ui.flush_pending is True

        await pilot.pause()
        await pilot.pause()

        assert flush_calls["count"] == 1
        assert grouped_calls == [{0, 1}]
        assert search_calls["count"] == 1
        assert status_calls["count"] == 1


@pytest.mark.asyncio
async def test_half_page_scroll_flushes_cursor_ui_immediately_after_scroll_adjustment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half-page cursor scrolls should repaint immediately after viewport adjustment."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        monkeypatch.setattr(
            _hl_mod, "_queue_highlight_diff", lambda filename, diff: None
        )

        diff = parse_patch(patch, "test.py")
        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert len(diff_view.query(".diff-block")) >= 1

        diff_view.cursor_line = 60
        await pilot.pause()
        await pilot.pause()

        await diff_view.action_half_page_down()

        assert diff_view._cursor_ui.flush_pending is False
        assert diff_view._cursor_ui.dirty_lines == set()
        await pilot.pause()
        await pilot.pause()

        diff_view.cursor_line = 80
        await pilot.pause()
        await pilot.pause()

        await diff_view.action_half_page_up()

        assert diff_view._cursor_ui.flush_pending is False
        assert diff_view._cursor_ui.dirty_lines == set()


@pytest.mark.asyncio
async def test_unified_block_refresh_reuses_cached_static_row_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unified grouped blocks should reuse cached static row data on cursor refresh."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        monkeypatch.setattr(
            _hl_mod, "_queue_highlight_diff", lambda filename, diff: None
        )

        static_calls = {"count": 0}
        base_calls = {"count": 0}
        original_static = _blocks_mod._compute_unified_block_static_rows
        original_base = diff_view._compute_base_code_content

        def counted_static(view, line):
            static_calls["count"] += 1
            return original_static(view, line)

        def counted_base(line, *, side="auto", empty_fallback=""):
            base_calls["count"] += 1
            return original_base(
                line,
                side=side,
                empty_fallback=empty_fallback,
            )

        monkeypatch.setattr(
            _blocks_mod, "_compute_unified_block_static_rows", counted_static
        )
        diff_view._compute_base_code_content = counted_base  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        initial_static = static_calls["count"]
        initial_base = base_calls["count"]
        assert initial_static > 0
        assert initial_base > 0
        assert len(diff_view.query(".diff-block")) >= 1

        diff_view.cursor_line = 10
        await pilot.pause()
        await pilot.pause()
        diff_view.cursor_line = 11
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        assert static_calls["count"] == initial_static
        assert base_calls["count"] == initial_base


@pytest.mark.asyncio
async def test_split_block_refresh_reuses_cached_static_row_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Split grouped blocks should reuse cached static row data on cursor refresh."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        monkeypatch.setattr(
            _hl_mod, "_queue_highlight_diff", lambda filename, diff: None
        )

        static_calls = {"count": 0}
        base_calls = {"count": 0}
        original_static = _blocks_mod._compute_split_block_static_row
        original_base = diff_view._compute_base_code_content

        def counted_static(view, line):
            static_calls["count"] += 1
            return original_static(view, line)

        def counted_base(line, *, side="auto", empty_fallback=""):
            base_calls["count"] += 1
            return original_base(
                line,
                side=side,
                empty_fallback=empty_fallback,
            )

        monkeypatch.setattr(
            _blocks_mod, "_compute_split_block_static_row", counted_static
        )
        diff_view._compute_base_code_content = counted_base  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        initial_static = static_calls["count"]
        initial_base = base_calls["count"]
        assert initial_static > 0
        assert initial_base > 0
        assert len(diff_view.query(".split-block")) >= 1

        diff_view.cursor_line = 10
        await pilot.pause()
        await pilot.pause()
        diff_view.cursor_line = 11
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        assert static_calls["count"] == initial_static
        assert base_calls["count"] == initial_base


@pytest.mark.asyncio
async def test_scroll_coalesces_pending_virtual_window_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid scroll updates should coalesce to the latest viewport target."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        calls = {"count": 0}
        started = threading.Event()
        unblock = threading.Event()
        original = _virtual_mod._render_virtual_window_and_finalize

        async def blocking_render_finalize(view) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                started.set()
                await asyncio.to_thread(unblock.wait, 1.0)
            await original(view)

        monkeypatch.setattr(
            _virtual_mod,
            "_render_virtual_window_and_finalize",
            blocking_render_finalize,
        )

        diff_view.scroll_to(y=120, animate=False)
        await pilot.pause()
        await pilot.pause()

        assert started.wait(timeout=1.0) is True

        diff_view.scroll_to(y=360, animate=False)
        await pilot.pause()
        await pilot.pause()

        queued_center = await wait_until(
            lambda: diff_view._virt.coalesced_center,
            timeout=1.0,
        )
        assert queued_center is not None

        unblock.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await wait_until(
            lambda: (
                not diff_view._virt.render_pending
                and diff_view._virt.rendered_start
                <= diff_view._viewport_center_line()
                <= diff_view._virt.rendered_end
            ),
            timeout=5.0,
        )

        assert 2 <= calls["count"] <= 3
        final_center = diff_view._viewport_center_line()
        assert (
            diff_view._virt.rendered_start
            <= final_center
            <= diff_view._virt.rendered_end
        )


@pytest.mark.asyncio
async def test_cursor_coalesces_pending_virtual_window_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid cursor jumps should coalesce to the latest virtual-window target."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()

        calls = {"count": 0}
        started = threading.Event()
        unblock = threading.Event()
        original = _virtual_mod._render_virtual_window_and_finalize

        async def blocking_render_finalize(view) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                started.set()
                await asyncio.to_thread(unblock.wait, 1.0)
            await original(view)

        monkeypatch.setattr(
            _virtual_mod,
            "_render_virtual_window_and_finalize",
            blocking_render_finalize,
        )

        diff_view.cursor_line = 80
        await pilot.pause()
        await pilot.pause()

        assert started.wait(timeout=1.0) is True

        diff_view.cursor_line = 360
        await pilot.pause()
        await pilot.pause()

        queued_center = diff_view._virt.coalesced_center
        assert queued_center == 360

        unblock.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert calls["count"] == 2
        assert diff_view._virt.rendered_start <= 360 <= diff_view._virt.rendered_end


@pytest.mark.asyncio
async def test_medium_windowed_highlight_tracks_scroll_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Medium non-virtualized diffs should request new highlight windows after scrolling."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 221))
    patch = f"@@ -1,220 +1,220 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    range_calls = {"count": 0}
    original_range = diff_highlight_module.highlight_lines_for_diff_range

    def counted_range(*args, **kwargs):
        range_calls["count"] += 1
        return original_range(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff_range", counted_range
    )

    try:
        async with app.run_test(size=(100, 12)) as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "medium.py")
            await diff_view.show_diff("medium.py", diff)
            await pilot.pause()
            await pilot.pause()

            initial_calls = range_calls["count"]
            assert diff_view._virt.active is False
            assert initial_calls >= 1

            diff_view.scroll_to(y=140, animate=False)
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            assert range_calls["count"] > initial_calls
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_virtualized_large_diff_preserves_scroll_height_with_spacer_buffers() -> (
    None
):
    """Large virtualized diffs should keep full scroll height via spacer buffers."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert diff_view._virt.active is True
        assert diff_view.max_scroll_y > 1000
        bottom_buffer = diff_view.query_one("#virtual-buffer-bottom", Static)
        bottom_height = getattr(bottom_buffer.styles.height, "value", 0)
        assert bottom_height > 1000


@pytest.mark.asyncio
async def test_virtualized_window_tracks_scroll_position_without_cursor_moves() -> None:
    """Viewport scrolling should shift the virtual window even when the cursor stays put."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert diff_view.cursor_line == 0
        assert diff_view._virt.window_start == 0

        diff_view.scroll_to(y=120, animate=False)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert diff_view.cursor_line == 0
        assert diff_view.scroll_y > 0
        assert diff_view._virt.window_start > 0
        top_buffer = diff_view.query_one("#virtual-buffer-top", Static)
        top_height = getattr(top_buffer.styles.height, "value", 0)
        assert top_height > 0


@pytest.mark.asyncio
async def test_virtual_window_down_shift_avoids_full_rerender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downward virtual-window shifts should use incremental DOM updates."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1
        monkeypatch.setattr(
            _blocks_mod, "_should_use_unified_block_renderer", lambda view: False
        )

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        # Small downward shift keeps overlap with previous window.
        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()

        # No additional full rerender; only incremental shift path.
        assert render_calls["count"] == baseline_calls


@pytest.mark.asyncio
async def test_virtual_window_up_shift_avoids_full_rerender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upward virtual-window shifts should use incremental DOM updates."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1
        monkeypatch.setattr(
            _blocks_mod, "_should_use_unified_block_renderer", lambda view: False
        )

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        # First move window down with overlap.
        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()
        after_down_calls = render_calls["count"]
        assert after_down_calls == baseline_calls

        # Then move up with overlap.
        diff_view.cursor_line = 2
        await pilot.pause()
        await pilot.pause()

        # Still no additional full rerender; incremental path should handle it.
        assert render_calls["count"] == after_down_calls


@pytest.mark.asyncio
async def test_grouped_virtual_large_jump_mounts_without_scanning_all_hunk_lines() -> (
    None
):
    """Grouped virtual-window jumps should mount from the visible line window."""

    hunks: list[str] = []
    for hunk_index in range(50):
        start = hunk_index * 100 + 1
        lines = "\n".join(f" line{line_no}" for line_no in range(start, start + 100))
        hunks.append(f"@@ -{start},100 +{start},100 @@\n{lines}")
    patch = "\n".join(hunks)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class CountingLines(list):
        def __iter__(self):
            iterated["count"] += len(self)
            return super().__iter__()

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        iterated = {"count": 0}
        for hunk in diff.hunks:
            hunk.lines = CountingLines(hunk.lines)

        diff_view.cursor_line = 4000
        await wait_until(
            lambda: (
                not diff_view._virt.render_pending
                and diff_view._virt.window_start <= 4000 <= diff_view._virt.window_end
            ),
            timeout=5.0,
        )

        assert diff_view._virt.window_start <= 4000 <= diff_view._virt.window_end
        window_size = diff_view._virt.window_end - diff_view._virt.window_start + 1
        assert iterated["count"] <= window_size * 2


@pytest.mark.asyncio
async def test_virtual_window_render_uses_visible_hunk_range() -> None:
    """Virtual renders should not scan every hunk to find the visible window."""

    hunks: list[str] = []
    for hunk_index in range(500):
        start = hunk_index * 20 + 1
        lines = "\n".join(f" line{line_no}" for line_no in range(start, start + 20))
        hunks.append(f"@@ -{start},20 +{start},20 @@\n{lines}")
    patch = "\n".join(hunks)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class CountingHunks(list):
        def __iter__(self):
            iterated["count"] += len(self)
            return super().__iter__()

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        iterated = {"count": 0}
        diff.hunks = CountingHunks(diff.hunks)

        diff_view.cursor_line = 5000
        await wait_until(
            lambda: (
                not diff_view._virt.render_pending
                and diff_view._virt.window_start <= 5000 <= diff_view._virt.window_end
            ),
            timeout=5.0,
        )

        assert iterated["count"] < len(diff.hunks) // 10


@pytest.mark.asyncio
async def test_grouped_virtual_large_jump_uses_full_window_rerender() -> None:
    """No-overlap grouped jumps should use the cheaper full window rerender path."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 101))
    patch = f"@@ -1,100 +1,100 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        diff_view.cursor_line = 80
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls + 1


@pytest.mark.asyncio
async def test_virtualized_show_diff_does_not_build_content_for_layout_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large virtualized diffs should avoid content construction for width scans."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 5001))
    patch = f"@@ -1,5000 +1,5000 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        base_calls = {"count": 0}
        original_base = diff_view._compute_base_code_content

        def counted_base(line, *, side="auto", empty_fallback=""):
            base_calls["count"] += 1
            return original_base(line, side=side, empty_fallback=empty_fallback)

        diff_view._compute_base_code_content = counted_base  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        assert base_calls["count"] < 100


@pytest.mark.asyncio
async def test_virtualized_show_diff_builds_only_active_row_projection() -> None:
    """Large diffs should not build row metadata for an inactive layout mode."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 5001))
    patch = f"@@ -1,5000 +1,5000 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        assert diff_view._rows_unified
        assert diff_view._row_lookup_unified
        assert diff_view._rows_split == []
        assert diff_view._row_lookup_split == {}

        diff_view.mode = "split"
        await pilot.pause()

        assert diff_view._rows_split
        assert diff_view._row_lookup_split


def test_active_row_projection_reuses_planned_lines_without_hunk_rescan() -> None:
    """Active row metadata should be built from the existing diff plan lines."""

    class NoIterHunks(list[DiffHunk]):
        def __iter__(self):
            raise AssertionError("row projection should reuse planned lines")

    diff = FileDiff(
        filename="test.py",
        hunks=[
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
        ],
    )
    plan = _plan_mod.build_diff_plan(diff, include_rendered_rows=False)
    diff.hunks = NoIterHunks(diff.hunks)

    diff_view = DiffView(mode="unified")
    diff_view._diff = diff
    diff_view._all_lines = plan.all_lines
    diff_view._hunk_index_by_line = plan.hunk_index_by_line

    _render_mod._ensure_rendered_rows_for_mode(diff_view, split=False)

    assert [row.anchor_id for row in diff_view._rows_unified] == [
        "line-0",
        "line-1-old",
        "line-1-new",
    ]
    assert diff_view._rows_split == []


@pytest.mark.asyncio
async def test_show_diff_reuses_planned_width_metrics_for_initial_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initial layout should not rescan all lines after the diff plan is built."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 5001))
    patch = f"@@ -1,5000 +1,5000 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    import rit.ui.widgets.diff_layout as diff_layout_module

    def fail_width_scan(*args, **kwargs):
        raise AssertionError("show_diff should use planned width metrics")

    monkeypatch.setattr(diff_layout_module, "code_widths_for_layout", fail_width_scan)
    monkeypatch.setattr(
        diff_layout_module,
        "can_fit_auto_split_content",
        fail_width_scan,
    )

    app = TestApp()
    async with app.run_test(size=(120, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        assert diff_view.current_file == "big.py"


@pytest.mark.asyncio
async def test_hunk_navigation_uses_line_range_lookup_without_row_scan() -> None:
    """Jumping hunks in huge diffs should not scan every rendered row."""

    hunks: list[str] = []
    for hunk_index in range(120):
        start = hunk_index * 20 + 1
        lines = "\n".join(f" line{line_no}" for line_no in range(start, start + 20))
        hunks.append(f"@@ -{start},20 +{start},20 @@\n{lines}")
    patch = "\n".join(hunks)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class CountingRows(list):
        def __iter__(self):
            iterated["count"] += len(self)
            return super().__iter__()

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        iterated = {"count": 0}
        diff_view._rows_unified = CountingRows(diff_view._rows_unified)

        diff_view.next_hunk()
        await pilot.pause()

        assert iterated["count"] == 0


def test_scroll_to_hunk_uses_direct_hunk_range_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Virtual hunk jumps should index the planned hunk range directly."""

    class NoIterHunkRanges(list):
        def __iter__(self):
            raise AssertionError("hunk jump should not scan planned hunk ranges")

    class VirtualState:
        active = True
        window_start = 20
        window_end = 35

    class HunkJumpView:
        _diff = FileDiff(
            filename="big.py",
            hunks=[
                DiffHunk(1, 1, 1, 1),
                DiffHunk(20, 1, 20, 1),
                DiffHunk(40, 1, 40, 1),
            ],
        )
        _virt = VirtualState()
        _hunk_line_ranges = NoIterHunkRanges([(0, 0, 0), (1, 20, 25), (2, 40, 45)])
        _hunk_header_top_offsets = [0, 20, 40]

    scrolled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        _cursor_mod,
        "_scroll_to_vertical_span",
        lambda _view, top, bottom, **_kwargs: scrolled.append((top, bottom)),
    )

    _cursor_mod._scroll_to_hunk(HunkJumpView(), 1)

    assert scrolled == [(20, 21)]
