from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive, var
from textual.widget import AwaitMount, Widget
from textual.widgets import Input, Static

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import (
    FileViewedState,
    PendingReviewComment,
    PRComment,
    PRFile,
    ReviewThread,
)
from rit.ui.messages import Flash
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_cursor as _cursor
from rit.ui.widgets import diff_cursor_side as _cursor_side
from rit.ui.widgets import diff_cursor_update as _cursor_update
from rit.ui.widgets import diff_fold_state as _fold_state
from rit.ui.widgets import diff_folding as _folding
from rit.ui.widgets import diff_full_file_preview as _full_preview
from rit.ui.widgets import diff_highlight as _hl
from rit.ui.widgets import diff_layout as _layout
from rit.ui.widgets import diff_location as _location
from rit.ui.widgets import diff_plan as _plan
from rit.ui.widgets import diff_render as _render
from rit.ui.widgets import diff_selection as _selection
from rit.ui.widgets import diff_virtual as _virtual
from rit.ui.widgets import diff_visual_mode as _visual_mode
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.comment_editor import InlineCommentEditor
from rit.ui.widgets.diff_plan_cache import DiffPlanCache, publish_line_metadata
from rit.ui.widgets.diff_search import DiffSearchSession, SearchCursor, SearchResult
from rit.ui.widgets.diff_search_policy import (
    search_activation_placement_update,
    search_close_update,
    search_reveal_update,
    search_start_update,
    search_submitted_input_update,
)
from rit.ui.widgets.diff_search_types import SearchActivationUpdate
from rit.ui.widgets.diff_types import (
    DEFAULT_DIFF_LAYOUT,
    CursorUIState,
    DiffSearchMatch,
    HighlightState,
    RenderedRow,
    SplitBlockLineStaticData,
    SplitDiffBlock,
    UnifiedBlockRowStaticData,
    UnifiedDiffBlock,
    VirtualState,
)

if TYPE_CHECKING:
    from rit.state.store import PRStore


__all__ = (
    "DiffView",
    "SplitDiffBlock",
    "UnifiedDiffBlock",
)


_RENDER_REQUEST_CONTEXT: ContextVar[int | None] = ContextVar(
    "diff_view_render_request", default=None
)


async def _finish_to_thread_on_cancel[**P, R](
    function: Callable[P, R],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


_DIFF_MODE_LABELS: dict[str, str] = {
    "auto": "Auto",
    "split": "Split",
    "unified": "Unified",
}


class DiffView(VerticalScroll):
    can_focus = True
    LAYOUT = DEFAULT_DIFF_LAYOUT
    PREVIEW_PREFIX_WIDTH = _render.PREVIEW_PREFIX_WIDTH

    VIRTUALIZE_LINE_THRESHOLD = 800
    BLOCK_RENDER_LINE_THRESHOLD: int = 40
    VIRTUAL_WINDOW_RADIUS = 120
    VIRTUAL_WINDOW_SHIFT_MARGIN = 40
    WINDOW_HIGHLIGHT_BUFFER = 60
    UNIFIED_BLOCK_CHUNK_SIZE = 64
    COMPLEX_DIFF_RATIO_THRESHOLD = 0.2
    DEFAULT_WINDOW_ROWS_MULTIPLIER = 2.0
    COMPLEX_DIFF_WINDOW_ROWS_MULTIPLIER = 0.75
    DYNAMIC_WINDOW_SHIFT_DIVISOR = 7
    MIN_DYNAMIC_WINDOW_RADIUS = 12
    INLINE_COMMENT_EDITOR_HEIGHT = 9
    FILE_COMMENT_EDITOR_HEIGHT = 9

    DEFAULT_CSS = Path(__file__).with_suffix(".tcss").read_text()

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("j", "scroll_down", "Scroll Down", show=False),
        Binding("k", "scroll_up", "Scroll Up", show=False),
        Binding("h", "cursor_left", "Left", show=False),
        Binding("l", "cursor_right", "Right", show=False),
        Binding("0", "start_of_line", "Start of Line", show=False),
        Binding("^", "first_non_blank", "First Non Blank", show=False),
        Binding("down", "scroll_down", "Scroll Down", show=False),
        Binding("up", "scroll_up", "Scroll Up", show=False),
        Binding("g", "scroll_home", "Go to Top", show=False),
        Binding("G", "scroll_end", "Go to Bottom", show=False),
        Binding("ctrl+d", "half_page_down", "Page Down", show=False),
        Binding("ctrl+u", "half_page_up", "Page Up", show=False),
        Binding("v", "toggle_visual", "Visual", show=True),
        Binding("V", "toggle_visual_line", "Visual Line", show=True),
        Binding("y", "yank", "Yank", show=False),
        Binding("Y", "copy_file_path", "Copy File Path", show=False),
        Binding("escape", "exit_visual", "Exit Visual", show=False),
        Binding("w", "next_word", "Next Word", show=False),
        Binding("b", "prev_word", "Prev Word", show=False),
        Binding("/", "start_search", "Search", show=False),
        Binding("n", "next_search_match", "Next Match", show=False),
        Binding("N", "prev_search_match", "Prev Match", show=False),
        Binding("$", "end_of_line", "End of Line", show=False),
        Binding("}", "next_paragraph", "Paragraphs", key_display="{/}"),
        Binding("{", "prev_paragraph", "Prev Paragraph", show=False),
        Binding("r", "toggle_resolve", "Resolve", show=False),
        Binding("|", "cycle_diff_mode", "Mode", show=False),
        Binding("z", "center_cursor", "Center", show=False),
        Binding("p", "toggle_full_file", "Preview", show=False),
    ]

    @dataclass
    class HunkNavigated(Message):
        hunk_index: int
        total_hunks: int

    @dataclass
    class CrossFileComment(Message):
        direction: Literal[1, -1]  # 1 = forward, -1 = backward

    @dataclass
    class CursorLineChanged(Message):
        line_index: int

    @dataclass
    class FullFilePreviewRequested(Message):
        filename: str
        view_revision: tuple[int, int]

    @dataclass
    class FullFilePreviewRestored(Message):
        filename: str

    mode: reactive[Literal["split", "unified", "auto"]] = reactive("auto")
    split: var[bool] = var(True, toggle_class="-split")
    active_pane: var[Literal["old", "new"]] = var("new")
    cursor_pane: var[Literal["old", "new"]] = var("new")
    current_file: var[str | None] = var(None)
    current_hunk_index: var[int] = var(0)
    show_line_numbers: var[bool] = var(True)
    word_diff_enabled: var[bool] = var(True)

    visual_mode: var[bool] = var(False, toggle_class="-visual")
    visual_type: var[Literal["char", "line"]] = var("char")
    visual_anchor_line: var[int | None] = var(None)
    visual_anchor_column: var[int | None] = var(None)
    cursor_line: var[int] = var(0)
    cursor_column: var[int] = var(0)
    _comment_cursor_index: var[int] = var(0)

    _diff: var[FileDiff | None] = var(None)
    _file: var[PRFile | None] = var(None)

    _all_lines: list[DiffLine]
    _rows_unified: list[RenderedRow]
    _rows_split: list[RenderedRow]
    _row_lookup_unified: dict[tuple[int, Literal["old", "new", "auto"]], int]
    _row_lookup_split: dict[int, int]

    def __init__(
        self,
        store: PRStore | None = None,
        *,
        mode: Literal["split", "unified", "auto"] = "auto",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.store = store
        self._all_lines = []
        self._rows_unified = []
        self._rows_split = []
        self._row_lookup_unified = {}
        self._row_lookup_split = {}
        self._rows_unified_ready = False
        self._rows_split_ready = False
        self._diff_plan_lock = asyncio.Lock()
        self._diff_plan_cache: DiffPlanCache | None = None
        self._fold_refresh_lock = asyncio.Lock()
        self._fold_worker_active = False
        self._requested_source: FileDiff | None = None
        self._committing_render_token: int | None = None
        self._inline_editor_state: _fold_state.EditorState | None = None
        self._file_editor_state: _fold_state.EditorState | None = None

        self._search = DiffSearchSession(
            self._search_cursor, self._display_search_result
        )

        self._hl_state = HighlightState()
        self._unified_block_static_rows_by_line: dict[
            int, tuple[UnifiedBlockRowStaticData, ...]
        ] = {}
        self._split_block_static_rows_by_line: dict[int, SplitBlockLineStaticData] = {}
        self._base_code_content_cache: dict[
            tuple[int, Literal["old", "new", "auto"], str], Content
        ] = {}
        self._base_code_content_cache_keys_by_line: dict[
            int,
            set[tuple[int, Literal["old", "new", "auto"], str]],
        ] = {}
        self._diff_file_paths: frozenset[str] = frozenset()
        self._file_change_stats: dict[str, tuple[int, int]] = {}
        self._line_index_by_new_number: dict[int, int] = {}
        self._line_index_by_old_number: dict[int, int] = {}
        self._new_line_number_bounds: tuple[int, int] | None = None
        self._line_index_by_file_new_number: dict[tuple[str, int], int] = {}
        self._line_index_by_file_old_number: dict[tuple[str, int], int] = {}
        self._hunk_index_by_line: list[int] = []
        self._modified_line_count: int = 0
        self._total_line_render_height: int = 0

        self._hunk_line_ranges: list[tuple[int, int, int]] = []
        self._hunk_start_line_indices: list[int] = []
        self._hunk_end_line_indices: list[int] = []
        self._hunk_header_top_offsets: list[int] = []
        self._line_top_offsets: list[int] = []
        self._line_heights: list[int] = []
        self._line_bottom_offsets: list[int] = []
        self._virtual_content_height: int = 0

        self._virt = VirtualState()
        self._render_request_token: int = 0
        self._source_diff: FileDiff | None = None
        self._source_line_count: int = 0
        self._folded_file_paths: frozenset[str] = frozenset()
        self._manually_folded_files: set[str] = set()
        self._expanded_viewed_files: set[str] = set()
        self._file_navigation_revision: int = 0
        self._last_navigated_file: str | None = None
        self._suspend_split_state_rerender: bool = False
        self._suspend_scroll_virtual_window_watch: bool = False
        self._syncing_split_scroll: bool = False
        self._split_horizontal_scroll_x: float = 0.0
        self._unified_code_width: int = 1
        self._split_old_code_width: int = 1
        self._split_new_code_width: int = 1
        self._old_line_number_width_value: int = _layout.MIN_LINE_NUMBER_WIDTH
        self._new_line_number_width_value: int = _layout.MIN_LINE_NUMBER_WIDTH

        self._code_widgets_by_line: dict[int, tuple[Static, ...]] = {}
        self._split_scroll_widgets_by_line: dict[int, tuple[Widget, ...]] = {}
        self._unified_blocks_by_line: dict[int, UnifiedDiffBlock] = {}
        self._split_blocks_by_line: dict[int, SplitDiffBlock] = {}
        self._line_widgets_by_index: dict[int, Widget] = {}
        self._row_anchor_widgets: dict[str, Widget] = {}
        self._file_header_widgets: dict[int, Widget] = {}
        self._hunk_header_widgets: dict[int, Widget] = {}
        self._selected_file_header_hunk: int | None = None

        self._search_bar_widget: Horizontal | None = None
        self._search_input_widget: Input | None = None

        self._content_widget: VerticalScroll | None = None

        self._center_padding_widget: Static | None = None
        self._center_padding_height: int = 0

        self._showing_full_file: bool = False
        self._saved_diff: FileDiff | None = None
        self._saved_filename: str | None = None
        self._saved_restore_position: _full_preview.FullFileRestorePosition | None = (
            None
        )

        self._highlighter_prewarm_started: bool = False
        self._cursor_ui = CursorUIState()

        self._visual_selection_specs: dict[
            int,
            tuple[int, int | None, Literal["char", "line"]],
        ] = {}

        self._comment_threads_by_line: dict[int, list[ReviewThread]] = {}
        self._comment_line_indices: list[int] = []
        self._comment_widgets_by_line: dict[int, list[Widget]] = {}
        self._comment_layout_widgets_by_line: dict[int, list[Widget]] = {}
        self._comment_side_by_line: dict[int, Literal["old", "new", "auto"]] = {}
        self._pending_comment_drafts_by_line: dict[int, list[PendingReviewComment]] = {}
        self._collapsed_pending_drafts: dict[int, PendingReviewComment] = {}
        self._pending_comment_widgets_by_line: dict[int, list[Widget]] = {}
        self._pending_comment_layout_widgets_by_line: dict[int, list[Widget]] = {}
        self._pending_file_comment_drafts_by_path: dict[
            str, list[PendingReviewComment]
        ] = {}
        self._file_comment_threads_by_path: dict[str, list[ReviewThread]] = {}
        self._pending_file_comment_widgets_by_hunk: dict[int, list[Widget]] = {}
        self._file_comment_widgets_by_hunk: dict[int, list[Widget]] = {}
        self._file_comment_annotation_widgets_by_hunk: dict[int, list[Widget]] = {}
        self._inline_comment_editor_line_index: int | None = None
        self._inline_comment_editor_target: (
            tuple[str, int, Literal["LEFT", "RIGHT"]] | None
        ) = None
        self._inline_comment_editor_widget: InlineCommentEditor | None = None
        self._inline_comment_editor_layout_widget: Widget | None = None
        self._inline_comment_editor_layout_height = 0
        self._inline_comment_editor_initial_body: str = ""
        self._inline_comment_editor_context: str = ""
        self._inline_comment_editor_draft_index: int | None = None
        self._inline_comment_editor_edit_target: PRComment | None = None
        self._inline_comment_editor_start_line: int | None = None
        self._inline_comment_editor_start_side: Literal["LEFT", "RIGHT"] | None = None
        self._file_comment_editor_hunk_index: int | None = None
        self._file_comment_editor_target: str | None = None
        self._file_comment_editor_widget: InlineCommentEditor | None = None
        self._file_comment_editor_mounted_hunk_index: int | None = None
        self._file_comment_editor_layout_height = 0
        self._pending_comment_jump: str | None = None  # "first" or "last"
        self._comment_layout_split_override: bool | None = None

        self.mode = mode

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="diff-content")
        with Horizontal(id="diff-search-bar"):
            yield Static("/", classes="search-prompt")
            yield Input(id="diff-search-input")

    @property
    def scrollable_content_region(self):
        region = super().scrollable_content_region
        if self._content_widget is not None:
            region = region.shrink(self._content_widget.dock_gutter)
        return region

    def watch_mode(self, new_mode: Literal["split", "unified", "auto"]) -> None:
        if self._suspend_split_state_rerender:
            return
        _render._update_split_state(self)
        self._search.refresh(
            self._all_lines,
            self._rows_for_current_mode() if self._search.query else (),
        )

    def watch_show_line_numbers(self, old_value: bool, new_value: bool) -> None:
        if old_value == new_value or not self.is_mounted or not self._all_lines:
            return

        self._unified_block_static_rows_by_line.clear()
        self._split_block_static_rows_by_line.clear()
        self.run_worker(
            self._run_render_diff_for_request(self._render_request_token),
            exclusive=True,
            name="diff-line-numbers-rerender",
        )

    def watch_word_diff_enabled(self, old_value: bool, new_value: bool) -> None:
        if old_value == new_value or not self.is_mounted:
            return
        if not self._reset_current_diff_highlight_state():
            return

        self.run_worker(
            self._run_render_diff_for_request(self._render_request_token),
            exclusive=True,
            name="diff-word-diff-rerender",
        )

    def on_resize(self) -> None:
        if self._suspend_split_state_rerender:
            return
        was_split = self.split
        _render._update_split_state(self)
        if was_split != self.split:
            self._search.refresh(
                self._all_lines,
                self._rows_for_current_mode() if self._search.query else (),
            )

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if old_value == new_value or self._suspend_scroll_virtual_window_watch:
            return
        _virtual._maybe_update_virtual_window_from_viewport(self)
        if not self._virt.active and _hl._use_windowed_highlight_strategy(
            self, self._diff
        ):
            _hl._ensure_visible_highlight(self)

    def on_mount(self) -> None:
        self.can_focus = True
        self._search_bar_widget = self.query_one("#diff-search-bar", Horizontal)
        self._search_input_widget = self.query_one("#diff-search-input", Input)

        self._content_widget = self.query_one("#diff-content", VerticalScroll)
        if not self._highlighter_prewarm_started:
            self._highlighter_prewarm_started = True
            self.run_worker(
                _hl._prewarm_highlighter(self),
                exclusive=False,
                name="diff-highlight-prewarm",
            )

    def watch_active_pane(
        self,
        old_pane: Literal["old", "new"],
        new_pane: Literal["old", "new"],
    ) -> None:
        if (
            not self._all_lines
            or not self.is_mounted
            or old_pane == new_pane
            or self._cursor_ui.suspend_pane_watch
        ):
            return

        _cursor._clamp_cursor_column_to_current_row(self)
        self.cursor_pane = new_pane
        if self._comment_cursor_index != 0:
            self._comment_cursor_index = 0
            _comments.update_cursor_highlight(self, self.cursor_line, self.cursor_line)

        if not self.visual_mode:
            _cursor._scroll_to_cursor(self)

        update = _cursor_update.active_pane_update(
            cursor_line=self.cursor_line,
            visual_mode=self.visual_mode,
        )
        _cursor._queue_cursor_update(self, update)

    def watch_cursor_line(self, old_line: int, new_line: int) -> None:
        if (
            not self._all_lines
            or not self.is_mounted
            or self._cursor_ui.suspend_line_watch
        ):
            return

        _cursor._clamp_cursor_column_to_current_row(self)
        self._set_file_header_selection(None)
        self._record_file_navigation(self.file_for_line_index(new_line))

        hunk_index = self._get_hunk_index_for_line(new_line)
        if hunk_index is not None and hunk_index != self.current_hunk_index:
            self.current_hunk_index = hunk_index

        _virtual._maybe_update_virtual_window(self, new_line)
        self._comment_cursor_index = 0
        _comments.update_cursor_highlight(self, old_line, new_line)

        if not self.visual_mode:
            _cursor._scroll_to_cursor(self)

        update = _cursor_update.cursor_line_update(
            old_line=old_line,
            new_line=new_line,
            visual_mode=self.visual_mode,
        )
        _cursor._queue_cursor_update(self, update)
        self.post_message(self.CursorLineChanged(line_index=new_line))

    def watch_cursor_column(self, old_col: int, new_col: int) -> None:
        if (
            not self._all_lines
            or not self.is_mounted
            or self._cursor_ui.suspend_column_watch
        ):
            return

        if self.cursor_line < len(self._all_lines):
            text = self._get_cursor_text()
            update = _cursor_update.cursor_column_update(
                cursor_line=self.cursor_line,
                new_column=new_col,
                text_length=len(text),
                visual_mode=self.visual_mode,
            )
            if update.corrected_column is not None:
                self.cursor_column = update.corrected_column
                return

            _cursor._queue_cursor_update(self, update)
            if update.scroll_horizontal:
                _cursor._scroll_to_cursor_horizontal(self)

    def watch_visual_mode(self, old_mode: bool, new_mode: bool) -> None:
        update = _visual_mode.visual_mode_ui_update(
            visual_mode=new_mode,
            visual_type=self.visual_type,
            cursor_line=self.cursor_line,
        )
        self.app.sub_title = update.sub_title
        if update.selection_refresh_lines:
            _selection._update_selection_highlighting(
                self, set(update.selection_refresh_lines)
            )
        if update.clear_selection:
            old_selection_specs = self._visual_selection_specs
            self._visual_selection_specs = {}
            for line_idx in old_selection_specs:
                _selection._clear_line_selection(self, line_idx)

    def watch_visual_type(
        self, old_type: Literal["char", "line"], new_type: Literal["char", "line"]
    ) -> None:
        update = _visual_mode.visual_type_ui_update(
            visual_mode=self.visual_mode,
            visual_type=new_type,
            cursor_line=self.cursor_line,
        )
        if update.sub_title is not None:
            self.app.sub_title = update.sub_title
        queue_update = _visual_mode.visual_queue_update(update)
        if queue_update.selection_dirty_lines is not None:
            _cursor._queue_cursor_ui_flush(
                self,
                selection_dirty_lines=queue_update.selection_dirty_lines,
            )

    def watch_visual_anchor_line(
        self, old_anchor: int | None, new_anchor: int | None
    ) -> None:
        self._queue_visual_anchor_ui_update()

    def watch_visual_anchor_column(
        self, old_col: int | None, new_col: int | None
    ) -> None:
        self._queue_visual_anchor_ui_update()

    def _queue_visual_anchor_ui_update(self) -> None:
        update = _visual_mode.visual_anchor_ui_update(
            visual_mode=self.visual_mode,
            cursor_line=self.cursor_line,
        )
        queue_update = _visual_mode.visual_queue_update(update)
        if queue_update.selection_dirty_lines is not None:
            _cursor._queue_cursor_ui_flush(
                self,
                selection_dirty_lines=queue_update.selection_dirty_lines,
            )

    def on_click(self, event: events.Click) -> None:
        if event.button != 1 or event.widget is None:
            return

        ancestors: list[Widget] = []
        target: Widget | None = event.widget
        while target is not None and target is not self:
            ancestors.append(target)
            parent = target.parent
            target = parent if isinstance(parent, Widget) else None

        comment = next(
            (widget for widget in ancestors if isinstance(widget, CommentCard)),
            None,
        )
        if comment is not None and _comments.select_comment_widget(self, comment):
            if self.visual_mode:
                _selection._exit_visual_mode(self)
            self.focus(scroll_visible=False)
            event.stop()
            return

        for widget in ancestors:
            widget_id = widget.id or ""
            if not widget_id.startswith("file-header-"):
                continue
            suffix = widget_id.removeprefix("file-header-")
            if not suffix.isdigit():
                continue
            if self.visual_mode:
                _selection._exit_visual_mode(self)
            self._set_file_header_selection(int(suffix))
            self.focus(scroll_visible=False)
            self._request_toggle_file_fold()
            event.stop()
            return

        clicked = self._clicked_line_target(event, ancestors)
        if clicked is None:
            return
        line_index, pane, column = clicked
        if event.shift:
            if not self.visual_mode or self.visual_type != "line":
                if self.visual_mode:
                    _selection._exit_visual_mode(self)
                _selection._enter_visual_mode(self, "line")
        elif self.visual_mode:
            _selection._exit_visual_mode(self)
        self._comment_cursor_index = 0
        self._move_cursor(
            line=line_index,
            pane=pane,
            column=column,
            update_active_pane=True,
        )
        self.focus(scroll_visible=False)
        event.stop()

    def _clicked_line_target(
        self,
        event: events.Click,
        ancestors: list[Widget],
    ) -> tuple[int, Literal["old", "new"], int] | None:
        pane: Literal["old", "new"] | None = None
        code_widget: Widget | None = None
        line_index: int | None = None

        for widget in ancestors:
            if widget.has_class("-old-side") or widget.has_class("split-pane-left"):
                pane = "old"
            elif widget.has_class("-new-side") or widget.has_class("split-pane-right"):
                pane = "new"
            if code_widget is None and widget.has_class("code-content"):
                code_widget = widget

            widget_id = widget.id or ""
            if not widget_id.startswith("line-"):
                continue
            parts = widget_id.split("-")
            if len(parts) < 2 or not parts[1].isdigit():
                continue
            line_index = int(parts[1])
            if len(parts) > 2 and parts[2] in {"old", "new"}:
                pane = "old" if parts[2] == "old" else "new"
                break

        if line_index is None:
            block = next(
                (
                    widget
                    for widget in ancestors
                    if isinstance(widget, (UnifiedDiffBlock, SplitDiffBlock))
                ),
                None,
            )
            if block is None:
                return None
            row = max(0, event.screen_y - block.content_region.y)
            if isinstance(block, SplitDiffBlock):
                if row >= len(block.line_indices):
                    return None
                line_index = block.line_indices[row]
                if pane is None:
                    pane = (
                        "new" if event.screen_x >= block._right_pane.region.x else "old"
                    )
            else:
                for candidate in block.line_indices:
                    start, end = block._row_ranges_by_line[candidate]
                    if start <= row < end:
                        line_index = candidate
                        if end - start == 2:
                            pane = "old" if row == start else "new"
                        break

        if line_index is None or not 0 <= line_index < len(self._all_lines):
            return None
        line = self._all_lines[line_index]
        if pane is None:
            if line.is_deleted:
                pane = "old"
            elif line.is_added:
                pane = "new"
            else:
                pane = self.cursor_pane

        column = (
            max(0, event.screen_x - code_widget.content_region.x)
            if code_widget is not None
            else 0
        )
        return line_index, pane, column

    _COUNT_MOTION_KEYS = frozenset(
        {
            "h",
            "j",
            "k",
            "l",
            "left",
            "right",
            "up",
            "down",
            "w",
            "b",
            "$",
            "G",
            "{",
            "}",
        }
    )

    def on_key(self, event: events.Key) -> None:
        if not self.has_focus:
            if event.key == "escape" and self._search_input_has_focus():
                self._close_search(clear_query=True)
                event.stop()
                event.prevent_default()
            return

        if event.character and event.character in "123456789":
            self._cursor_ui.pending_count += event.character
            event.stop()
            event.prevent_default()
            return
        if event.character == "0" and self._cursor_ui.pending_count:
            self._cursor_ui.pending_count += "0"
            event.stop()
            event.prevent_default()
            return

        if (
            event.key not in self._COUNT_MOTION_KEYS
            and event.character not in self._COUNT_MOTION_KEYS
        ):
            self._cursor_ui.pending_count = ""

        if event.key == "enter":
            if _comments.try_toggle_current(self):
                event.stop()
                event.prevent_default()
                return
            if self._can_toggle_current_file_fold():
                self._request_toggle_file_fold()
                event.stop()
                event.prevent_default()
                return

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if not self.has_focus:
            return False
        return super().check_action(action, parameters)

    def _search_input_has_focus(self) -> bool:
        inp = self._search_input_widget
        return inp is not None and self.screen.focused is inp

    def _search_cursor(self) -> SearchCursor:
        return SearchCursor(
            row=self._current_row_index(),
            line=self.cursor_line,
            side=self._current_cursor_side(),
            column=self.cursor_column,
        )

    def _close_search(self, *, clear_query: bool) -> None:
        bar = self._search_bar_widget
        update = search_close_update(
            has_bar=bar is not None,
            bar_displayed=bool(bar.display) if bar is not None else False,
            clear_query=clear_query,
        )
        if update.action == "ignore":
            return
        assert bar is not None
        bar.display = False
        if update.clear_state:
            self._search.clear()
        elif update.refresh_display:
            self._search.repaint()
        if update.focus_view:
            self.focus()

    def action_start_search(self) -> None:
        bar = self._search_bar_widget
        search_input = self._search_input_widget
        update = search_start_update(
            has_bar=bar is not None,
            has_input=search_input is not None,
            query=self._search.query,
        )
        if update.action == "ignore":
            return
        assert bar is not None
        assert search_input is not None
        bar.display = True
        search_input.value = update.input_value
        if update.focus_input:
            search_input.focus()

    def _run_search(self, value: str, *, submitted: bool = False) -> None:
        work = self._search.search(
            value, self._all_lines, self._rows_for_current_mode(), submitted=submitted
        )
        if work is not None:
            self.run_worker(
                work,
                group="diff-search",
                exclusive=True,
                name="diff-search-submit" if submitted else "diff-search-change",
            )

    @on(Input.Changed, "#diff-search-input")
    def _on_search_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._run_search(event.value)

    @on(Input.Submitted, "#diff-search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        update = search_submitted_input_update(
            has_bar=self._search_bar_widget is not None,
            value=event.value,
        )
        if update.close_bar:
            assert self._search_bar_widget is not None
            self._search_bar_widget.display = False
        if update.focus_view:
            self.focus()
        self._run_search(update.submit_query, submitted=True)

    def action_next_search_match(self) -> None:
        self._search.jump(
            1,
            self._all_lines,
            self._rows_for_current_mode() if self._search.query else (),
        )

    def action_prev_search_match(self) -> None:
        self._search.jump(
            -1,
            self._all_lines,
            self._rows_for_current_mode() if self._search.query else (),
        )

    def _display_search_result(self, result: SearchResult) -> None:
        if result.dirty_lines:
            self._invalidate_base_code_content_cache(result.dirty_lines)
            from rit.ui.widgets import diff_blocks as _blocks

            if not _blocks._refresh_grouped_blocks_for_lines(self, result.dirty_lines):
                for line_index in result.dirty_lines:
                    self._update_line_cursor(line_index)
        if result.flash_message is not None:
            self.post_message(
                Flash(
                    result.flash_message,
                    style=result.flash_style,
                    duration=result.flash_duration,
                )
            )
        if result.reveal is not None:
            self._reveal_search_match(result.reveal)
        if result.activation is not None:
            self._activate_search_match(result.activation)

    def _reveal_search_match(self, match: DiffSearchMatch) -> None:
        rows = self._rows_for_current_mode()
        target_row = rows[match.row_index] if 0 <= match.row_index < len(rows) else None
        if target_row is None:
            return
        target_widget = _cursor._target_widget_for_row(self, target_row)
        update = search_reveal_update(
            target_exists=True,
            has_target_widget=target_widget is not None,
            target_visible=False
            if target_widget is not None
            else self._row_is_visible(target_row),
        )
        if update.action == "scroll_widget":
            assert target_widget is not None
            self.scroll_to_widget(target_widget, animate=False, top=True)
        elif update.action == "scroll_row":
            _cursor._scroll_row_to_viewport_offset(
                self, target_row, update.viewport_offset
            )

    def _activate_search_match(self, activation: SearchActivationUpdate) -> None:
        match = activation.match
        self._invalidate_base_code_content_cache(activation.dirty_lines)
        rows = self._rows_for_current_mode()
        target_row = rows[match.row_index] if 0 <= match.row_index < len(rows) else None
        current_row = self._current_row()
        placement = search_activation_placement_update(
            has_target_row=target_row is not None,
            target_row_visible=self._row_is_visible(target_row)
            if target_row is not None
            else False,
            has_current_row=current_row is not None,
            row_distance=abs(target_row.row_index - current_row.row_index)
            if target_row is not None and current_row is not None
            else 0,
            half_page_step=self._half_page_step(),
        )
        if placement.action == "jump_anchor" and target_row is not None:
            self._jump_to_row_with_anchor(
                target_row,
                pane=activation.pane,
                column=match.column,
                viewport_offset=placement.viewport_offset,
                reveal_horizontal=placement.reveal_horizontal,
                update_active_pane=activation.update_active_pane,
            )
            return
        self._move_cursor(
            line=match.line_index,
            pane=activation.pane,
            column=match.column,
            scroll_in_visual=self.visual_mode,
            update_active_pane=activation.update_active_pane,
        )
        self._scroll_to_cursor_horizontal()

    def action_next_comment(self) -> None:
        _comments.next_comment(self)

    def action_prev_comment(self) -> None:
        _comments.prev_comment(self)

    def action_toggle_resolve(self) -> None:
        self.run_worker(
            _comments.toggle_resolve(self),
            exclusive=False,
            name="diff-toggle-resolve",
        )

    def _current_line(self) -> DiffLine | None:
        if not self._all_lines or not (0 <= self.cursor_line < len(self._all_lines)):
            return None
        return self._all_lines[self.cursor_line]

    def _resolve_active_pane_for_line(
        self,
        line: DiffLine,
        pane: Literal["old", "new"] | None = None,
    ) -> Literal["old", "new"]:
        return _cursor_side.resolve_active_pane_for_line(
            line,
            self.active_pane if pane is None else pane,
        )

    def _focus_entry_pane(
        self,
        preferred_pane: Literal["old", "new"],
    ) -> Literal["old", "new"]:
        line = self._current_line()
        if line is None:
            return preferred_pane

        hunk_index = self._get_hunk_index_for_line(line.line_index)
        if self._diff is not None and hunk_index is not None:
            file_status = self._diff.hunks[hunk_index].file_status
            if file_status == "added":
                return "new"
            if file_status == "removed":
                return "old"

        return self._resolve_active_pane_for_line(line, preferred_pane)

    def _cursor_side_for_line(
        self,
        line: DiffLine,
        pane: Literal["old", "new"] | None = None,
    ) -> Literal["old", "new", "auto"]:
        split = self.split
        hunk_index = self._get_hunk_index_for_line(line.line_index)
        if self._diff is not None and hunk_index is not None:
            split = split and not _layout.should_force_unified_for_hunk(
                self._diff.hunks[hunk_index]
            )
        return _cursor_side.cursor_side_for_line(
            line,
            split=split,
            cursor_pane=self.cursor_pane if pane is None else pane,
        )

    def _current_cursor_side(self) -> Literal["old", "new", "auto"]:
        line = self._current_line()
        if line is None:
            return "auto"
        return self._cursor_side_for_line(line)

    def _diff_line_cursor_active(self, line_index: int) -> bool:
        """Return True when the diff-line cursor block should be shown."""
        return (
            line_index == self.cursor_line
            and self._comment_cursor_index == 0
            and self._selected_file_header_hunk is None
        )

    def _line_number_cursor_active(self, line_index: int) -> bool:
        """Return True when the line number belongs to the current cursor row."""
        return (
            line_index == self.cursor_line and self._selected_file_header_hunk is None
        )

    def inline_comment_target(
        self,
    ) -> tuple[str, int, Literal["LEFT", "RIGHT"]] | None:
        return self._inline_comment_editor_target

    def inline_comment_draft_index(self) -> int | None:
        return self._inline_comment_editor_draft_index

    def inline_comment_edit_target(self) -> PRComment | None:
        return self._inline_comment_editor_edit_target

    def inline_comment_start_line(self) -> int | None:
        return self._inline_comment_editor_start_line

    def inline_comment_start_side(self) -> Literal["LEFT", "RIGHT"] | None:
        return self._inline_comment_editor_start_side

    def active_pending_draft_index(self) -> int | None:
        hunk_index = self._selected_file_header_hunk
        draft = (
            _comments.active_file_pending_draft(self, hunk_index)
            if hunk_index is not None
            else _comments.active_pending_draft(self, self.cursor_line)
        )
        return self._pending_draft_index(draft)

    def active_review_comment(self) -> PRComment | None:
        """Return the individually selected submitted review comment."""
        hunk_index = self._selected_file_header_hunk
        if hunk_index is not None:
            return _comments.active_file_review_comment(self, hunk_index)
        return _comments.active_review_comment(self, self.cursor_line)

    def _pending_draft_index(
        self,
        draft: PendingReviewComment | None,
    ) -> int | None:
        if self.store is None:
            return None
        return self.store.review_annotations().index_for_comment(draft)

    @property
    def current_diff(self) -> FileDiff | None:
        return self._source_diff or self._diff

    def _render_diff_for_source(self, diff: FileDiff) -> FileDiff:
        if self._showing_full_file:
            self._folded_file_paths = frozenset()
            return diff

        render_diff, folded_files = _folding.build_viewed_file_fold_diff(
            diff,
            is_collapsed=self._should_collapse_file,
        )
        self._folded_file_paths = folded_files
        return render_diff

    def _should_collapse_file(self, filename: str) -> bool:
        if not filename:
            return False
        if filename in self._manually_folded_files:
            return True
        if filename in self._expanded_viewed_files:
            return False
        return self._file_viewed_state(filename) == FileViewedState.VIEWED

    def _file_viewed_state(self, filename: str) -> FileViewedState:
        file = self._file_for_path(filename)
        if file is None:
            return FileViewedState.UNVIEWED
        return file.viewer_viewed_state

    def _file_for_path(self, filename: str) -> PRFile | None:
        if self._file is not None and self._file.filename == filename:
            return self._file
        if self.store is None:
            return None

        state = self.store.state
        files_by_filename = getattr(state, "files_by_filename", None)
        if files_by_filename is not None:
            file = files_by_filename.get(filename)
            if file is not None:
                return file
        files = getattr(state, "files", ())
        return next((file for file in files if file.filename == filename), None)

    def _is_file_folded(self, filename: str) -> bool:
        return filename in self._folded_file_paths

    def expand_file(self, filename: str) -> None:
        """Reveal an explicitly opened file without changing its viewed state."""
        if not self._should_collapse_file(filename):
            return
        self._manually_folded_files.discard(filename)
        if self._file_viewed_state(filename) == FileViewedState.VIEWED:
            self._expanded_viewed_files.add(filename)
        if self.selected_file_header_path() == filename:
            self._set_file_header_selection(None)
        self.refresh_viewed_folds()

    def collapse_viewed_file(self, filename: str) -> None:
        """Clear a manual expansion so a viewed file folds on refresh."""
        self._expanded_viewed_files.discard(filename)

    def _file_path_for_hunk(self, hunk_index: int) -> str | None:
        if self._diff is None or not 0 <= hunk_index < len(self._diff.hunks):
            return None

        active_path = self._diff.filename
        for index, hunk in enumerate(self._diff.hunks):
            if hunk.starts_file and hunk.file_path:
                active_path = hunk.file_path
            if index == hunk_index:
                return hunk.file_path or active_path
        return None

    def _file_header_hunk_index(self, path: str) -> int | None:
        if self._diff is None:
            return None
        active_path = self._diff.filename
        for index, hunk in enumerate(self._diff.hunks):
            if hunk.starts_file and hunk.file_path:
                active_path = hunk.file_path
            if hunk.starts_file and active_path == path:
                return index
        return None

    def selected_file_header_path(self) -> str | None:
        """Return the path targeted by the selected file header."""
        hunk_index = self._selected_file_header_hunk
        if hunk_index is None:
            return None
        return self._file_path_for_hunk(hunk_index)

    def _set_file_header_selection(self, hunk_index: int | None) -> bool:
        if hunk_index is not None:
            if self._diff is None or not 0 <= hunk_index < len(self._diff.hunks):
                return False
            if not self._diff.hunks[hunk_index].starts_file:
                return False

        previous = self._selected_file_header_hunk
        if previous == hunk_index:
            return False

        self._selected_file_header_hunk = hunk_index
        if hunk_index is not None:
            self.current_hunk_index = hunk_index
            self._record_file_navigation(self._file_path_for_hunk(hunk_index))
        self._comment_cursor_index = 0
        _comments.update_cursor_highlight(self, self.cursor_line, self.cursor_line)
        for index in (previous, hunk_index):
            if index is not None:
                _comments.update_file_comment_cursor_highlight(self, index)

        if self.is_mounted:
            for index in (previous, hunk_index):
                if index is None:
                    continue
                for widget in self.query(f"#file-header-{index}"):
                    if index == hunk_index:
                        widget.add_class("-selected")
                    else:
                        widget.remove_class("-selected")
            self._queue_cursor_ui_flush(cursor_lines={self.cursor_line})
        return True

    def _record_file_navigation(self, filename: str | None) -> None:
        if filename is None:
            return
        self._file_navigation_revision += 1
        self._last_navigated_file = filename

    def _current_fold_target(self) -> str | None:
        selected_path = self.selected_file_header_path()
        if selected_path is not None:
            return selected_path
        line = self._current_line()
        if line is not None and line.file_path:
            return line.file_path
        if self.current_file:
            return self.current_file
        return None

    def _can_toggle_current_file_fold(self) -> bool:
        return self._current_fold_target() is not None and self._source_diff is not None

    def _first_line_index_for_file(self, filename: str) -> int | None:
        for line in self._all_lines:
            if line.file_path == filename:
                return line.line_index
        if filename == self.current_file and self._all_lines:
            return 0
        return None

    def file_start_line_index(self, filename: str) -> int | None:
        """Return the first visible line for a file in the rendered diff."""
        return self._first_line_index_for_file(filename)

    def file_for_line_index(self, line_index: int) -> str | None:
        """Return the file represented by a visible rendered line."""
        if 0 <= line_index < len(self._all_lines):
            return self._all_lines[line_index].file_path or self.current_file
        return None

    async def _await_content_mounts(self) -> None:
        content = self._content_widget
        if content is None:
            return
        pending = [child for child in content.children if not child.is_mounted]
        if pending:
            await AwaitMount(content, pending)

    def _restore_file_fold_target(
        self,
        filename: str,
        *,
        preserve_header_position: bool,
        viewport_offset: int | None,
    ) -> None:
        target_line = self._first_line_index_for_file(filename)
        if target_line is not None:
            if preserve_header_position:
                suppress_scroll = self._cursor_ui.suppress_scroll
                self._cursor_ui.suppress_scroll = True
                try:
                    self._move_cursor(line=target_line, pane="new")
                finally:
                    self._cursor_ui.suppress_scroll = suppress_scroll
            else:
                self.jump_to_line_index(
                    target_line,
                    side="RIGHT",
                    focus=self.has_focus,
                    viewport_offset=max(1, viewport_offset)
                    if viewport_offset is not None
                    else 2,
                )

        if not preserve_header_position and filename not in self._folded_file_paths:
            return
        hunk_index = self._file_header_hunk_index(filename)
        if hunk_index is None:
            return
        self._set_file_header_selection(hunk_index)
        if not preserve_header_position and viewport_offset is None:
            _cursor._scroll_to_file_header(self, hunk_index)

    def _toggle_file_fold_intent(self) -> bool:
        filename = self._current_fold_target()
        if filename is None or self.current_diff is None:
            return False
        if self._should_collapse_file(filename):
            self._manually_folded_files.discard(filename)
            if self._file_viewed_state(filename) == FileViewedState.VIEWED:
                self._expanded_viewed_files.add(filename)
        else:
            self._manually_folded_files.add(filename)
            self._expanded_viewed_files.discard(filename)
        return True

    def _request_toggle_file_fold(self) -> None:
        if self._toggle_file_fold_intent():
            self._queue_fold_refresh("diff-toggle-file-fold")

    async def toggle_current_file_fold(self) -> bool:
        """Toggle desired state and reconcile the latest visible projection."""
        if not self._toggle_file_fold_intent():
            return False
        source, filename = self.current_diff, self.current_file
        if source is None or filename is None:
            return False
        await self._refresh_viewed_folds(source, filename)
        return True

    def _fold_projection(
        self, source: FileDiff, *, full_file: bool
    ) -> tuple[FileDiff, frozenset[str]]:
        if full_file:
            return source, frozenset()
        return _folding.build_viewed_file_fold_diff(
            source, is_collapsed=self._should_collapse_file
        )

    def refresh_viewed_folds(self) -> None:
        """Reconcile folded bodies with the latest optimistic viewed state."""
        self._queue_fold_refresh("diff-viewed-fold-refresh")

    def _queue_fold_refresh(self, name: str) -> None:
        source, filename = self.current_diff, self.current_file
        if (
            source is None
            or filename is None
            or not self.is_mounted
            or self._fold_worker_active
        ):
            return
        self._fold_worker_active = True
        self.run_worker(
            self._drain_fold_refresh(source, filename),
            group="diff-fold",
            exclusive=False,
            name=name,
        )

    async def _drain_fold_refresh(self, source: FileDiff, filename: str) -> None:
        try:
            await self._refresh_viewed_folds(source, filename)
        finally:
            self._fold_worker_active = False

    async def _refresh_viewed_folds(self, source: FileDiff, current_file: str) -> None:
        async with self._fold_refresh_lock:
            while self.current_diff is source and self.current_file == current_file:
                if self._requested_source is not source:
                    return
                _, desired = self._fold_projection(
                    source, full_file=self._showing_full_file
                )
                if desired == self._folded_file_paths:
                    return
                request_token = self._render_request_token + 1
                await self.show_diff(
                    current_file,
                    source,
                    preserve_full_file_state=True,
                    _fold_refresh=True,
                )
                if self._render_request_token != request_token:
                    return

    def line_index_for_location(
        self,
        filename: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> int | None:
        diff = self._diff
        if diff is None:
            return None

        return _location.line_index_for_location(
            diff,
            filename,
            line,
            side,
            old_line_index=self._line_index_by_old_number,
            new_line_index=self._line_index_by_new_number,
            old_file_line_index=self._line_index_by_file_old_number,
            new_file_line_index=self._line_index_by_file_new_number,
        )

    def jump_to_line_index(
        self,
        line_index: int,
        *,
        side: Literal["LEFT", "RIGHT"],
        focus: bool = False,
        viewport_offset: int = 2,
        preserve_scroll_if_near_center: bool = False,
    ) -> None:
        self._set_file_header_selection(None)
        target_pane: Literal["old", "new"] = "old" if side == "LEFT" else "new"
        target_row = self._row_for_line_and_pane(line_index, target_pane)
        if target_row is not None:
            anchor_offset = (
                None
                if preserve_scroll_if_near_center
                and _cursor._row_is_near_viewport_center(self, target_row)
                else viewport_offset
            )
            self._jump_to_row_with_anchor(
                target_row,
                pane=target_pane,
                viewport_offset=anchor_offset,
            )
        else:
            self.cursor_line = line_index
            if 0 <= line_index < len(self._line_top_offsets):
                self.scroll_to(
                    y=max(0, self._line_top_offsets[line_index] - viewport_offset),
                    animate=False,
                )

        if focus:
            self.focus()

    def has_inline_comment_editor_for_line(self, line_index: int) -> bool:
        return (
            self._inline_comment_editor_target is not None
            and self._inline_comment_editor_line_index == line_index
        )

    def _inline_comment_target_for_current_line(
        self,
    ) -> tuple[str, int, Literal["LEFT", "RIGHT"]] | None:
        line = self._current_line()
        if not self.current_file or line is None:
            return None
        filename = line.file_path or self.current_file

        side = self._current_cursor_side()
        if side == "old":
            if line.old_line_no is None:
                return None
            return filename, line.old_line_no, "LEFT"

        if line.new_line_no is not None:
            return filename, line.new_line_no, "RIGHT"
        if line.old_line_no is not None:
            return filename, line.old_line_no, "LEFT"
        return None

    def _inline_comment_target_for_visual_selection(
        self,
    ) -> (
        tuple[str, int, Literal["LEFT", "RIGHT"], int, Literal["LEFT", "RIGHT"], int]
        | None
    ):
        if not self.visual_mode or self.visual_anchor_line is None:
            return None
        if self.visual_anchor_line == self.cursor_line:
            return None
        if not self.current_file:
            return None

        current_line = self._current_line()
        if current_line is None:
            return None

        target_path = current_line.file_path or self.current_file
        target_side: Literal["LEFT", "RIGHT"] = (
            "LEFT" if self._current_cursor_side() == "old" else "RIGHT"
        )
        selected_start = min(self.visual_anchor_line, self.cursor_line)
        selected_end = max(self.visual_anchor_line, self.cursor_line)
        selected_numbers: list[tuple[int, int]] = []
        for line_index in range(selected_start, selected_end + 1):
            if not (0 <= line_index < len(self._all_lines)):
                continue
            line = self._all_lines[line_index]
            line_path = line.file_path or self.current_file
            if line_path != target_path:
                return None
            line_number = (
                line.old_line_no if target_side == "LEFT" else line.new_line_no
            )
            if line_number is not None:
                selected_numbers.append((line_number, line.line_index))

        if len(selected_numbers) < 2:
            return None

        start_line, _ = min(selected_numbers, key=lambda item: item[0])
        end_line, end_line_index = max(selected_numbers, key=lambda item: item[0])
        if start_line == end_line:
            return None
        return (
            target_path,
            end_line,
            target_side,
            start_line,
            target_side,
            end_line_index,
        )

    def _inline_comment_editor_height(self) -> int:
        return (
            self._inline_comment_editor_layout_height
            or self.INLINE_COMMENT_EDITOR_HEIGHT
        )

    def _file_comment_editor_height(self) -> int:
        if self._file_comment_editor_hunk_index is None:
            return 0
        return (
            self._file_comment_editor_layout_height or self.FILE_COMMENT_EDITOR_HEIGHT
        )

    @on(InlineCommentEditor.LayoutHeightChanged)
    def _on_comment_editor_layout_height_changed(
        self,
        event: InlineCommentEditor.LayoutHeightChanged,
    ) -> None:
        event.stop()
        if event.editor is self._inline_comment_editor_widget:
            if self._inline_comment_editor_layout_height == event.height:
                return
            self._inline_comment_editor_layout_height = event.height
        elif event.editor is self._file_comment_editor_widget:
            if self._file_comment_editor_layout_height == event.height:
                return
            self._file_comment_editor_layout_height = event.height
        else:
            return
        _virtual._rebuild_virtual_layout(self)

    def file_comment_target(self) -> str | None:
        return self._file_comment_editor_target

    def _mount_file_comment_editor(
        self,
        container: VerticalScroll,
        hunk_index: int,
        *,
        before: Widget | None = None,
    ) -> None:
        if self._file_comment_editor_hunk_index != hunk_index:
            return
        target = self._file_comment_editor_target
        if target is None:
            return

        widget = InlineCommentEditor(
            kind="file",
            title="Add file comment",
            placeholder="Write a comment for the entire file...",
            context=f"Entire file: {target}",
            id="diff-file-comment-editor",
        )
        if before is None:
            container.mount(widget)
        else:
            container.mount(widget, before=before)
        self._file_comment_editor_widget = widget
        self._file_comment_editor_mounted_hunk_index = hunk_index

    def _focus_file_comment_editor(self) -> None:
        if self._file_comment_editor_widget is not None:
            self._file_comment_editor_widget.open()

    async def open_file_comment_editor(self) -> bool:
        hunk_index = self._selected_file_header_hunk
        target = self.selected_file_header_path()
        if hunk_index is None or target is None:
            return False

        self._file_comment_editor_hunk_index = hunk_index
        self._file_comment_editor_target = target
        _virtual._rebuild_virtual_layout(self)
        if (
            self._file_comment_editor_widget is not None
            and self._file_comment_editor_widget.is_mounted
            and self._file_comment_editor_mounted_hunk_index == hunk_index
        ):
            self._file_comment_editor_widget.open()
        else:
            self._file_editor_state = None
            self._file_comment_editor_widget = None
            await self._render_diff()
            self.call_after_refresh(self._focus_file_comment_editor)
        return True

    async def close_file_comment_editor(self) -> None:
        if (
            self._file_comment_editor_hunk_index is None
            and self._file_comment_editor_target is None
        ):
            return

        if self._file_comment_editor_widget is not None:
            self._file_comment_editor_widget.close()
        self.focus()
        self._file_comment_editor_hunk_index = None
        self._file_comment_editor_target = None
        _virtual._rebuild_virtual_layout(self)

    def _mount_inline_comment_editor(
        self,
        container: VerticalScroll,
        line_index: int,
        *,
        before: Widget | None = None,
    ) -> None:
        if not self.has_inline_comment_editor_for_line(line_index):
            return

        edit_target = self._inline_comment_editor_edit_target
        widget = InlineCommentEditor(
            kind="inline",
            title=(
                "Edit inline comment"
                if edit_target is not None
                else "Add inline comment"
            ),
            placeholder="Write a comment for the selected line...",
            initial_text=self._inline_comment_editor_initial_body,
            context=self._inline_comment_editor_context,
            update_existing=edit_target is not None,
            id="diff-inline-comment-editor",
        )
        _, _, target_side = self._inline_comment_editor_target or ("", 0, "RIGHT")
        layout_widget = _comments.mount_side_aware_widget(
            self,
            container,
            widget,
            side="old" if target_side == "LEFT" else "new",
            line_index=line_index,
            before=before,
        )
        self._inline_comment_editor_widget = widget
        self._inline_comment_editor_layout_widget = layout_widget

    def _focus_inline_comment_editor(self) -> None:
        if self._inline_comment_editor_widget is not None:
            self._inline_comment_editor_widget.open(
                self._inline_comment_editor_initial_body
            )

    @staticmethod
    def _inline_comment_side_label(side: Literal["LEFT", "RIGHT"]) -> str:
        return "old" if side == "LEFT" else "new"

    @classmethod
    def _inline_comment_context_label(
        cls,
        target: tuple[str, int, Literal["LEFT", "RIGHT"]],
        *,
        start_line: int | None,
        start_side: Literal["LEFT", "RIGHT"] | None,
    ) -> str:
        path, end_line, side = target
        side_label = cls._inline_comment_side_label(side)
        if start_line is None:
            return f"Selected: {path}:{end_line} ({side_label})"

        effective_start_side = start_side or side
        start_side_label = cls._inline_comment_side_label(effective_start_side)
        if effective_start_side == side:
            first_line = min(start_line, end_line)
            last_line = max(start_line, end_line)
            if first_line == last_line:
                return f"Selected: {path}:{first_line} ({side_label})"
            return f"Selected: {path}:{first_line}-{last_line} ({side_label})"

        return (
            f"Selected: {path}:{start_line} ({start_side_label}) "
            f"-> {end_line} ({side_label})"
        )

    @staticmethod
    def _review_comment_editor_target(
        comment: PRComment,
    ) -> tuple[str, int, Literal["LEFT", "RIGHT"]] | None:
        anchor_line = comment.anchor_line
        if not comment.path or anchor_line is None or comment.anchor_side == "auto":
            return None
        side: Literal["LEFT", "RIGHT"] = (
            "LEFT" if comment.anchor_side == "old" else "RIGHT"
        )
        return (comment.path, anchor_line, side)

    async def open_inline_comment_editor(self) -> bool:
        line = self._current_line()
        target = self._inline_comment_target_for_current_line()
        if line is None or target is None:
            return False

        editor_line_index = line.line_index
        selected_draft = _comments.active_pending_draft(self, line.line_index)
        selected_comment = _comments.active_review_comment(self, line.line_index)
        self._inline_comment_editor_edit_target = None
        if selected_draft is not None:
            target = (selected_draft.path, selected_draft.line, selected_draft.side)
            self._inline_comment_editor_initial_body = selected_draft.body
            self._inline_comment_editor_draft_index = self._pending_draft_index(
                selected_draft
            )
            self._inline_comment_editor_start_line = selected_draft.start_line
            self._inline_comment_editor_start_side = selected_draft.start_side
        elif selected_comment is not None:
            comment_target = self._review_comment_editor_target(selected_comment)
            if comment_target is None:
                return False
            target = comment_target
            self._inline_comment_editor_initial_body = selected_comment.body
            self._inline_comment_editor_draft_index = None
            self._inline_comment_editor_edit_target = selected_comment
            if target[2] == "LEFT":
                start_line = (
                    selected_comment.original_start_line or selected_comment.start_line
                )
            else:
                start_line = (
                    selected_comment.start_line or selected_comment.original_start_line
                )
            self._inline_comment_editor_start_line = start_line
            if start_line is None:
                self._inline_comment_editor_start_side = None
            elif selected_comment.start_side == "LEFT":
                self._inline_comment_editor_start_side = "LEFT"
            elif selected_comment.start_side == "RIGHT":
                self._inline_comment_editor_start_side = "RIGHT"
            else:
                self._inline_comment_editor_start_side = target[2]
        else:
            visual_target = self._inline_comment_target_for_visual_selection()
            if visual_target is not None:
                path, end_line, side, start_line, start_side, editor_line_index = (
                    visual_target
                )
                target = (path, end_line, side)
                self._inline_comment_editor_start_line = start_line
                self._inline_comment_editor_start_side = start_side
            else:
                self._inline_comment_editor_start_line = None
                self._inline_comment_editor_start_side = None
            self._inline_comment_editor_initial_body = ""
            self._inline_comment_editor_draft_index = None

        self._inline_comment_editor_line_index = editor_line_index
        self._inline_comment_editor_target = target
        self._inline_comment_editor_context = self._inline_comment_context_label(
            target,
            start_line=self._inline_comment_editor_start_line,
            start_side=self._inline_comment_editor_start_side,
        )
        self._inline_editor_state = None
        self._inline_comment_editor_widget = None
        self._inline_comment_editor_layout_widget = None
        _virtual._rebuild_virtual_layout(self)
        await self._render_diff()
        self.call_after_refresh(self._focus_inline_comment_editor)
        return True

    async def close_inline_comment_editor(self) -> None:
        if (
            self._inline_comment_editor_line_index is None
            and self._inline_comment_editor_target is None
        ):
            return

        if self.is_mounted:
            self.screen.set_focus(self, scroll_visible=False)
        self._inline_comment_editor_line_index = None
        self._inline_comment_editor_target = None
        self._inline_comment_editor_widget = None
        self._inline_comment_editor_layout_widget = None
        self._inline_comment_editor_initial_body = ""
        self._inline_comment_editor_context = ""
        self._inline_comment_editor_draft_index = None
        self._inline_comment_editor_edit_target = None
        self._inline_comment_editor_start_line = None
        self._inline_comment_editor_start_side = None
        _virtual._rebuild_virtual_layout(self)
        await self._render_diff()
        self.call_after_refresh(self.focus)

    def _get_cursor_text_for_target(
        self,
        line_index: int,
        pane: Literal["old", "new"],
    ) -> str:
        return _cursor._get_cursor_text_for_target(self, line_index, pane)

    def _current_row_index(self) -> int:
        line = self._current_line()
        if line is None:
            return 0
        _render._ensure_rendered_rows_for_mode(self, split=self.split)
        if self.split:
            return self._row_lookup_split.get(line.line_index, 0)
        side = self._cursor_side_for_line(line)
        return self._row_lookup_unified.get((line.line_index, side), 0)

    def _current_row(self) -> RenderedRow | None:
        rows = self._rows_for_current_mode()
        if not rows:
            return None
        row_index = self._current_row_index()
        if not (0 <= row_index < len(rows)):
            return None
        return rows[row_index]

    def _rows_for_current_mode(self) -> list[RenderedRow]:
        _render._ensure_rendered_rows_for_mode(self, split=self.split)
        return self._rows_split if self.split else self._rows_unified

    def _get_cursor_text(self) -> str:
        line = self._current_line()
        if line is None:
            return ""
        return self._get_line_text(line, self._cursor_side_for_line(line))

    def _get_line_text(
        self,
        line: DiffLine,
        side: Literal["old", "new", "auto"] = "auto",
    ) -> str:
        if side == "old":
            return line.old_content
        if side == "new":
            return line.new_content
        if line.has_new_side:
            return line.new_content
        if line.has_old_side:
            return line.old_content
        return ""

    def _get_line_side_for_widget(
        self,
        line: DiffLine,
        widget: Static,
    ) -> Literal["old", "new", "auto"]:
        if widget.has_class("-old-side"):
            return "old"
        if widget.has_class("-new-side"):
            return "new"
        if line.is_modified:
            if widget.has_class("-removed"):
                return "old"
            if widget.has_class("-added"):
                return "new"
        if line.is_deleted:
            return "old"
        if line.is_added:
            return "new"
        return "auto"

    def _update_placeholder_cursor(
        self, widget: Static, line: DiffLine, has_cursor: bool
    ) -> None:
        side = self._get_line_side_for_widget(line, widget)
        if side == "auto":
            return
        widget.update(
            _render._split_placeholder_content(self, side=side, has_cursor=has_cursor)
        )
        widget.set_class(has_cursor, "-cursor")

    def _widget_matches_cursor_side(self, line: DiffLine, widget: Static) -> bool:
        cursor_side = self._cursor_side_for_line(line)
        widget_side = self._get_line_side_for_widget(line, widget)
        if cursor_side == "auto":
            return True
        return widget_side == cursor_side or widget_side == "auto"

    def _get_hunk_index_for_line(self, line_index: int) -> int | None:
        if 0 <= line_index < len(self._hunk_index_by_line):
            return self._hunk_index_by_line[line_index]
        return None

    def _half_page_step(self) -> int:
        return _cursor._half_page_step(self)

    def _row_vertical_bounds(self, row: RenderedRow) -> tuple[int, int] | None:
        return _cursor._row_vertical_bounds(self, row)

    def _get_line_container(self, line_idx: int):
        if not self._is_line_rendered(line_idx):
            return None
        return self._line_widgets_by_index.get(line_idx)

    def _get_file_header_widget(self, hunk_index: int):
        return self._file_header_widgets.get(hunk_index)

    def _get_hunk_header_widget(self, hunk_index: int):
        return self._hunk_header_widgets.get(hunk_index)

    def _register_line_widget(self, line_index: int, widget: Widget) -> None:
        self._line_widgets_by_index[line_index] = widget

    def _register_row_anchor_widget(self, anchor_id: str, widget: Widget) -> None:
        self._row_anchor_widgets[anchor_id] = widget

    def _unregister_line_widgets(self, line_index: int) -> None:
        self._line_widgets_by_index.pop(line_index, None)
        self._unified_blocks_by_line.pop(line_index, None)
        self._split_blocks_by_line.pop(line_index, None)
        self._split_scroll_widgets_by_line.pop(line_index, None)

        if not (0 <= line_index < len(self._all_lines)):
            return

        line = self._all_lines[line_index]
        anchor_ids = [f"line-{line_index}"]
        if not self.split and line.is_modified:
            anchor_ids.extend([f"line-{line_index}-old", f"line-{line_index}-new"])

        for anchor_id in anchor_ids:
            self._row_anchor_widgets.pop(anchor_id, None)

    def _register_file_header_widget(self, hunk_index: int, widget: Widget) -> None:
        self._file_header_widgets[hunk_index] = widget

    def _register_hunk_header_widget(self, hunk_index: int, widget: Widget) -> None:
        self._hunk_header_widgets[hunk_index] = widget

    def _register_code_widgets(self, line_index: int, *widgets: Static) -> None:
        self._code_widgets_by_line[line_index] = tuple(widgets)

    def _get_code_widgets(self, line_index: int) -> tuple[Static, ...]:
        return self._code_widgets_by_line.get(line_index, ())

    def _register_split_scroll_widgets(self, line_index: int, *widgets: Widget) -> None:
        self._split_scroll_widgets_by_line[line_index] = tuple(widgets)

    def _get_split_scroll_widgets(self, line_index: int) -> tuple[Widget, ...]:
        return self._split_scroll_widgets_by_line.get(line_index, ())

    def _sync_split_horizontal_scroll(
        self,
        scroll_x: float,
        source: Widget | None = None,
    ) -> None:
        clamped_scroll_x = max(0.0, scroll_x)
        if source is not None and clamped_scroll_x == self._split_horizontal_scroll_x:
            return
        self._split_horizontal_scroll_x = clamped_scroll_x

        if self._syncing_split_scroll:
            return

        widgets: list[Widget] = []
        seen: set[int] = set()
        for scroll_widgets in self._split_scroll_widgets_by_line.values():
            for widget in scroll_widgets:
                widget_id = id(widget)
                if widget_id in seen:
                    continue
                seen.add(widget_id)
                widgets.append(widget)
        for widget in self._hunk_header_widgets.values():
            if not widget.has_class("split-hunk-header-scroll"):
                continue
            widget_id = id(widget)
            if widget_id in seen:
                continue
            seen.add(widget_id)
            widgets.append(widget)

        self._syncing_split_scroll = True
        try:
            for widget in widgets:
                if widget is source:
                    continue
                if getattr(widget, "scroll_x", None) != clamped_scroll_x:
                    widget.scroll_x = clamped_scroll_x
        finally:
            self._syncing_split_scroll = False

    def _get_active_split_scroll_widget(self) -> Widget | None:
        if not (0 <= self.cursor_line < len(self._all_lines)):
            return None

        target_side = self._current_cursor_side()
        if target_side == "auto":
            target_side = self.cursor_pane
        for widget in self._get_split_scroll_widgets(self.cursor_line):
            if target_side == "old" and widget.has_class("-old-side"):
                return widget
            if target_side == "new" and widget.has_class("-new-side"):
                return widget
        return None

    @property
    def _render_policy_line_count(self) -> int:
        """Keep render eligibility stable when folding shrinks the projection."""
        return max(self._source_line_count, len(self._all_lines))

    async def _remount_grouped_visible_window(
        self,
        container: VerticalScroll,
        old_start: int,
        old_end: int,
        new_start: int,
        new_end: int,
    ) -> None:
        if old_end >= old_start:
            await _virtual._remove_virtualized_lines(self, old_start, old_end)
        await _virtual._clear_virtual_file_headers(self)
        await _virtual._clear_virtual_hunk_headers(self)
        await _virtual._sync_virtual_buffers(self, container, new_start, new_end)
        _virtual._mount_virtualized_lines_at_bottom(self, container, new_start, new_end)
        await _virtual._sync_visible_virtual_file_headers(
            self, container, new_start, new_end
        )
        await _virtual._sync_visible_virtual_hunk_headers(
            self, container, new_start, new_end
        )

    def _is_current_render_request(self, request_token: int) -> bool:
        return (
            request_token == self._render_request_token
            or request_token == self._committing_render_token
        )

    def _finalize_render_state_if_current(self, request_token: int) -> None:
        if not self._is_current_render_request(request_token):
            return
        _render._finalize_render_state(self)

    async def _run_render_diff_for_request(self, request_token: int) -> None:
        token = _RENDER_REQUEST_CONTEXT.set(request_token)
        try:
            await self._render_diff()
        finally:
            _RENDER_REQUEST_CONTEXT.reset(token)

    async def _build_render_plan(
        self,
        diff: FileDiff,
        *,
        showing_full_file: bool,
        file: PRFile | None,
        request_token: int,
    ) -> tuple[_plan.DiffPlan, _plan.RenderedRowsPlan, bool] | None:
        cache = self._diff_plan_cache
        if cache is None:
            cache = self._diff_plan_cache = DiffPlanCache(diff)
        projection = await _finish_to_thread_on_cancel(cache.prepare, diff)
        plan = projection.plan
        if not self._is_current_render_request(request_token):
            return None

        while True:
            layout_mode = self.mode
            layout_width = self.size.width
            planned_split = layout_mode == "split" or (
                layout_mode == "auto"
                and layout_width >= self.LAYOUT.auto_split_min_width
            )
            if planned_split and _layout.should_force_unified_for_file(
                showing_full_file=showing_full_file,
                file=file,
                diff=diff,
            ):
                planned_split = False
            rendered_rows = await _finish_to_thread_on_cancel(
                projection.build_rows,
                split=planned_split,
            )
            if not self._is_current_render_request(request_token):
                return None
            if layout_mode == self.mode and layout_width == self.size.width:
                return plan, rendered_rows, planned_split

    async def show_diff(
        self,
        filename: str,
        diff: FileDiff,
        *,
        preserve_full_file_state: bool = False,
        _expected_navigation_revision: int | None = None,
        _show_full_file: bool | None = None,
        _fold_refresh: bool = False,
    ) -> None:
        """Prepare outside repaint batches and publish the current projection."""
        if _fold_refresh:
            if self.current_diff is not diff or self._requested_source is not diff:
                return
        else:
            self._requested_source = diff
        self._render_request_token += 1
        request_token = self._render_request_token
        cache: DiffPlanCache | None = None
        try:
            async with self._diff_plan_lock:
                if request_token != self._render_request_token:
                    return
                if (
                    self._diff_plan_cache is None
                    or self._diff_plan_cache.source is not diff
                ):
                    self._diff_plan_cache = DiffPlanCache(diff)
                cache = self._diff_plan_cache
                showing_full_file = (
                    _show_full_file
                    if _show_full_file is not None
                    else self._showing_full_file
                    if preserve_full_file_state
                    else False
                )
                files_by_filename = (
                    getattr(self.store.state, "files_by_filename", {})
                    if self.store
                    else {}
                )
                target_file = files_by_filename.get(filename)
                if self.store and target_file is None:
                    target_file = next(
                        (
                            file
                            for file in self.store.state.files
                            if file.filename == filename
                        ),
                        None,
                    )
                while request_token == self._render_request_token:
                    if (
                        _expected_navigation_revision is not None
                        and self._file_navigation_revision
                        != _expected_navigation_revision
                    ):
                        return
                    render_diff, folded_files = self._fold_projection(
                        diff, full_file=showing_full_file
                    )
                    render_plan = await self._build_render_plan(
                        render_diff,
                        showing_full_file=showing_full_file,
                        file=target_file,
                        request_token=request_token,
                    )
                    if render_plan is None:
                        return
                    commit = asyncio.create_task(
                        self._commit_prepared_diff(
                            filename,
                            diff,
                            render_diff,
                            folded_files,
                            render_plan,
                            request_token=request_token,
                            layout=(self.mode, self.size.width),
                            showing_full_file=showing_full_file,
                            target_file=target_file,
                            preserve_full_file_state=preserve_full_file_state,
                            show_full_file=_show_full_file,
                            fold_refresh=_fold_refresh,
                            navigation_revision=_expected_navigation_revision,
                        )
                    )
                    try:
                        committed = await asyncio.shield(commit)
                    except asyncio.CancelledError:
                        await asyncio.gather(commit, return_exceptions=True)
                        raise
                    if committed:
                        return
        except asyncio.CancelledError:
            if cache is not None and self._diff_plan_cache is cache:
                self._diff_plan_cache = None
            raise
        finally:
            if request_token == self._render_request_token:
                self._requested_source = self.current_diff

    async def _commit_prepared_diff(
        self,
        filename: str,
        source: FileDiff,
        render_diff: FileDiff,
        folded_files: frozenset[str],
        render_plan: tuple[_plan.DiffPlan, _plan.RenderedRowsPlan, bool],
        *,
        request_token: int,
        layout: tuple[str, int],
        showing_full_file: bool,
        target_file: PRFile | None,
        preserve_full_file_state: bool,
        show_full_file: bool | None,
        fold_refresh: bool,
        navigation_revision: int | None,
    ) -> bool:
        async with self.batch() if self.is_mounted else self.lock:
            if request_token != self._render_request_token:
                return False
            if (
                navigation_revision is not None
                and self._file_navigation_revision != navigation_revision
            ):
                return False
            _, desired = self._fold_projection(source, full_file=showing_full_file)
            if layout != (self.mode, self.size.width) or desired != folded_files:
                return False
            cache = self._diff_plan_cache
            if (
                cache is None
                or cache.source is not source
                or not cache.source_structure_matches()
            ):
                return False
            state = _fold_state.FoldState.capture(self) if fold_refresh else None
            allow_retained_prefix = fold_refresh and not cache.has_source_changes
            if cache.has_source_changes:
                self._hl_state.request_token += 1
                self._hl_state.cache.clear()
                cache.has_source_changes = False
            self._committing_render_token = request_token
            self._suspend_split_state_rerender = True
            self._suspend_scroll_virtual_window_watch = True
            try:
                await self._apply_render_plan(
                    filename,
                    source,
                    render_diff,
                    folded_files,
                    render_plan,
                    request_token=request_token,
                    target_file=target_file,
                    preserve_full_file_state=preserve_full_file_state,
                    _show_full_file=show_full_file,
                    fold_state=state,
                    allow_retained_prefix=allow_retained_prefix,
                )
            finally:
                self._committing_render_token = None
                self._suspend_split_state_rerender = False
                self._suspend_scroll_virtual_window_watch = False
            return layout == (self.mode, self.size.width)

    async def _apply_render_plan(
        self,
        filename: str,
        diff: FileDiff,
        render_diff: FileDiff,
        folded_file_paths: frozenset[str],
        render_plan: tuple[_plan.DiffPlan, _plan.RenderedRowsPlan, bool],
        *,
        request_token: int,
        target_file: PRFile | None,
        preserve_full_file_state: bool,
        _show_full_file: bool | None,
        fold_state: _fold_state.FoldState | None,
        allow_retained_prefix: bool,
    ) -> None:
        with self.app.batch_update() if self.is_mounted else nullcontext():
            is_new_file = filename != self.current_file
            selected_header_path = self.selected_file_header_path()
            plan, rendered_rows, planned_split = render_plan
            retained_prefix = (
                _render._capture_retained_render_prefix(
                    self,
                    source=diff,
                    render_diff=render_diff,
                    folded_file_paths=folded_file_paths,
                    plan=plan,
                    planned_split=planned_split,
                )
                if allow_retained_prefix
                else None
            )
            publish_line_metadata(render_diff)
            if not preserve_full_file_state:
                self._showing_full_file = False
                self._saved_diff = None
                self._saved_filename = None
                self._saved_restore_position = None
            elif _show_full_file is not None:
                self._showing_full_file = _show_full_file
            if is_new_file:
                self._inline_editor_state = None
                self._file_editor_state = None
                self._inline_comment_editor_line_index = None
                self._inline_comment_editor_target = None
                self._inline_comment_editor_widget = None
                self._inline_comment_editor_layout_widget = None
                self._inline_comment_editor_initial_body = ""
                self._inline_comment_editor_context = ""
                self._inline_comment_editor_draft_index = None
                self._inline_comment_editor_edit_target = None
                self._inline_comment_editor_start_line = None
                self._inline_comment_editor_start_side = None
                self._file_comment_editor_hunk_index = None
                self._file_comment_editor_target = None
                self._file_comment_editor_widget = None
                self._file_comment_editor_mounted_hunk_index = None
                self._selected_file_header_hunk = None
                selected_header_path = None

            self.current_file = filename
            self._source_diff = diff
            self._source_line_count = sum(len(hunk.lines) for hunk in diff.hunks)
            self._diff = diff
            self.current_hunk_index = 0

            self._all_lines = []
            self._rows_unified = []
            self._rows_split = []
            self._row_lookup_unified = {}
            self._row_lookup_split = {}
            self._rows_unified_ready = False
            self._rows_split_ready = False
            self._diff_file_paths = frozenset()
            self._file_change_stats = {}
            self._search.clear(repaint=False)
            _comments.clear_state(self)
            self._line_index_by_new_number = {}
            self._line_index_by_old_number = {}
            self._new_line_number_bounds = None
            self._line_index_by_file_new_number = {}
            self._line_index_by_file_old_number = {}
            self._hunk_index_by_line = []
            self._modified_line_count = 0
            self._total_line_render_height = 0
            self._old_line_number_width_value = _layout.MIN_LINE_NUMBER_WIDTH
            self._new_line_number_width_value = _layout.MIN_LINE_NUMBER_WIDTH
            self._hunk_line_ranges = []
            self._hunk_start_line_indices = []
            self._hunk_end_line_indices = []
            self._hunk_header_top_offsets = []
            self._line_top_offsets = []
            self._line_heights = []
            self._line_bottom_offsets = []
            self._virtual_content_height = 0
            self._virt = VirtualState()
            self._hl_state.window_inflight = None
            self._hl_state.queued_window = None
            self._hl_state.queued_full = None
            self._unified_block_static_rows_by_line.clear()
            self._split_block_static_rows_by_line.clear()
            self._base_code_content_cache.clear()
            self._base_code_content_cache_keys_by_line.clear()
            self._code_widgets_by_line = {}
            self._split_scroll_widgets_by_line = {}
            self._split_horizontal_scroll_x = 0.0
            self._unified_code_width = 1
            self._split_old_code_width = 1
            self._split_new_code_width = 1
            self.scroll_x = 0
            if is_new_file:
                self.scroll_y = 0
            self._unified_blocks_by_line = {}
            self._split_blocks_by_line = {}
            self._line_widgets_by_index = {}
            self._row_anchor_widgets = {}
            self._file_header_widgets = {}
            self._hunk_header_widgets = {}
            self._cursor_ui = CursorUIState()
            self._visual_selection_specs = {}

            _selection._exit_visual_mode(self)
            self.visual_type = "char"

            self.active_pane = "new"
            self.cursor_pane = "new"
            self.cursor_line = 0
            self.cursor_column = 0
            self._comment_cursor_index = 0

            self._file = target_file
            self._folded_file_paths = folded_file_paths
            diff = render_diff
            self._diff = diff

            self._all_lines = plan.all_lines
            self._diff_file_paths = plan.file_paths
            self._file_change_stats = plan.file_change_stats
            self._line_index_by_new_number = plan.line_index_by_new_number
            self._line_index_by_old_number = plan.line_index_by_old_number
            self._new_line_number_bounds = plan.new_line_number_bounds
            self._line_index_by_file_new_number = plan.line_index_by_file_new_number
            self._line_index_by_file_old_number = plan.line_index_by_file_old_number
            self._hunk_index_by_line = plan.hunk_index_by_line
            self._modified_line_count = plan.modified_line_count
            self._hunk_line_ranges = plan.hunk_line_ranges
            self._hunk_start_line_indices = plan.hunk_start_line_indices
            self._hunk_end_line_indices = plan.hunk_end_line_indices
            self._selected_file_header_hunk = (
                self._file_header_hunk_index(selected_header_path)
                if selected_header_path is not None
                else None
            )
            initial_line = self._current_line()
            if (
                self._selected_file_header_hunk is None
                and initial_line is not None
                and _folding.is_folded_placeholder_line(initial_line)
            ):
                self._selected_file_header_hunk = self._get_hunk_index_for_line(
                    initial_line.line_index
                )
            if self._file_comment_editor_target is not None:
                self._file_comment_editor_hunk_index = self._file_header_hunk_index(
                    self._file_comment_editor_target
                )
            (
                self._unified_code_width,
                self._split_old_code_width,
                self._split_new_code_width,
            ) = plan.code_widths
            self._old_line_number_width_value = plan.old_line_number_width
            self._new_line_number_width_value = plan.new_line_number_width
            if planned_split:
                self._rows_split = rendered_rows.rows_split
                self._row_lookup_split = rendered_rows.row_lookup_split
                self._rows_split_ready = True
            else:
                self._rows_unified = rendered_rows.rows_unified
                self._row_lookup_unified = rendered_rows.row_lookup_unified
                self._rows_unified_ready = True

            if fold_state is not None:
                fold_state.restore_model(self)
            if self._inline_comment_editor_target is not None:
                editor_line = self.line_index_for_location(
                    *self._inline_comment_editor_target
                )
                self._inline_comment_editor_line_index = (
                    editor_line
                    if editor_line is not None
                    and not self._all_lines[editor_line].is_folded_file_placeholder
                    else None
                )
            _comments.build_comment_map(self)
            _render._update_split_state(self)
            _render._ensure_rendered_rows_for_mode(self, split=self.split)
            _virtual._rebuild_virtual_layout(self)
            if fold_state is not None:
                fold_state.restore_scroll(self)
            _virtual._configure_virtual_window(self)
            if fold_state is not None and self._virt.active:
                viewport_line = fold_state.viewport.resolve(self)
                if viewport_line is not None:
                    _virtual._set_virtual_window_around(
                        self, viewport_line + self.scrollable_content_region.height // 2
                    )

            if _hl._has_highlighted_diff(self, diff):
                _hl._highlight_diff_sync(self, diff)
            elif _hl._use_windowed_highlight_strategy(self, diff):
                _hl._clear_highlighted_content(self, diff)
            else:
                _hl._clear_highlighted_content(self, diff)
                _hl._queue_highlight_diff(self, filename, diff)

            navigation_revision = self._file_navigation_revision
            used_retained_prefix = (
                retained_prefix is not None
                and await _render._render_diff_from_retained_prefix(
                    self,
                    retained_prefix,
                    request_token=request_token,
                )
            )
            if not used_retained_prefix:
                await self._run_render_diff_for_request(request_token)
            if (
                fold_state is not None
                and self._file_navigation_revision == navigation_revision
            ):
                # Mounts finish before layout; resolve anchors before the first paint.
                self._reflow_fold_layout()
                fold_state.restore_scroll(self, mounted=True)
                self._reflow_fold_layout()

    def _reflow_fold_layout(self) -> None:
        """Settle retained container geometry while fold painting is paused."""
        containers = [
            widget for widget in (self._content_widget, self) if widget is not None
        ]
        # Flush deferred scrollbar layout without letting oscillation block input.
        for _ in range(4):
            offset = self.scroll_offset
            for container in containers:
                container._check_refresh()
            self.screen._refresh_layout()
            if self.scroll_offset == offset and not any(
                container._layout_required for container in containers
            ):
                break

    async def prepare(self) -> None:
        if self._diff:
            await asyncio.to_thread(lambda: _render._precompute_diff_data(self))

    def refresh_header(self) -> None:
        """Re-render visible file headers."""
        _render._refresh_file_header_widgets(self)

    def refresh_thread_metadata(self) -> None:
        """Refresh inline thread metadata without rebuilding diff rows."""
        _comments.refresh_thread_metadata(self)

    def _reset_current_diff_highlight_state(self) -> bool:
        diff = self._diff
        filename = self.current_file
        if diff is None or filename is None:
            return False

        self._hl_state.request_token += 1
        self._hl_state.queued_window = None
        self._hl_state.queued_full = None
        self._hl_state.cache.clear()
        _hl._clear_highlighted_content(self, diff)

        if not _hl._use_windowed_highlight_strategy(self, diff):
            _hl._highlight_diff_sync(self, diff)
        return True

    def refresh_syntax_theme(self) -> None:
        if not self.is_mounted:
            return
        if not self._reset_current_diff_highlight_state():
            return

        self.run_worker(
            self._run_render_diff_for_request(self._render_request_token),
            exclusive=True,
            name="diff-theme-rerender",
        )

    @property
    def view_revision(self) -> tuple[int, int]:
        """Return the current render and navigation revision."""
        return self._render_request_token, self._file_navigation_revision

    def _is_current_view_revision(self, revision: tuple[int, int]) -> bool:
        return self.view_revision == revision

    def action_toggle_full_file(self) -> None:
        action = _full_preview.choose_full_file_preview_action(
            current_file=self.current_file,
            selected_file=self._full_file_preview_target(),
            showing_full_file=self._showing_full_file,
            has_store=self.store is not None,
        )
        if action.kind == "ignore":
            return
        if action.kind == "restore":
            self._restore_diff_view()
            return
        if action.kind == "request_file" and action.filename is not None:
            self.post_message(
                self.FullFilePreviewRequested(action.filename, self.view_revision)
            )
            return
        self.run_worker(
            self._load_and_show_full_file(),
            exclusive=True,
            name="diff-full-file",
        )

    def _full_file_preview_target(self) -> str | None:
        selected_file = self.selected_file_header_path()
        if selected_file is not None:
            return selected_file
        return _full_preview.full_file_preview_target(
            self.current_file,
            self._current_line(),
        )

    async def _load_and_show_full_file(self) -> None:
        filename = self.current_file
        if filename is None or self.store is None:
            return
        view_revision = self.view_revision
        content = await self.store.get_file_content(filename)
        if not self._is_current_view_revision(view_revision):
            return
        if content is None:
            self.post_message(
                Flash("Failed to load file content", style="error", duration=2.0)
            )
            return
        await self.show_full_file_preview(
            filename,
            content,
            source_diff=self._source_diff or self._diff,
            expected_view_revision=view_revision,
        )

    async def show_full_file_preview(
        self,
        filename: str,
        content: str,
        *,
        source_diff: FileDiff | None = None,
        restore_filename: str | None = None,
        restore_diff: FileDiff | None = None,
        expected_view_revision: tuple[int, int] | None = None,
    ) -> bool:
        view_revision = expected_view_revision or self.view_revision
        anchor_line_no = self._full_file_preview_anchor_line_no(
            filename,
            source_diff,
        )
        full_diff = await asyncio.to_thread(
            _full_preview.build_full_file_diff,
            filename,
            content,
            source_diff=source_diff,
        )
        if not self._is_current_view_revision(view_revision):
            return False

        saved_filename = restore_filename or self.current_file
        saved_diff = restore_diff or self._source_diff or self._diff
        saved_restore_position = self._full_file_restore_position()
        preview_render_token = self._render_request_token + 1
        await self.show_diff(
            filename,
            full_diff,
            preserve_full_file_state=True,
            _expected_navigation_revision=view_revision[1],
            _show_full_file=True,
        )
        if (
            self._render_request_token != preview_render_token
            or self._diff is not full_diff
        ):
            return False
        self._saved_filename = saved_filename
        self._saved_diff = saved_diff
        self._saved_restore_position = saved_restore_position
        self._jump_to_full_file_preview_anchor(anchor_line_no)
        self.post_message(Flash("Full file preview", style="success", duration=1.5))
        return True

    def _full_file_preview_anchor_line_no(
        self,
        filename: str,
        source_diff: FileDiff | None,
    ) -> int | None:
        return _full_preview.selected_full_file_anchor(
            filename,
            self._current_line(),
            source_diff,
        )

    def _jump_to_full_file_preview_anchor(self, line_no: int | None) -> None:
        line_index = _full_preview.full_file_anchor_line_index(
            line_no,
            self._line_index_by_new_number,
            available_line_bounds=self._new_line_number_bounds,
        )
        if line_index is None:
            return

        row = self._row_for_line_and_pane(line_index, "new")
        if row is not None:
            self._jump_to_row_with_anchor(
                row,
                pane="new",
                viewport_offset=2,
                update_active_pane=True,
            )
            return

        self._move_cursor(line=line_index, pane="new", update_active_pane=True)

    def _full_file_restore_position(
        self,
    ) -> _full_preview.FullFileRestorePosition | None:
        if not self._all_lines:
            return None
        return _full_preview.FullFileRestorePosition(
            line=self.cursor_line,
            column=self.cursor_column,
            cursor_pane=self.cursor_pane,
            active_pane=self.active_pane,
            viewport_offset=self._current_cursor_viewport_offset(),
        )

    def _restore_diff_view(self) -> None:
        if self._saved_diff is None:
            return
        filename = self._saved_filename or self.current_file
        if filename is None:
            return
        diff = self._saved_diff
        restore_position = self._saved_restore_position
        self._saved_diff = None
        self._saved_filename = None
        self._saved_restore_position = None
        self._showing_full_file = False
        self.run_worker(
            self._restore_diff_async(filename, diff, restore_position),
            exclusive=True,
            name="diff-restore",
        )

    async def _restore_diff_async(
        self,
        filename: str,
        diff: FileDiff,
        restore_position: _full_preview.FullFileRestorePosition | None,
    ) -> None:
        await self.show_diff(filename, diff)
        self._restore_full_file_position(restore_position)
        self.post_message(self.FullFilePreviewRestored(filename=filename))
        self.post_message(Flash("Diff view", style="success", duration=1.5))

    def _restore_full_file_position(
        self,
        restore_position: _full_preview.FullFileRestorePosition | None,
    ) -> None:
        if restore_position is None:
            return
        line_index = _full_preview.full_file_restore_line_index(
            restore_position,
            line_count=len(self._all_lines),
        )
        if line_index is None:
            return

        target_row = self._row_for_line_and_pane(
            line_index, restore_position.cursor_pane
        )
        if target_row is not None:
            self._jump_to_row_with_anchor(
                target_row,
                pane=restore_position.cursor_pane,
                column=restore_position.column,
                viewport_offset=restore_position.viewport_offset
                if restore_position.viewport_offset is not None
                else 2,
                update_active_pane=True,
            )
        else:
            self._move_cursor(
                line=line_index,
                column=restore_position.column,
                pane=restore_position.cursor_pane,
                update_active_pane=True,
            )
        self.active_pane = restore_position.active_pane

    def _row_for_line_and_pane(
        self,
        line_index: int,
        pane: Literal["old", "new"],
    ) -> RenderedRow | None:
        rows = self._rows_for_current_mode()
        if not (0 <= line_index < len(self._all_lines)):
            return None

        if self.split:
            row_index = self._row_lookup_split.get(line_index)
            if row_index is None or not (0 <= row_index < len(rows)):
                return None
            return rows[row_index]

        line = self._all_lines[line_index]
        fallback_side: Literal["old", "new", "auto"]
        if line.is_modified or line.is_deleted:
            fallback_side = "old"
        elif line.is_added:
            fallback_side = "new"
        else:
            fallback_side = "auto"

        for side in dict.fromkeys((pane, "auto", fallback_side)):
            row_index = self._row_lookup_unified.get((line_index, side))
            if row_index is not None and 0 <= row_index < len(rows):
                return rows[row_index]
        return None

    _DIFF_MODES: tuple[Literal["auto", "split", "unified"], ...] = (
        "auto",
        "split",
        "unified",
    )

    def action_cycle_diff_mode(self) -> None:
        try:
            idx = self._DIFF_MODES.index(self.mode)
        except ValueError:
            idx = 0
        new_mode = self._DIFF_MODES[(idx + 1) % len(self._DIFF_MODES)]
        self.mode = new_mode
        label = _DIFF_MODE_LABELS[new_mode]
        self.post_message(Flash(f"Diff mode: {label}", style="success", duration=1.5))

    def action_scroll_down(self) -> None:
        _cursor._scroll_down(self)

    def action_scroll_up(self) -> None:
        _cursor._scroll_up(self)

    def action_cursor_left(self) -> None:
        _cursor._cursor_left(self)

    def action_cursor_right(self) -> None:
        _cursor._cursor_right(self)

    def action_start_of_line(self) -> None:
        _cursor._start_of_line(self)

    def action_first_non_blank(self) -> None:
        _cursor._first_non_blank(self)

    def action_end_of_line(self) -> None:
        _cursor._end_of_line(self)

    def action_scroll_home(self) -> None:
        _cursor._scroll_home(self)

    def action_scroll_end(self) -> None:
        _cursor._scroll_end(self)

    async def action_half_page_down(self) -> None:
        await _cursor._half_page_down(self)

    async def action_half_page_up(self) -> None:
        await _cursor._half_page_up(self)

    def action_cycle_active_pane(self) -> None:
        _cursor._cycle_active_pane(self)

    def action_cycle_active_pane_reverse(self) -> None:
        _cursor._cycle_active_pane(self)

    def action_next_word(self) -> None:
        _cursor._next_word(self)

    def action_prev_word(self) -> None:
        _cursor._prev_word(self)

    def action_end_word(self) -> None:
        _cursor._end_word(self)

    def action_next_paragraph(self) -> None:
        _cursor._paragraph(self, 1)

    def action_prev_paragraph(self) -> None:
        _cursor._paragraph(self, -1)

    def action_center_cursor(self) -> None:
        _cursor._center_cursor(self)

    def next_hunk(self) -> None:
        _cursor._next_hunk(self)

    def prev_hunk(self) -> None:
        _cursor._prev_hunk(self)

    def _move_cursor(self, **kwargs) -> bool:
        return _cursor._move_cursor(self, **kwargs)

    def _jump_to_row_with_anchor(self, row: RenderedRow, **kwargs) -> None:
        _cursor._jump_to_row_with_anchor(self, row, **kwargs)

    def _row_is_visible(self, row: RenderedRow) -> bool:
        return _cursor._row_is_visible(self, row)

    def _scroll_to_cursor_horizontal(self) -> None:
        _cursor._scroll_to_cursor_horizontal(self)

    def action_toggle_visual(self) -> None:
        _selection._toggle_visual(self)

    def action_toggle_visual_line(self) -> None:
        _selection._toggle_visual_line(self)

    def action_yank(self) -> None:
        _selection._yank(self)

    def action_copy_file_path(self) -> None:
        filename = self._current_fold_target()
        if filename is None:
            self.post_message(Flash("No file selected", style="warning", duration=2.0))
            return

        self._copy_to_clipboard(filename)
        self.post_message(
            Flash(
                f"Copied file path: {filename}",
                style="success",
                duration=2.0,
            )
        )

    def action_exit_visual(self) -> None:
        _selection._exit_visual(self)

    def _compute_selection_spec_for_line(self, line_idx: int):
        return _selection._compute_selection_spec_for_line(self, line_idx)

    def _update_selection_highlighting(
        self, dirty_lines: set[int] | None = None
    ) -> None:
        _selection._update_selection_highlighting(self, dirty_lines)

    def _build_code_content_with_selection(self, *args, **kwargs) -> Content:
        return _selection._build_code_content_with_selection(self, *args, **kwargs)

    def _update_split_state(self) -> None:
        _render._update_split_state(self)

    def _rebuild_rendered_rows(self) -> None:
        _render._rebuild_rendered_rows(self)

    def _capture_comment_editors(self) -> None:
        if self._inline_comment_editor_target is None:
            self._inline_editor_state = None
        elif (
            self._inline_comment_editor_widget is not None
            and self._inline_comment_editor_widget.is_mounted
        ):
            self._inline_editor_state = _fold_state.EditorState.capture(
                self, self._inline_comment_editor_widget, self._inline_editor_state
            )
        if self._file_comment_editor_target is None:
            self._file_editor_state = None
        elif (
            self._file_comment_editor_widget is not None
            and self._file_comment_editor_widget.is_mounted
        ):
            self._file_editor_state = _fold_state.EditorState.capture(
                self, self._file_comment_editor_widget, self._file_editor_state
            )
        if any(
            state is not None and state.focus_id is not None
            for state in (self._inline_editor_state, self._file_editor_state)
        ):
            # Unmounting a focused editor otherwise scrolls an ancestor to its origin.
            self.screen.set_focus(self, scroll_visible=False)

    def _restore_comment_editors(self) -> None:
        for state, editor in (
            (self._inline_editor_state, self._inline_comment_editor_widget),
            (self._file_editor_state, self._file_comment_editor_widget),
        ):
            if state is None:
                continue
            if editor is not None and editor.is_mounted:
                state.restore(self, editor)
            else:
                state.focus_id = None

    async def _render_diff(self) -> None:
        async with self.batch():
            request_token = _RENDER_REQUEST_CONTEXT.get()
            if request_token is not None and not self._is_current_render_request(
                request_token
            ):
                return
            self._capture_comment_editors()
            await _render._render_diff(self)
            await self._await_content_mounts()
            self._restore_comment_editors()

    def _create_file_header_widget(self, *args, **kwargs):
        return _render._create_file_header_widget(self, *args, **kwargs)

    def _should_force_unified_for_hunk(self, hunk: DiffHunk) -> bool:
        return _render._should_force_unified_for_hunk(hunk)

    def _create_hunk_header_widget(self, *args, **kwargs):
        return _render._create_hunk_header_widget(self, *args, **kwargs)

    def _render_hunk(self, *args, **kwargs) -> None:
        _render._render_hunk(self, *args, **kwargs)

    def _finalize_render_state(self) -> None:
        _render._finalize_render_state(self)

    def _build_unified_prefix_content(self, line: DiffLine) -> Content:
        return _render._build_unified_prefix_content(self, line)

    def _build_unified_modified_prefix_content(self, *args, **kwargs) -> Content:
        return _render._build_unified_modified_prefix_content(self, *args, **kwargs)

    def _old_line_number_width(self) -> int:
        return _render._old_line_number_width(self)

    def _new_line_number_width(self) -> int:
        return _render._new_line_number_width(self)

    def _unified_prefix_width_for_layout(self, line: DiffLine | None = None) -> int:
        return _render._unified_prefix_width_for_layout(self, line)

    def _build_split_prefix(self, *args, **kwargs) -> Content:
        return _render._build_split_prefix(self, *args, **kwargs)

    def _build_split_prefix_content(self, line: DiffLine, **kwargs) -> Content:
        return _render._build_split_prefix_content(self, line, **kwargs)

    def _split_annotation_style(self, line: DiffLine, **kwargs) -> str:
        return _render._split_annotation_style(self, line, **kwargs)

    def _build_split_code_content(self, line: DiffLine, **kwargs) -> Content | None:
        return _render._build_split_code_content(self, line, **kwargs)

    def _unified_line_style(self, line: DiffLine, **kwargs) -> str:
        return _render._unified_line_style(self, line, **kwargs)

    def _split_line_style(self, line: DiffLine, **kwargs) -> str:
        return _render._split_line_style(self, line, **kwargs)

    def _mount_split_lines(self, *args, **kwargs) -> None:
        _render._mount_split_lines(self, *args, **kwargs)

    def _mount_unified_lines(self, *args, **kwargs) -> None:
        _render._mount_unified_lines(self, *args, **kwargs)

    def _base_code_content(self, line: DiffLine, **kwargs) -> Content:
        return _render._base_code_content(self, line, **kwargs)

    def _build_code_content_with_cursor(self, *args, **kwargs) -> Content:
        return _render._build_code_content_with_cursor(self, *args, **kwargs)

    def _update_line_cursor(self, line_idx: int) -> None:
        _render._update_line_cursor(self, line_idx)

    def _invalidate_base_code_content_cache(
        self, line_indices: Collection[int] | None = None
    ) -> None:
        _render._invalidate_base_code_content_cache(self, line_indices)

    def _comparison_heavy_ratio(self) -> float:
        return _render._comparison_heavy_ratio(self)

    def _average_render_line_height(self) -> float:
        return _render._average_render_line_height(self)

    def _render_height_for_line(self, line: DiffLine) -> int:
        return _render._render_height_for_line(self, line)

    def _line_index_at_vertical_offset(self, offset: int) -> int:
        return _render._line_index_at_vertical_offset(self, offset)

    def _viewport_center_line(self) -> int:
        return _render._viewport_center_line(self)

    def _get_rendered_line_bounds(self) -> tuple[int, int]:
        return _render._get_rendered_line_bounds(self)

    def _is_line_rendered(self, line_idx: int) -> bool:
        return _render._is_line_rendered(self, line_idx)

    def _should_render_hunk_header(self, *args, **kwargs) -> bool:
        return _render._should_render_hunk_header(self, *args, **kwargs)

    def _compute_base_code_content(self, *args, **kwargs) -> Content:
        return _render._compute_base_code_content(self, *args, **kwargs)

    def _current_cursor_viewport_offset(self) -> int | None:
        return _cursor._current_cursor_viewport_offset(self)

    def _queue_cursor_ui_flush(self, **kwargs) -> None:
        _cursor._queue_cursor_ui_flush(self, **kwargs)

    def _flush_queued_cursor_ui_updates(self) -> None:
        _cursor._flush_queued_cursor_ui_updates(self)

    def _copy_to_clipboard(self, text: str) -> None:
        self.app.copy_to_clipboard(text)
