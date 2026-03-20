from __future__ import annotations

import asyncio
import subprocess
import sys
from bisect import bisect_right
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pyperclip
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive, var
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from rit.core.highlighting import (
    highlight_lines_for_diff,
    highlight_lines_for_diff_range,
    prewarm_highlighter,
)
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile, ReviewThread
from rit.ui.icons import get_file_icon
from rit.ui.messages import Flash
from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_highlight as _hl
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_virtual as _virtual
from rit.ui.widgets.diff_types import (
    DEFAULT_DIFF_LAYOUT,
    DiffLayout,
    DiffSearchMatch,
    RenderedRow,
    SplitBlockLineStaticData,
    SplitDiffBlock,
    UnifiedBlockRowStaticData,
    UnifiedDiffBlock,
)
from rit.ui.widgets.diff_visual import DiffCode, LineAnnotations, LineContent

if TYPE_CHECKING:
    from rit.state.store import PRStore


_RENDER_REQUEST_CONTEXT: ContextVar[int | None] = ContextVar(
    "diff_view_render_request", default=None
)


class DiffView(VerticalScroll):
    can_focus = True
    LAYOUT = DEFAULT_DIFF_LAYOUT

    VIRTUALIZE_LINE_THRESHOLD = 1200
    BLOCK_RENDER_LINE_THRESHOLD = 120
    VIRTUAL_WINDOW_RADIUS = 120
    VIRTUAL_WINDOW_SHIFT_MARGIN = 40
    WINDOW_HIGHLIGHT_BUFFER = 60
    UNIFIED_BLOCK_CHUNK_SIZE = 64
    COMPLEX_DIFF_RATIO_THRESHOLD = 0.2
    DEFAULT_WINDOW_ROWS_MULTIPLIER = 2.0
    COMPLEX_DIFF_WINDOW_ROWS_MULTIPLIER = 0.75
    DYNAMIC_WINDOW_SHIFT_DIVISOR = 7
    MIN_DYNAMIC_WINDOW_RADIUS = 12

    DEFAULT_CSS = """
    DiffView {
        width: 1fr;
        overflow-x: auto;
        overflow-y: auto;
        layers: base search;
    }

    DiffView .diff-header {
        dock: top;
        text-style: bold;
        padding: 0 1;
        background: $surface;
        height: 3;
        content-align: left middle;
    }



    DiffView #diff-content {
        width: 1fr;
        height: auto;
        overflow-x: auto;
        overflow-y: auto;
    }

    DiffView .placeholder {
        color: $text-disabled;
        text-align: center;
        margin: 2;
    }

    DiffView .hunk-header {
        background: $surface;
        color: $text-disabled;
        padding: 0 1;
        height: 1;
        margin: 0;
    }

    /* ===== Line Structure: Horizontal(line-prefix, code-content) ===== */
    
    /* Horizontal container for each line */
    DiffView Horizontal.diff-line {
        height: 1;
        width: auto;
        min-width: 100%;
        margin: 0;
        padding: 0;
    }
    
    /* Vertical container for modified lines (contains 2 Horizontals) */
    DiffView Vertical.diff-line {
        height: auto;
        margin: 0;
        padding: 0;
    }

    /* Line number + prefix area (fixed width) */
    DiffView .line-prefix {
        width: __UNIFIED_PREFIX_WIDTH__;
        height: 1;
        color: $text-disabled;
        padding: 0;
        margin: 0;
    }

    /* Code content area (flexible width) */
    DiffView .code-content {
        width: auto;
        min-width: 1fr;
        height: 1;
        padding: 0;
        margin: 0;
    }

    /* Added/Removed backgrounds on code-content */
    DiffView .code-content.-added {
        background: $success 10%;
    }

    DiffView .code-content.-removed {
        background: $error 10%;
    }

    /* ===== Cursor & Visual Mode Styles (Vim-style) ===== */

    DiffView.-visual {
    }

    /* Visual mode selection */
    DiffView.-visual .code-content.-selected {
        background: $accent 25%;
    }

    DiffView.-visual .code-content.-selected.-added {
        background: $success 30%;
    }

    DiffView.-visual .code-content.-selected.-removed {
        background: $error 30%;
    }

    /* Visual mode anchor */
    DiffView.-visual .code-content.-anchor {
        background: $accent 35%;
        outline-left: thick $accent;
    }

    /* Split mode styles */
    DiffView.-split .split-container {
        layout: horizontal;
    }

    DiffView.-split .split-pane {
        width: 50%;
    }

    DiffView.-split .split-pane .line-prefix {
        width: __SPLIT_PREFIX_WIDTH__;
    }

    DiffView.-split .split-pane .code-content {
        min-width: 1fr;
    }

    DiffView .code-content.-placeholder {
        color: $text-disabled;
    }

    DiffView .-virtual-buffer {
        text-align: center;
    }

    DiffView .diff-block {
        height: auto;
        width: auto;
        min-width: 100%;
        margin: 0;
        padding: 0;
    }

    /* Block renderers contain multiple lines; override single-line height */
    DiffView .diff-block .line-prefix {
        height: auto;
    }

    DiffView .diff-block .code-content {
        height: auto;
    }

    DiffView .split-block {
        layout: horizontal;
    }

    DiffView .diff-block-anchors {
        width: 0;
        height: auto;
        margin: 0;
        padding: 0;
    }

    DiffView .diff-block-anchor {
        width: 0;
        height: 1;
        margin: 0;
        padding: 0;
    }

    DiffView .diff-header,
    DiffView #diff-thread-inspector-shell,
    DiffView #diff-content {
        layer: base;
    }

    DiffView #diff-search-bar {
        layer: search;
        dock: bottom;
        height: 1;
        min-height: 1;
        display: none;
    }

    DiffView #diff-search-bar Input {
        width: 1fr;
        height: 1;
        min-height: 1;
        border: none;
        padding: 0 1;
        background: $surface;
    }

    DiffView #diff-search-bar Input:focus {
        border: none;
    }

    DiffView #diff-search-bar .search-prompt {
        width: 1;
        height: 1;
        min-height: 1;
        padding: 0;
        background: $surface;
    }

    """.replace("__UNIFIED_PREFIX_WIDTH__", str(LAYOUT.unified_prefix_width)).replace(
        "__SPLIT_PREFIX_WIDTH__", str(LAYOUT.split_prefix_width)
    )

    BINDINGS = [
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
        Binding("escape", "exit_visual", "Exit Visual", show=False),
        Binding("w", "next_word", "Next Word", show=False),
        Binding("b", "prev_word", "Prev Word", show=False),
        Binding("/", "start_search", "Search", show=False),
        Binding("n", "next_search_match", "Next Match", show=False),
        Binding("N", "prev_search_match", "Prev Match", show=False),
        Binding("$", "end_of_line", "End of Line", show=False),
        Binding("tab", "cycle_active_pane", "Switch Pane", show=False),
        Binding("shift+tab", "cycle_active_pane_reverse", "", show=False),
        Binding("}", "next_comment", "Next Comment", show=False),
        Binding("{", "prev_comment", "Prev Comment", show=False),
        Binding("r", "toggle_resolve", "Resolve", show=False),
        Binding("|", "cycle_diff_mode", "Mode", show=False),
        Binding("z", "center_cursor", "Center", show=False),
    ]

    @dataclass
    class HunkNavigated(Message):
        hunk_index: int
        total_hunks: int

    @dataclass
    class CrossFileComment(Message):
        direction: Literal[1, -1]  # 1 = forward, -1 = backward

    mode: reactive[Literal["split", "unified", "auto"]] = reactive("auto")
    split: var[bool] = var(True, toggle_class="-split")
    active_pane: var[Literal["old", "new"]] = var("new")
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

        self._search_query: str = ""
        self._search_matches: list[DiffSearchMatch] = []
        self._search_match_index: int = -1
        self._prev_search_match_lines: set[int] = set()

        self._highlight_cache: set[tuple[int, bool]] = set()
        self._highlight_request_token: int = 0
        self._window_highlight_inflight: tuple[int, int, bool] | None = None
        self._queued_window_highlight: (
            tuple[str, FileDiff, int, int, bool, int] | None
        ) = None
        self._window_highlight_worker_active: bool = False
        self._queued_full_highlight: tuple[str, FileDiff, bool, int] | None = None
        self._full_highlight_worker_active: bool = False
        self._unified_block_static_rows_by_line: dict[
            int, tuple[UnifiedBlockRowStaticData, ...]
        ] = {}
        self._split_block_static_rows_by_line: dict[int, SplitBlockLineStaticData] = {}
        self._base_code_content_cache: dict[
            tuple[int, Literal["old", "new", "auto"], str], Content
        ] = {}
        self._line_index_by_new_number: dict[int, int] = {}
        self._line_index_by_old_number: dict[int, int] = {}
        self._hunk_index_by_line: list[int] = []
        self._modified_line_count: int = 0
        self._total_line_render_height: int = 0

        self._hunk_line_ranges: list[tuple[int, int, int]] = []
        self._hunk_header_top_offsets: list[int] = []
        self._line_top_offsets: list[int] = []
        self._line_heights: list[int] = []
        self._line_bottom_offsets: list[int] = []
        self._virtual_content_height: int = 0

        self._virtualized: bool = False
        self._virtual_window_start: int = 0
        self._virtual_window_end: int = -1
        self._rendered_window_start: int = 0
        self._rendered_window_end: int = -1
        self._window_render_pending: bool = False
        self._cursor_shift_pending: bool = False
        self._coalesced_scroll_center_line: int | None = None
        self._render_request_token: int = 0
        self._suspend_split_state_rerender: bool = False
        self._suspend_scroll_virtual_window_watch: bool = False

        self._code_widgets_by_line: dict[int, tuple[Static, ...]] = {}
        self._unified_blocks_by_line: dict[int, UnifiedDiffBlock] = {}
        self._split_blocks_by_line: dict[int, SplitDiffBlock] = {}
        self._line_widgets_by_index: dict[int, Widget] = {}
        self._row_anchor_widgets: dict[str, Widget] = {}
        self._hunk_header_widgets: dict[int, Static] = {}

        self._header_widget: Static | None = None
        self._search_bar_widget: Horizontal | None = None
        self._search_input_widget: Input | None = None

        self._content_widget: VerticalScroll | None = None

        self._top_virtual_buffer_widget: Static | None = None
        self._bottom_virtual_buffer_widget: Static | None = None

        self._highlighter_prewarm_started: bool = False
        self._suspend_active_pane_watch: bool = False
        self._suspend_cursor_line_watch: bool = False
        self._suspend_cursor_column_watch: bool = False
        self._suppress_cursor_scroll: bool = False

        self._cursor_ui_flush_pending: bool = False
        self._queued_cursor_dirty_lines: set[int] = set()
        self._queued_selection_dirty_lines: set[int] = set()
        self._queued_selection_full_refresh: bool = False

        self._queued_sync_search_match: bool = False
        self._queued_update_status_line: bool = False

        self._visual_selection_specs: dict[
            int,
            tuple[int, int | None, Literal["char", "line"]],
        ] = {}

        self._comment_threads_by_line: dict[int, list[ReviewThread]] = {}
        self._comment_line_indices: list[int] = []
        self._comment_widgets_by_line: dict[int, list[Widget]] = {}
        self._pending_comment_jump: str | None = None  # "first" or "last"

        self.mode = mode

    def compose(self) -> ComposeResult:
        yield Static(
            "Select a file to view diff",
            classes="diff-header",
            id="diff-header",
        )

        yield VerticalScroll(id="diff-content")
        with Horizontal(id="diff-search-bar"):
            yield Static("/", classes="search-prompt")
            yield Input(id="diff-search-input")

    @property
    def scrollable_content_region(self):
        region = super().scrollable_content_region
        if self._content_widget is not None:
            try:
                region = region.shrink(self._content_widget.dock_gutter)
            except Exception:
                pass
        return region

    def watch_mode(self, new_mode: Literal["split", "unified", "auto"]) -> None:
        self._update_split_state()
        _search.refresh_matches(self)
        self._queue_cursor_ui_flush(update_status_line=True)

    def watch_current_hunk_index(self, _old_index: int, _new_index: int) -> None:
        self._queue_cursor_ui_flush(update_status_line=True)

    def on_resize(self) -> None:
        self._update_split_state()
        _search.refresh_matches(self)
        self._queue_cursor_ui_flush(update_status_line=True)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if old_value == new_value or self._suspend_scroll_virtual_window_watch:
            return
        _virtual._maybe_update_virtual_window_from_viewport(self)
        if not self._virtualized and _hl._use_windowed_highlight_strategy(
            self, self._diff
        ):
            _hl._ensure_visible_highlight(self)

    def on_mount(self) -> None:
        self.can_focus = True
        self._header_widget = self.query_one("#diff-header", Static)
        self._search_bar_widget = self.query_one("#diff-search-bar", Horizontal)
        self._search_input_widget = self.query_one("#diff-search-input", Input)

        self._content_widget = self.query_one("#diff-content", VerticalScroll)
        self._update_status_line()
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
            or self._suspend_active_pane_watch
        ):
            return

        self._clamp_cursor_column_to_current_row()

        if not self.visual_mode:
            self._scroll_to_cursor()

        self._queue_cursor_ui_flush(
            cursor_lines={self.cursor_line},
            selection_dirty_lines={self.cursor_line} if self.visual_mode else None,
            sync_search_match=True,
            update_status_line=True,
        )

    def watch_cursor_line(self, old_line: int, new_line: int) -> None:
        if (
            not self._all_lines
            or not self.is_mounted
            or self._suspend_cursor_line_watch
        ):
            return

        self._clamp_cursor_column_to_current_row()

        hunk_index = self._get_hunk_index_for_line(new_line)
        if hunk_index is not None and hunk_index != self.current_hunk_index:
            self.current_hunk_index = hunk_index

        _virtual._maybe_update_virtual_window(self, new_line)
        _comments.update_cursor_highlight(self, old_line, new_line)

        if not self.visual_mode:
            self._scroll_to_cursor()

        self._queue_cursor_ui_flush(
            cursor_lines={old_line, new_line},
            selection_dirty_lines={old_line, new_line} if self.visual_mode else None,
            sync_search_match=True,
            update_status_line=True,
        )

    def watch_cursor_column(self, old_col: int, new_col: int) -> None:
        if (
            not self._all_lines
            or not self.is_mounted
            or self._suspend_cursor_column_watch
        ):
            return

        if self.cursor_line < len(self._all_lines):
            text = self._get_cursor_text()

            if len(text) == 0:
                self.cursor_column = 0
                return

            max_col = len(text) - 1
            if new_col > max_col:
                self.cursor_column = max_col
                return

            self._queue_cursor_ui_flush(
                cursor_lines={self.cursor_line},
                selection_dirty_lines={self.cursor_line} if self.visual_mode else None,
                sync_search_match=True,
            )
            self._scroll_to_cursor_horizontal()

    def watch_visual_mode(self, old_mode: bool, new_mode: bool) -> None:
        if new_mode:
            self.app.sub_title = (
                "-- VISUAL LINE --" if self.visual_type == "line" else "-- VISUAL --"
            )
            self._update_selection_highlighting({self.cursor_line})
        else:
            self.app.sub_title = ""
            for line_idx in list(self._visual_selection_specs):
                self._clear_line_selection(line_idx)
            self._visual_selection_specs = {}

        self._update_status_line()

    def watch_visual_type(
        self, old_type: Literal["char", "line"], new_type: Literal["char", "line"]
    ) -> None:
        if self.visual_mode:
            self.app.sub_title = (
                "-- VISUAL LINE --" if new_type == "line" else "-- VISUAL --"
            )
            self._queue_cursor_ui_flush(
                selection_dirty_lines={self.cursor_line},
                update_status_line=True,
            )
            return
        self._queue_cursor_ui_flush(update_status_line=True)

    def _enter_visual_mode(self, visual_type: Literal["char", "line"]) -> None:
        self.visual_type = visual_type

        if not self.visual_mode:
            self.visual_mode = True
            self.visual_anchor_line = self.cursor_line
            self.visual_anchor_column = self.cursor_column
            return

        if self.visual_anchor_line is None:
            self.visual_anchor_line = self.cursor_line
        if self.visual_anchor_column is None:
            self.visual_anchor_column = self.cursor_column

    def _exit_visual_mode(self) -> None:
        self.visual_mode = False
        self.visual_anchor_line = None
        self.visual_anchor_column = None

    def watch_visual_anchor_line(
        self, old_anchor: int | None, new_anchor: int | None
    ) -> None:
        if self.visual_mode:
            self._queue_cursor_ui_flush(selection_dirty_lines={self.cursor_line})

    def watch_visual_anchor_column(
        self, old_col: int | None, new_col: int | None
    ) -> None:
        if self.visual_mode:
            self._queue_cursor_ui_flush(selection_dirty_lines={self.cursor_line})

    def action_scroll_down(self) -> None:
        if self._all_lines:
            self._move_cursor_rows(1, scroll_in_visual=self.visual_mode)
        else:
            super().action_scroll_down()

    def action_scroll_up(self) -> None:
        if self._all_lines:
            self._move_cursor_rows(-1, scroll_in_visual=self.visual_mode)
        else:
            super().action_scroll_up()

    def action_cursor_left(self) -> None:
        if not self._all_lines:
            return

        if self.visual_mode and self.visual_type == "line":
            return

        if self.cursor_column > 0:
            self._move_cursor(column=self.cursor_column - 1)

    def action_cursor_right(self) -> None:
        if not self._all_lines:
            return

        if self.visual_mode and self.visual_type == "line":
            return

        if self.cursor_line >= len(self._all_lines):
            return

        text = self._get_cursor_text()
        if not text:
            self._move_cursor(column=0)
            return

        max_col = len(text) - 1
        if self.cursor_column < max_col:
            self._move_cursor(column=self.cursor_column + 1)

    def action_start_of_line(self) -> None:
        if not self._all_lines:
            return

        if self.visual_mode and self.visual_type == "line":
            return

        self._move_cursor(column=0)

    def action_first_non_blank(self) -> None:
        if not self._all_lines:
            return

        if self.visual_mode and self.visual_type == "line":
            return

        if not (0 <= self.cursor_line < len(self._all_lines)):
            return

        text = self._get_cursor_text()
        first_non_blank = 0
        while first_non_blank < len(text) and text[first_non_blank].isspace():
            first_non_blank += 1

        self._move_cursor(column=0 if first_non_blank >= len(text) else first_non_blank)

    def action_end_of_line(self) -> None:
        if not self._all_lines:
            return

        if self.visual_mode and self.visual_type == "line":
            return

        if not (0 <= self.cursor_line < len(self._all_lines)):
            return

        text = self._get_cursor_text()
        self._move_cursor(column=max(0, len(text) - 1))

    def action_scroll_home(self) -> None:
        rows = self._rows_for_current_mode()
        if rows:
            self._jump_to_row_with_anchor(rows[0], viewport_offset=0)
            return

        self.scroll_home(animate=False)

    def action_scroll_end(self) -> None:
        rows = self._rows_for_current_mode()
        if rows:
            self._jump_to_row_with_anchor(rows[-1], bottom_align=True)
            self.scroll_end(animate=False)
            self._flush_cursor_ui_now_if_safe()
            return

        self.scroll_end(animate=False)

    def _half_page_step(self) -> int:
        return max(1, self.scrollable_content_region.height // 2)

    def _current_cursor_viewport_offset(self) -> int | None:
        row = self._current_row()
        if row is None:
            return None

        bounds = self._row_vertical_bounds(row)
        if bounds is None:
            return None

        top, _ = bounds
        return max(0, top - int(self.scroll_y))

    def _scroll_row_to_viewport_offset(
        self,
        row: RenderedRow,
        viewport_offset: int,
        *,
        animate: bool = False,
    ) -> None:
        bounds = self._row_vertical_bounds(row)
        if bounds is None:
            return

        top, bottom = bounds
        viewport_height = max(1, self.scrollable_content_region.height)
        target_scroll = max(0, top - max(0, viewport_offset))
        if bottom - target_scroll > viewport_height:
            target_scroll = max(0, bottom - viewport_height)

        self.scroll_to(
            y=min(target_scroll, max(0, int(self.max_scroll_y))),
            animate=animate,
        )

    def _scroll_row_to_viewport_bottom(
        self,
        row: RenderedRow,
        *,
        animate: bool = False,
    ) -> None:
        bounds = self._row_vertical_bounds(row)
        if bounds is None:
            return

        _, bottom = bounds
        viewport_height = max(1, self.scrollable_content_region.height)
        self.scroll_to(
            y=min(max(0, bottom - viewport_height), max(0, int(self.max_scroll_y))),
            animate=animate,
        )

    def _row_is_visible(self, row: RenderedRow) -> bool:
        bounds = self._row_vertical_bounds(row)
        if bounds is None:
            return False

        top, bottom = bounds
        current_top = int(self.scroll_y)
        current_bottom = current_top + max(1, self.scrollable_content_region.height)
        return top >= current_top and bottom <= current_bottom

    def _jump_to_row_with_anchor(
        self,
        row: RenderedRow,
        *,
        pane: Literal["old", "new"] | None = None,
        column: int | None = None,
        viewport_offset: int | None = None,
        bottom_align: bool = False,
        animate: bool = False,
        reveal_horizontal: bool = False,
    ) -> None:
        target_pane = pane
        if target_pane is None and row.side != "auto":
            target_pane = "old" if row.side == "old" else "new"

        self._suppress_cursor_scroll = True
        try:
            self._move_cursor(
                line=row.line_index,
                pane=target_pane,
                column=column,
                scroll_in_visual=self.visual_mode,
            )
        finally:
            self._suppress_cursor_scroll = False

        if bottom_align:
            self._scroll_row_to_viewport_bottom(row, animate=animate)
        elif viewport_offset is not None:
            self._scroll_row_to_viewport_offset(
                row,
                viewport_offset,
                animate=animate,
            )
        else:
            self._scroll_to_cursor()

        if reveal_horizontal:
            self._scroll_to_cursor_horizontal()

        self._flush_cursor_ui_now_if_safe()

    def _flush_cursor_ui_now_if_safe(self) -> None:
        if (
            not self.is_mounted
            or not self._cursor_ui_flush_pending
            or self._window_render_pending
        ):
            return

        self._flush_queued_cursor_ui_updates()

    async def action_half_page_down(self) -> None:
        if self._all_lines:
            await self._animated_half_page_scroll(1)
            return

        self.scroll_page_down(animate=False)

    async def action_half_page_up(self) -> None:
        if self._all_lines:
            await self._animated_half_page_scroll(-1)
            return

        self.scroll_page_up(animate=False)

    async def _animated_half_page_scroll(self, direction: int) -> None:
        step = self._half_page_step()
        viewport_offset = self._current_cursor_viewport_offset()
        delay = 0.15 / step

        for _ in range(step):
            self._suppress_cursor_scroll = True
            try:
                moved = self._move_cursor_rows(
                    direction,
                    scroll_in_visual=self.visual_mode,
                )
            finally:
                self._suppress_cursor_scroll = False
            if not moved:
                break
            if viewport_offset is not None:
                row = self._current_row()
                if row is not None:
                    self._scroll_row_to_viewport_offset(row, viewport_offset)
            self._flush_cursor_ui_now_if_safe()
            await asyncio.sleep(delay)

    def action_cycle_active_pane(self) -> None:
        if not self._all_lines:
            return

        line = self._current_line()
        if line is None:
            return

        if not self.split and not line.is_modified:
            return

        target_pane: Literal["old", "new"] = (
            "old" if self._resolve_active_pane_for_line(line) == "new" else "new"
        )
        self._move_cursor(pane=target_pane, scroll_in_visual=self.visual_mode)

    def action_cycle_active_pane_reverse(self) -> None:
        self.action_cycle_active_pane()

    def action_toggle_visual(self) -> None:
        if self.visual_mode and self.visual_type == "char":
            self._exit_visual_mode()
            return

        self._enter_visual_mode("char")

    def action_toggle_visual_line(self) -> None:
        if self.visual_mode and self.visual_type == "line":
            self._exit_visual_mode()
            return

        self._enter_visual_mode("line")

    def action_yank(self) -> None:
        if not self._all_lines:
            return

        if not self.visual_mode:
            if not (0 <= self.cursor_line < len(self._all_lines)):
                return

            text_to_copy = self._get_cursor_text() + "\n"
            try:
                self._copy_to_clipboard(text_to_copy)
                self.post_message(Flash("Copied 1 line", style="success", duration=2.0))
            except Exception as e:
                self.post_message(
                    Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
                )
            return

        if self.visual_anchor_line is None:
            return

        start_line = min(self.visual_anchor_line, self.cursor_line)
        end_line = max(self.visual_anchor_line, self.cursor_line)

        if self.visual_type == "line":
            selected_lines = [
                self._get_line_text(self._all_lines[line_idx])
                for line_idx in range(start_line, end_line + 1)
            ]
            text_to_copy = "\n".join(selected_lines)
            if text_to_copy:
                text_to_copy += "\n"

            try:
                self._copy_to_clipboard(text_to_copy)
                line_count = end_line - start_line + 1
                self.post_message(
                    Flash(
                        f"Copied {line_count} line{'s' if line_count != 1 else ''}",
                        style="success",
                        duration=2.0,
                    )
                )
            except Exception as e:
                self.post_message(
                    Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
                )

            self._exit_visual_mode()
            return

        start_col = (
            self.visual_anchor_column if self.visual_anchor_column is not None else 0
        )
        end_col = self.cursor_column

        if self.visual_anchor_line < self.cursor_line:
            first_line_col = start_col
            last_line_col = end_col
        elif self.visual_anchor_line > self.cursor_line:
            first_line_col = end_col
            last_line_col = start_col
        else:
            first_line_col = min(start_col, end_col)
            last_line_col = max(start_col, end_col)

        selected_lines = []

        if start_line == end_line:
            line = self._all_lines[start_line]
            text = self._get_line_text(line)
            actual_start = min(start_col, end_col)
            actual_end = max(start_col, end_col)
            selected_lines.append(text[actual_start : actual_end + 1])
        else:
            for line_idx in range(start_line, end_line + 1):
                line = self._all_lines[line_idx]
                text = self._get_line_text(line)

                if line_idx == start_line:
                    selected_lines.append(text[first_line_col:])
                elif line_idx == end_line:
                    selected_lines.append(text[: last_line_col + 1])
                else:
                    selected_lines.append(text)

        text_to_copy = "\n".join(selected_lines)

        try:
            self._copy_to_clipboard(text_to_copy)
            char_count = len(text_to_copy)
            self.post_message(
                Flash(
                    f"Copied {char_count} character{'s' if char_count != 1 else ''}",
                    style="success",
                    duration=2.0,
                )
            )
        except Exception as e:
            self.post_message(
                Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
            )

        self._exit_visual_mode()

    @work(thread=True)
    async def _copy_to_clipboard_async(self, text: str) -> None:
        from rit.ui.messages import Flash

        try:
            await asyncio.to_thread(pyperclip.copy, text)
            self.app.post_message(
                Flash("Copied to clipboard", style="success", duration=2.0)
            )
        except Exception as e:
            self.app.post_message(
                Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
            )

    def action_exit_visual(self) -> None:
        if self.visual_mode:
            self._exit_visual_mode()
        elif self._search_query:
            _search.clear_state(self)
            _search._refresh_search_display(self)

    def action_next_word(self) -> None:
        if not self._all_lines or self.cursor_line >= len(self._all_lines):
            return

        text = self._get_cursor_text()

        next_pos = self._find_next_word_start(text, self.cursor_column)

        if next_pos is not None:
            self._move_cursor(column=next_pos)
            return

        rows = self._rows_for_current_mode()
        current = self._current_row_index()
        if current >= len(rows) - 1:
            return

        target_row = rows[current + 1]
        next_text = self._get_cursor_text_for_target(
            target_row.line_index,
            self.active_pane
            if target_row.side == "auto"
            else ("old" if target_row.side == "old" else "new"),
        )
        self._move_cursor(
            line=target_row.line_index,
            pane=None
            if target_row.side == "auto"
            else ("old" if target_row.side == "old" else "new"),
            column=self._find_first_word(next_text),
            scroll_in_visual=self.visual_mode,
        )

    def action_prev_word(self) -> None:
        if not self._all_lines or self.cursor_line >= len(self._all_lines):
            return

        text = self._get_cursor_text()

        prev_pos = self._find_prev_word_start(text, self.cursor_column)

        if prev_pos is not None:
            self._move_cursor(column=prev_pos)
            return

        rows = self._rows_for_current_mode()
        current = self._current_row_index()
        if current <= 0:
            return

        target_row = rows[current - 1]
        prev_text = self._get_cursor_text_for_target(
            target_row.line_index,
            self.active_pane
            if target_row.side == "auto"
            else ("old" if target_row.side == "old" else "new"),
        )
        self._move_cursor(
            line=target_row.line_index,
            pane=None
            if target_row.side == "auto"
            else ("old" if target_row.side == "old" else "new"),
            column=max(0, len(prev_text) - 1),
            scroll_in_visual=self.visual_mode,
        )

    def action_end_word(self) -> None:
        if not self._all_lines or self.cursor_line >= len(self._all_lines):
            return

        text = self._get_cursor_text()

        end_pos = self._find_next_word_end(text, self.cursor_column)

        if end_pos is not None:
            self._move_cursor(column=end_pos)
            return

        rows = self._rows_for_current_mode()
        current = self._current_row_index()
        if current >= len(rows) - 1:
            return

        target_row = rows[current + 1]
        next_text = self._get_cursor_text_for_target(
            target_row.line_index,
            self.active_pane
            if target_row.side == "auto"
            else ("old" if target_row.side == "old" else "new"),
        )
        first_word_pos = self._find_first_word(next_text)
        end_pos = self._find_next_word_end(next_text, first_word_pos - 1)
        self._move_cursor(
            line=target_row.line_index,
            pane=None
            if target_row.side == "auto"
            else ("old" if target_row.side == "old" else "new"),
            column=end_pos if end_pos is not None else first_word_pos,
            scroll_in_visual=self.visual_mode,
        )

    def action_start_search(self) -> None:
        bar = self._search_bar_widget
        inp = self._search_input_widget
        if bar is None or inp is None:
            return
        bar.display = True
        inp.value = self._search_query
        inp.focus()

    @on(Input.Changed, "#diff-search-input")
    def _on_search_changed(self, event: Input.Changed) -> None:
        event.stop()
        query = event.value.strip()
        if query:
            self._search_query = query
            _search.refresh_matches(self)
            self._search_match_index = _search.next_match_index_from_cursor(self)
        else:
            _search.clear_state(self)
        _search._refresh_search_display(self)

    @on(Input.Submitted, "#diff-search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._search_bar_widget is not None:
            self._search_bar_widget.display = False
        self.focus()
        _search.handle_submitted(self, event.value)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            bar = self._search_bar_widget
            if bar is not None and bar.display:
                event.stop()
                event.prevent_default()
                bar.display = False
                _search.clear_state(self)
                _search._refresh_search_display(self)
                self.focus()
                return
        if event.key == "enter":
            if _comments.try_toggle_current(self):
                event.stop()
                event.prevent_default()
                return

    def action_next_search_match(self) -> None:
        _search.jump_match(self, 1)

    def action_prev_search_match(self) -> None:
        _search.jump_match(self, -1)

    def _copy_to_clipboard(self, text: str) -> None:
        if sys.platform == "darwin":
            process = subprocess.Popen(
                ["pbcopy"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process.communicate(text.encode("utf-8"))
        elif sys.platform.startswith("linux"):
            try:
                process = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                process.communicate(text.encode("utf-8"))
            except FileNotFoundError:
                process = subprocess.Popen(
                    ["xsel", "--clipboard", "--input"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                process.communicate(text.encode("utf-8"))
        elif sys.platform == "win32":
            process = subprocess.Popen(
                ["clip"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            process.communicate(text.encode("utf-8"))

    def _should_force_unified_for_current_file(self) -> bool:
        if self._file is not None and self._file.status in {"added", "removed"}:
            return True

        diff = self._diff
        if diff is None:
            return False
        if diff.is_new or diff.is_deleted:
            return True

        all_lines = self._all_lines
        if not all_lines:
            return False

        return all(line.is_added for line in all_lines) or all(
            line.is_deleted for line in all_lines
        )

    def _update_split_state(self) -> None:
        old_split = self.split

        if self.mode == "split":
            self.split = True
        elif self.mode == "unified":
            self.split = False
        else:  # auto
            self.split = self.size.width >= self.LAYOUT.auto_split_min_width

        if self.split and self._should_force_unified_for_current_file():
            self.split = False

        if old_split != self.split and self._all_lines:
            _virtual._rebuild_virtual_layout(self)
            if self._virtualized:
                _virtual._set_virtual_window_from_viewport(self)

        if (
            old_split != self.split
            and self.is_mounted
            and self._diff is not None
            and not self._window_render_pending
            and not self._suspend_split_state_rerender
        ):
            self.run_worker(
                self._run_render_diff_for_request(self._render_request_token),
                exclusive=True,
                name="diff-mode-rerender",
            )

    def _row_kind_for_line(
        self,
        line: DiffLine,
        *,
        modified_side: Literal["old", "new"] | None = None,
    ) -> Literal[
        "context",
        "added",
        "deleted",
        "modified-old",
        "modified-new",
    ]:
        if line.is_modified:
            return "modified-old" if modified_side == "old" else "modified-new"
        if line.is_added:
            return "added"
        if line.is_deleted:
            return "deleted"
        return "context"

    def _rebuild_rendered_rows(self) -> None:
        self._rows_unified = []
        self._rows_split = []
        self._row_lookup_unified = {}
        self._row_lookup_split = {}

        if self._diff is None:
            return

        for hunk_index, hunk in enumerate(self._diff.hunks):
            for line in hunk.lines:
                if line.is_modified:
                    old_row = RenderedRow(
                        mode="unified",
                        row_index=len(self._rows_unified),
                        line_index=line.line_index,
                        hunk_index=hunk_index,
                        kind=self._row_kind_for_line(line, modified_side="old"),
                        side="old",
                        anchor_id=f"line-{line.line_index}-old",
                        old_line_no=line.old_line_no,
                        new_line_no=line.new_line_no,
                    )
                    self._rows_unified.append(old_row)
                    self._row_lookup_unified[(line.line_index, "old")] = (
                        old_row.row_index
                    )

                    new_row = RenderedRow(
                        mode="unified",
                        row_index=len(self._rows_unified),
                        line_index=line.line_index,
                        hunk_index=hunk_index,
                        kind=self._row_kind_for_line(line, modified_side="new"),
                        side="new",
                        anchor_id=f"line-{line.line_index}-new",
                        old_line_no=line.old_line_no,
                        new_line_no=line.new_line_no,
                    )
                    self._rows_unified.append(new_row)
                    self._row_lookup_unified[(line.line_index, "new")] = (
                        new_row.row_index
                    )
                else:
                    side: Literal["old", "new", "auto"]
                    if line.is_deleted:
                        side = "old"
                    elif line.is_added:
                        side = "new"
                    else:
                        side = "auto"

                    row = RenderedRow(
                        mode="unified",
                        row_index=len(self._rows_unified),
                        line_index=line.line_index,
                        hunk_index=hunk_index,
                        kind=self._row_kind_for_line(line),
                        side=side,
                        anchor_id=f"line-{line.line_index}",
                        old_line_no=line.old_line_no,
                        new_line_no=line.new_line_no,
                    )
                    self._rows_unified.append(row)
                    self._row_lookup_unified[(line.line_index, side)] = row.row_index

                split_row = RenderedRow(
                    mode="split",
                    row_index=len(self._rows_split),
                    line_index=line.line_index,
                    hunk_index=hunk_index,
                    kind=self._row_kind_for_line(line),
                    side="auto",
                    anchor_id=f"line-{line.line_index}",
                    old_line_no=line.old_line_no,
                    new_line_no=line.new_line_no,
                )
                self._rows_split.append(split_row)
                self._row_lookup_split[line.line_index] = split_row.row_index

    def _rows_for_current_mode(self) -> list[RenderedRow]:
        return self._rows_split if self.split else self._rows_unified

    def _current_line(self) -> DiffLine | None:
        if not self._all_lines or not (0 <= self.cursor_line < len(self._all_lines)):
            return None
        return self._all_lines[self.cursor_line]

    def _resolve_active_pane_for_line(
        self,
        line: DiffLine,
        pane: Literal["old", "new"] | None = None,
    ) -> Literal["old", "new"]:
        active_pane = self.active_pane if pane is None else pane
        if line.is_added and not line.is_modified:
            return "new"
        if line.is_deleted and not line.is_modified:
            return "old"
        return active_pane

    def _cursor_side_for_line(
        self,
        line: DiffLine,
        pane: Literal["old", "new"] | None = None,
    ) -> Literal["old", "new", "auto"]:
        if self.split:
            return self._resolve_active_pane_for_line(line, pane)
        if line.is_modified:
            return self._resolve_active_pane_for_line(line, pane)
        if line.is_deleted:
            return "old"
        if line.is_added:
            return "new"
        return "auto"

    def _current_cursor_side(self) -> Literal["old", "new", "auto"]:
        line = self._current_line()
        if line is None:
            return "auto"
        return self._cursor_side_for_line(line)

    def _get_cursor_text_for_target(
        self,
        line_index: int,
        pane: Literal["old", "new"],
    ) -> str:
        if not (0 <= line_index < len(self._all_lines)):
            return ""

        line = self._all_lines[line_index]
        side = self._cursor_side_for_line(line, pane)
        return self._get_line_text(line, side)

    def _current_row_index(self) -> int:
        line = self._current_line()
        if line is None:
            return 0

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

    def _queue_cursor_ui_flush(
        self,
        *,
        cursor_lines: set[int] | None = None,
        selection_dirty_lines: set[int] | None = None,
        selection_full_refresh: bool = False,
        sync_search_match: bool = False,
        update_status_line: bool = False,
    ) -> None:
        if not self.is_mounted:
            if sync_search_match:
                _search.sync_match_index_to_cursor(self)
            if update_status_line:
                self._update_status_line()
            return

        if cursor_lines:
            self._queued_cursor_dirty_lines.update(
                line_idx
                for line_idx in cursor_lines
                if 0 <= line_idx < len(self._all_lines)
            )
        if selection_dirty_lines:
            self._queued_selection_dirty_lines.update(
                line_idx
                for line_idx in selection_dirty_lines
                if 0 <= line_idx < len(self._all_lines)
            )
        if selection_full_refresh:
            self._queued_selection_full_refresh = True
        if sync_search_match:
            self._queued_sync_search_match = True
        if update_status_line:
            self._queued_update_status_line = True

        if self._cursor_ui_flush_pending:
            return

        self._cursor_ui_flush_pending = True
        self.call_next(self._flush_queued_cursor_ui_updates)

    def _flush_queued_cursor_ui_updates(self) -> None:
        self._cursor_ui_flush_pending = False

        cursor_lines = set(self._queued_cursor_dirty_lines)
        selection_dirty_lines = set(self._queued_selection_dirty_lines)
        selection_full_refresh = self._queued_selection_full_refresh
        sync_search_match = self._queued_sync_search_match
        update_status_line = self._queued_update_status_line

        self._queued_cursor_dirty_lines.clear()
        self._queued_selection_dirty_lines.clear()
        self._queued_selection_full_refresh = False
        self._queued_sync_search_match = False
        self._queued_update_status_line = False

        if not self.is_mounted:
            return

        if self.visual_mode:
            if selection_full_refresh:
                cursor_lines.clear()
            elif selection_dirty_lines:
                cursor_lines.difference_update(selection_dirty_lines)

        if cursor_lines:
            if not _blocks._refresh_grouped_blocks_for_lines(self, cursor_lines):
                for line_idx in sorted(cursor_lines):
                    self._update_line_cursor(line_idx)

        if selection_full_refresh:
            self._update_selection_highlighting()
        elif selection_dirty_lines:
            self._update_selection_highlighting(selection_dirty_lines)

        if sync_search_match:
            _search.sync_match_index_to_cursor(self)
        if update_status_line:
            self._update_status_line()

    def _apply_cursor_move_side_effects(
        self,
        *,
        old_line: int,
        new_line: int,
        old_column: int,
        new_column: int,
        old_pane: Literal["old", "new"],
        new_pane: Literal["old", "new"],
        scroll_in_visual: bool,
    ) -> None:
        line_changed = old_line != new_line
        column_changed = old_column != new_column
        pane_changed = old_pane != new_pane

        dirty_cursor_lines = {new_line}
        if line_changed:
            dirty_cursor_lines.add(old_line)
            hunk_index = self._get_hunk_index_for_line(new_line)
            if hunk_index is not None and hunk_index != self.current_hunk_index:
                self.current_hunk_index = hunk_index
            _virtual._maybe_update_virtual_window(self, new_line)
            _comments.update_cursor_highlight(self, old_line, new_line)

        dirty_lines: set[int] = set()
        if self.visual_mode:
            dirty_lines = {new_line}
            if line_changed:
                dirty_lines.add(old_line)
            if scroll_in_visual and (line_changed or pane_changed):
                if not self._suppress_cursor_scroll:
                    self._scroll_to_cursor()
        else:
            if line_changed or pane_changed:
                if not self._suppress_cursor_scroll:
                    self._scroll_to_cursor()

        if column_changed or pane_changed:
            if not self._suppress_cursor_scroll:
                self._scroll_to_cursor_horizontal()

        if line_changed or pane_changed or column_changed:
            self._queue_cursor_ui_flush(
                cursor_lines=dirty_cursor_lines,
                selection_dirty_lines=dirty_lines if self.visual_mode else None,
                sync_search_match=True,
                update_status_line=(line_changed or pane_changed)
                or (column_changed and bool(self._search_query)),
            )

    def _move_cursor(
        self,
        *,
        line: int | None = None,
        column: int | None = None,
        pane: Literal["old", "new"] | None = None,
        scroll_in_visual: bool = False,
    ) -> bool:
        if not self._all_lines:
            return False

        old_line = self.cursor_line
        old_column = self.cursor_column
        old_pane = self.active_pane

        target_line = (
            old_line if line is None else max(0, min(line, len(self._all_lines) - 1))
        )
        target_line_obj = self._all_lines[target_line]
        requested_pane = old_pane if pane is None else pane
        target_pane = self._resolve_active_pane_for_line(
            target_line_obj, requested_pane
        )

        target_text = self._get_cursor_text_for_target(target_line, target_pane)
        requested_column = old_column if column is None else column
        if target_text:
            target_column = max(0, min(requested_column, len(target_text) - 1))
        else:
            target_column = 0

        if (
            target_line == old_line
            and target_column == old_column
            and target_pane == old_pane
        ):
            return False

        self._suspend_active_pane_watch = True
        self._suspend_cursor_line_watch = True
        self._suspend_cursor_column_watch = True
        try:
            self.active_pane = target_pane
            self.cursor_line = target_line
            self.cursor_column = target_column
        finally:
            self._suspend_active_pane_watch = False
            self._suspend_cursor_line_watch = False
            self._suspend_cursor_column_watch = False

        self._apply_cursor_move_side_effects(
            old_line=old_line,
            new_line=target_line,
            old_column=old_column,
            new_column=target_column,
            old_pane=old_pane,
            new_pane=target_pane,
            scroll_in_visual=scroll_in_visual,
        )
        return True

    def _move_cursor_to_row(
        self,
        row: RenderedRow,
        *,
        scroll_in_visual: bool = False,
    ) -> bool:
        target_pane: Literal["old", "new"] | None
        if row.side == "auto":
            target_pane = None
        else:
            target_pane = "old" if row.side == "old" else "new"
        return self._move_cursor(
            line=row.line_index,
            pane=target_pane,
            scroll_in_visual=scroll_in_visual,
        )

    def _set_cursor_from_row(self, row: RenderedRow) -> None:
        self._move_cursor_to_row(row)

    def _move_cursor_rows(self, delta: int, *, scroll_in_visual: bool = False) -> bool:
        rows = self._rows_for_current_mode()
        if not rows:
            return False

        current = self._current_row_index()
        target = max(0, min(current + delta, len(rows) - 1))
        if target == current:
            return False

        return self._move_cursor_to_row(rows[target], scroll_in_visual=scroll_in_visual)

    def _first_row_for_hunk(self, hunk_index: int) -> RenderedRow | None:
        for row in self._rows_for_current_mode():
            if row.hunk_index == hunk_index:
                return row
        return None

    def _get_cursor_text(self) -> str:
        line = self._current_line()
        if line is None:
            return ""
        return self._get_line_text(line, self._cursor_side_for_line(line))

    def _clamp_cursor_column_to_current_row(self) -> None:
        text = self._get_cursor_text()
        if text:
            self.cursor_column = min(self.cursor_column, len(text) - 1)
        else:
            self.cursor_column = 0

    def _row_vertical_bounds(self, row: RenderedRow) -> tuple[int, int] | None:
        if not (0 <= row.line_index < len(self._all_lines)):
            return None

        top = self._line_top_offsets[row.line_index]
        bottom = self._line_bottom_offsets[row.line_index]
        line = self._all_lines[row.line_index]

        if not self.split and line.is_modified:
            if row.side == "old":
                return top, top + 1
            if row.side == "new":
                return top + 1, top + 2

        return top, bottom

    def _scroll_to_vertical_span(
        self,
        top: int,
        bottom: int,
        *,
        animate: bool = False,
        top_align: bool = False,
    ) -> None:
        viewport_height = max(1, self.scrollable_content_region.height)
        current_top = int(self.scroll_y)
        current_bottom = current_top + viewport_height

        if top_align:
            self.scroll_to(y=max(0, top), animate=animate)
            return

        if top < current_top:
            self.scroll_to(y=max(0, top), animate=animate)
            return

        if bottom > current_bottom:
            self.scroll_to(y=max(0, bottom - viewport_height), animate=animate)

    def _comparison_heavy_ratio(self) -> float:
        if not self._all_lines:
            return 0.0
        return self._modified_line_count / len(self._all_lines)

    def _average_render_line_height(self) -> float:
        if not self._all_lines or self._total_line_render_height <= 0:
            return 1.0
        return self._total_line_render_height / len(self._all_lines)

    async def prepare(self) -> None:
        if self._diff:
            await asyncio.to_thread(self._precompute_diff_data)

    def _precompute_diff_data(self) -> None:
        if self._diff is not None:
            _hl._highlight_diff_sync(self, self._diff)

    def _invalidate_base_code_content_cache(
        self, line_indices: set[int] | None = None
    ) -> None:
        if line_indices is None:
            self._base_code_content_cache.clear()
            return

        for line_idx in line_indices:
            for side in ("old", "new", "auto"):
                self._base_code_content_cache.pop((line_idx, side, ""), None)
                self._base_code_content_cache.pop((line_idx, side, " "), None)

    def _render_height_for_line(self, line: DiffLine) -> int:
        if not self.split and line.is_modified:
            return 2
        return 1

    def _line_index_at_vertical_offset(self, offset: int) -> int:
        if not self._all_lines:
            return 0

        clamped = max(0, min(offset, max(0, self._virtual_content_height - 1)))
        index = bisect_right(self._line_top_offsets, clamped) - 1
        if index < 0:
            return 0

        if clamped >= self._line_bottom_offsets[index] and index + 1 < len(
            self._all_lines
        ):
            return index + 1
        return index

    def _viewport_center_line(self) -> int:
        if not self._all_lines:
            return 0

        viewport_height = max(1, self.scrollable_content_region.height)
        center_offset = int(self.scroll_y + viewport_height / 2)
        return self._line_index_at_vertical_offset(center_offset)

    def _get_rendered_line_bounds(self) -> tuple[int, int]:
        if not self._all_lines:
            return 0, -1

        if self._virtualized:
            start = max(0, self._rendered_window_start)
            end = min(len(self._all_lines) - 1, self._rendered_window_end)
            return start, end

        return 0, len(self._all_lines) - 1

    def _is_line_rendered(self, line_idx: int) -> bool:
        if line_idx < 0 or line_idx >= len(self._all_lines):
            return False

        start, end = self._get_rendered_line_bounds()
        return start <= line_idx <= end

    def _get_line_container(self, line_idx: int):
        if not self._is_line_rendered(line_idx):
            return None

        return self._line_widgets_by_index.get(line_idx)

    def _get_hunk_header_widget(self, hunk_index: int):
        return self._hunk_header_widgets.get(hunk_index)

    def _should_render_hunk_header(
        self,
        hunk_index: int,
        window_start: int,
        window_end: int,
    ) -> bool:
        if not (0 <= hunk_index < len(self._hunk_line_ranges)):
            return False

        _, hunk_start, hunk_end = self._hunk_line_ranges[hunk_index]
        if hunk_end < window_start or hunk_start > window_end:
            return False
        return window_start <= hunk_start <= window_end

    def _register_line_widget(self, line_index: int, widget: Widget) -> None:
        self._line_widgets_by_index[line_index] = widget

    def _register_row_anchor_widget(self, anchor_id: str, widget: Widget) -> None:
        self._row_anchor_widgets[anchor_id] = widget

    def _unregister_line_widgets(self, line_index: int) -> None:
        self._line_widgets_by_index.pop(line_index, None)
        self._unified_blocks_by_line.pop(line_index, None)
        self._split_blocks_by_line.pop(line_index, None)

        if not (0 <= line_index < len(self._all_lines)):
            return

        line = self._all_lines[line_index]
        anchor_ids = [f"line-{line_index}"]
        if not self.split and line.is_modified:
            anchor_ids.extend([f"line-{line_index}-old", f"line-{line_index}-new"])

        for anchor_id in anchor_ids:
            self._row_anchor_widgets.pop(anchor_id, None)

    def _register_hunk_header_widget(self, hunk_index: int, widget: Static) -> None:
        self._hunk_header_widgets[hunk_index] = widget

    def _register_code_widgets(self, line_index: int, *widgets: Static) -> None:
        self._code_widgets_by_line[line_index] = tuple(widgets)

    def _get_code_widgets(self, line_index: int) -> tuple[Static, ...]:
        return self._code_widgets_by_line.get(line_index, ())

    @staticmethod
    def _merge_line_ranges(
        ranges: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        if not ranges:
            return []

        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if not merged:
                merged.append((start, end))
                continue

            prev_start, prev_end = merged[-1]
            if start <= prev_end + 1:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))

        return merged

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
        await _virtual._clear_virtual_hunk_headers(self)
        await _virtual._sync_virtual_buffers(self, container, new_start, new_end)
        _virtual._mount_virtualized_lines_at_bottom(self, container, new_start, new_end)
        await _virtual._sync_visible_virtual_hunk_headers(
            self, container, new_start, new_end
        )

    def _is_current_render_request(self, request_token: int) -> bool:
        return request_token == self._render_request_token

    def _finalize_render_state_if_current(self, request_token: int) -> None:
        if not self._is_current_render_request(request_token):
            return
        self._finalize_render_state()

    async def _run_render_diff_for_request(self, request_token: int) -> None:
        token = _RENDER_REQUEST_CONTEXT.set(request_token)
        try:
            await self._render_diff()
        finally:
            _RENDER_REQUEST_CONTEXT.reset(token)

    async def show_diff(self, filename: str, diff: FileDiff) -> None:
        self._render_request_token += 1
        request_token = self._render_request_token
        self._suspend_split_state_rerender = True
        self._suspend_scroll_virtual_window_watch = True
        try:
            self.current_file = filename
            self._diff = diff
            self.current_hunk_index = 0

            self._all_lines = []
            self._rows_unified = []
            self._rows_split = []
            self._row_lookup_unified = {}
            self._row_lookup_split = {}
            _search.clear_state(self)
            _comments.clear_state(self)
            self._line_index_by_new_number = {}
            self._line_index_by_old_number = {}
            self._hunk_index_by_line = []
            self._modified_line_count = 0
            self._total_line_render_height = 0
            self._hunk_line_ranges = []
            self._hunk_header_top_offsets = []
            self._line_top_offsets = []
            self._line_heights = []
            self._line_bottom_offsets = []
            self._virtual_content_height = 0
            self._virtualized = False
            self._virtual_window_start = 0
            self._virtual_window_end = -1
            self._rendered_window_start = 0
            self._rendered_window_end = -1
            self._coalesced_scroll_center_line = None
            self._window_render_pending = False
            self._window_highlight_inflight = None
            self._queued_window_highlight = None
            self._queued_full_highlight = None
            self._unified_block_static_rows_by_line.clear()
            self._split_block_static_rows_by_line.clear()
            self._base_code_content_cache.clear()
            self._code_widgets_by_line = {}
            self._unified_blocks_by_line = {}
            self._split_blocks_by_line = {}
            self._line_widgets_by_index = {}
            self._row_anchor_widgets = {}
            self._hunk_header_widgets = {}
            self._top_virtual_buffer_widget = None
            self._bottom_virtual_buffer_widget = None
            self._suspend_active_pane_watch = False
            self._cursor_ui_flush_pending = False
            self._queued_cursor_dirty_lines.clear()
            self._queued_selection_dirty_lines.clear()
            self._queued_selection_full_refresh = False
            self._queued_sync_search_match = False
            self._queued_update_status_line = False
            self._visual_selection_specs = {}

            self._exit_visual_mode()
            self.visual_type = "char"

            self.active_pane = "new"
            self.cursor_line = 0
            self.cursor_column = 0

            if self.store:
                self._file = next(
                    (f for f in self.store.state.files if f.filename == filename),
                    None,
                )
            else:
                self._file = None

            line_index = 0
            for hunk_index, hunk in enumerate(diff.hunks):
                hunk_start = line_index
                for line in hunk.lines:
                    line.line_index = line_index
                    self._all_lines.append(line)
                    self._hunk_index_by_line.append(hunk_index)
                    if line.is_modified:
                        self._modified_line_count += 1

                    if line.new_line_no is not None:
                        self._line_index_by_new_number.setdefault(
                            line.new_line_no, line_index
                        )
                    if line.old_line_no is not None:
                        self._line_index_by_old_number.setdefault(
                            line.old_line_no, line_index
                        )

                    line_index += 1

                hunk_end = line_index - 1
                self._hunk_line_ranges.append((hunk_index, hunk_start, hunk_end))

            _comments.build_comment_map(self)
            self._update_split_state()
            self._rebuild_rendered_rows()
            _virtual._rebuild_virtual_layout(self)
            _virtual._configure_virtual_window(self)

            # Render cache hit immediately; otherwise show plain text first and
            # finish syntax highlighting in the background. Large virtualized diffs
            # highlight only the visible window after the first paint.
            if _hl._has_highlighted_diff(self, diff):
                _hl._highlight_diff_sync(self, diff)
            elif _hl._use_windowed_highlight_strategy(self, diff):
                _hl._clear_highlighted_content(self, diff)
            else:
                _hl._clear_highlighted_content(self, diff)
                _hl._queue_highlight_diff(self, filename, diff)

            await self._run_render_diff_for_request(request_token)
        finally:
            self._suspend_split_state_rerender = False
            self._suspend_scroll_virtual_window_watch = False

    async def _render_diff(self) -> None:
        request_token = _RENDER_REQUEST_CONTEXT.get()
        if request_token is not None and not self._is_current_render_request(
            request_token
        ):
            return
        header = self._header_widget
        if header is None:
            header = self.query_one("#diff-header", Static)
            self._header_widget = header
        if self._file:
            header_text = (
                f"{self.current_file}  "
                f"[green]+{self._file.additions}[/] "
                f"[red]-{self._file.deletions}[/]"
            )
            header.update(header_text)
        else:
            header.update(self.current_file or "No file selected")

        new_content = self._content_widget
        if new_content is None:
            new_content = self.query_one("#diff-content", VerticalScroll)
            self._content_widget = new_content
        await new_content.remove_children()
        if request_token is not None and not self._is_current_render_request(
            request_token
        ):
            return

        self._code_widgets_by_line = {}
        self._unified_blocks_by_line = {}
        self._split_blocks_by_line = {}
        self._line_widgets_by_index = {}
        self._row_anchor_widgets = {}
        self._hunk_header_widgets = {}
        self._top_virtual_buffer_widget = None
        self._bottom_virtual_buffer_widget = None
        self._suspend_active_pane_watch = False
        self._visual_selection_specs = {}

        if not self._diff or not self._diff.hunks:
            new_content.mount(Static("No changes in this file", classes="placeholder"))
        elif self._virtualized:
            _virtual._render_virtual_window(self, new_content)
        else:
            for hunk_index, hunk in enumerate(self._diff.hunks):
                self._render_hunk(new_content, hunk, hunk_index=hunk_index)

        if self._virtualized:
            self._rendered_window_start = self._virtual_window_start
            self._rendered_window_end = self._virtual_window_end
        else:
            self._rendered_window_start = 0
            self._rendered_window_end = len(self._all_lines) - 1

        if request_token is not None:
            self.call_after_refresh(
                lambda: self._finalize_render_state_if_current(request_token)
            )
        else:
            self.call_after_refresh(self._finalize_render_state)

    def _render_hunk(
        self,
        container: VerticalScroll,
        hunk: DiffHunk,
        *,
        hunk_index: int,
        window_start: int | None = None,
        window_end: int | None = None,
        show_header: bool = True,
    ) -> None:
        if window_start is not None and window_end is not None:
            lines = [
                line
                for line in hunk.lines
                if window_start <= line.line_index <= window_end
            ]
        else:
            lines = hunk.lines

        if not lines:
            return

        if show_header:
            hunk_header = (
                f"@@ -{hunk.old_start},{hunk.old_count} "
                f"+{hunk.new_start},{hunk.new_count} @@"
            )
            if hunk.header:
                hunk_header += f" {hunk.header}"
            hunk_header_widget = Static(
                hunk_header,
                classes="hunk-header",
                id=f"hunk-{hunk_index}",
            )
            container.mount(hunk_header_widget)
            self._register_hunk_header_widget(hunk_index, hunk_header_widget)

        if self.split:
            self._render_hunk_split(container, lines)
        else:
            self._render_hunk_unified(container, lines)

    def _build_unified_prefix_content(self, line: DiffLine) -> Content:
        prefix_parts: list[Content] = []

        if self.show_line_numbers:
            old_no = str(line.old_line_no) if line.old_line_no else ""
            new_no = str(line.new_line_no) if line.new_line_no else ""
            prefix_parts.append(Content.styled(f"{old_no:>4} ", "$text-disabled"))
            prefix_parts.append(Content.styled(f"{new_no:>4} ", "$text-disabled"))

        prefix = " "
        if line.is_added:
            prefix = "+"
        elif line.is_deleted:
            prefix = "-"

        prefix_parts.append(Content(prefix + " "))
        return Content("").join(prefix_parts)

    def _unified_line_style(
        self,
        line: DiffLine,
        *,
        side: Literal["old", "new", "auto"] = "auto",
    ) -> str:
        if side == "old" and line.is_modified:
            return "on $error 10%"
        if side == "new" and line.is_modified:
            return "on $success 10%"
        if line.is_added:
            return "on $success 10%"
        if line.is_deleted:
            return "on $error 10%"
        return ""

    def _split_line_style(
        self,
        line: DiffLine,
        *,
        side: Literal["old", "new"],
    ) -> str:
        if side == "old" and (line.is_deleted or line.is_modified):
            return "on $error 10%"
        if side == "new" and (line.is_added or line.is_modified):
            return "on $success 10%"
        return ""

    def _mount_split_lines(
        self,
        container: VerticalScroll,
        lines: list[DiffLine],
        *,
        before: Widget | None = None,
    ) -> None:
        if not _blocks._should_use_split_block_renderer(self):
            for line in lines:
                widget = self._render_line_split(line)
                if before is not None:
                    container.mount(widget, before=before)
                else:
                    container.mount(widget)
                _comments.mount_comments_for_line(
                    self, container, line.line_index, before=before
                )
            return

        chunk_limit = _blocks._block_chunk_limit(self)
        block_lines: list[DiffLine] = []
        for line in lines:
            if _blocks._can_render_in_split_block(self, line):
                block_lines.append(line)
                if chunk_limit is not None and len(block_lines) >= chunk_limit:
                    _blocks._render_split_line_block(
                        self, container, block_lines, before=before
                    )
                    block_lines = []
                continue

            if block_lines:
                _blocks._render_split_line_block(
                    self, container, block_lines, before=before
                )
                block_lines = []

            widget = self._render_line_split(line)
            if before is not None:
                container.mount(widget, before=before)
            else:
                container.mount(widget)
            _comments.mount_comments_for_line(
                self, container, line.line_index, before=before
            )

        if block_lines:
            _blocks._render_split_line_block(
                self, container, block_lines, before=before
            )

    def _mount_unified_lines(
        self,
        container: VerticalScroll,
        lines: list[DiffLine],
        *,
        before: Widget | None = None,
    ) -> None:
        if not _blocks._should_use_unified_block_renderer(self):
            for line in lines:
                widget = self._render_line_unified(line)
                if before is not None:
                    container.mount(widget, before=before)
                else:
                    container.mount(widget)
                _comments.mount_comments_for_line(
                    self, container, line.line_index, before=before
                )
            return

        chunk_limit = _blocks._block_chunk_limit(self)
        block_lines: list[DiffLine] = []
        for line in lines:
            if _blocks._can_render_in_unified_block(self, line):
                block_lines.append(line)
                if chunk_limit is not None and len(block_lines) >= chunk_limit:
                    _blocks._render_unified_line_block(
                        self, container, block_lines, before=before
                    )
                    block_lines = []
                continue

            if block_lines:
                _blocks._render_unified_line_block(
                    self, container, block_lines, before=before
                )
                block_lines = []

            widget = self._render_line_unified(line)
            if before is not None:
                container.mount(widget, before=before)
            else:
                container.mount(widget)
            _comments.mount_comments_for_line(
                self, container, line.line_index, before=before
            )

        if block_lines:
            _blocks._render_unified_line_block(
                self, container, block_lines, before=before
            )

    def _render_hunk_unified(
        self,
        container: VerticalScroll,
        lines: list[DiffLine],
    ) -> None:
        self._mount_unified_lines(container, lines)

    def _render_hunk_split(
        self,
        container: VerticalScroll,
        lines: list[DiffLine],
    ) -> None:
        self._mount_split_lines(container, lines)

    def _get_hunk_index_for_line(self, line_index: int) -> int | None:
        if 0 <= line_index < len(self._hunk_index_by_line):
            return self._hunk_index_by_line[line_index]
        return None

    def _update_status_line(self) -> None:
        return

    def _finalize_render_state(self) -> None:
        self._show_initial_cursor()
        if self.visual_mode:
            self._update_selection_highlighting({self.cursor_line})
        self._update_status_line()
        _hl._ensure_visible_highlight(self)

        # Execute pending comment jump (set by cross-file navigation).
        pending = self._pending_comment_jump
        if pending is not None:
            self._pending_comment_jump = None
            indices = self._comment_line_indices
            if indices:
                line = indices[0] if pending == "first" else indices[-1]
                _comments._jump_to_comment_line(self, line)
            else:
                # File has threads but no visible comment lines → skip onward.
                direction = 1 if pending == "first" else -1
                self.post_message(self.CrossFileComment(direction=direction))

    def _render_line_unified(self, line: DiffLine) -> Horizontal | Vertical:
        if line.is_modified:
            return self._render_modified_line(line)

        prefix_content = self._build_unified_prefix_content(line)

        if line.is_added and line.highlighted_new_content:
            code_content = line.highlighted_new_content
        elif line.is_deleted and line.highlighted_old_content:
            code_content = line.highlighted_old_content
        elif line.highlighted_old_content:
            code_content = line.highlighted_old_content
        else:
            content_text = line.new_content if line.is_added else line.old_content
            code_content = Content(content_text)

        code_classes = "code-content"
        if line.is_added:
            code_classes += " -added"
        elif line.is_deleted:
            code_classes += " -removed"

        prefix_widget = Static(prefix_content, classes="line-prefix")
        code_widget = Static(code_content, classes=code_classes)

        container = Horizontal(
            prefix_widget,
            code_widget,
            classes="diff-line",
            id=f"line-{line.line_index}",
        )

        self._register_line_widget(line.line_index, container)
        self._register_row_anchor_widget(f"line-{line.line_index}", container)
        self._register_code_widgets(line.line_index, code_widget)
        return container

    def _build_split_prefix(
        self,
        line_no: int | None,
        prefix: str,
        *,
        line_index: int,
    ) -> Content:
        parts: list[Content] = []

        if self.show_line_numbers:
            line_text = str(line_no) if line_no is not None else ""
            parts.append(Content.styled(f"{line_text:>4} ", "$text-disabled"))

        parts.append(Content(prefix + " "))

        return Content("").join(parts)

    def _render_line_split(self, line: DiffLine) -> Horizontal:
        if line.is_deleted or line.is_modified:
            left_prefix = self._build_split_prefix(
                line.old_line_no,
                "-" if line.is_deleted or line.is_modified else " ",
                line_index=line.line_index,
            )
            left_content = (
                line.highlighted_old_content
                if line.highlighted_old_content
                else Content(line.old_content)
            )
            left_classes = "code-content -old-side"
            if line.is_deleted or line.is_modified:
                left_classes += " -removed"
        else:
            left_prefix = self._build_split_prefix(
                line.old_line_no,
                " ",
                line_index=line.line_index,
            )
            left_content = line.highlighted_old_content or Content(line.old_content)
            left_classes = "code-content -old-side"

        if line.is_added or line.is_modified:
            right_prefix = self._build_split_prefix(
                line.new_line_no,
                "+" if line.is_added or line.is_modified else " ",
                line_index=line.line_index,
            )
            right_content = (
                line.highlighted_new_content
                if line.highlighted_new_content
                else Content(line.new_content)
            )
            right_classes = "code-content -new-side"
            if line.is_added or line.is_modified:
                right_classes += " -added"
        else:
            right_prefix = self._build_split_prefix(
                line.new_line_no,
                " ",
                line_index=line.line_index,
            )
            right_content = line.highlighted_new_content or Content(line.new_content)
            right_classes = "code-content -new-side"

        if line.is_added:
            left_content = Content(" ")
            left_classes += " -placeholder"
        if line.is_deleted:
            right_content = Content(" ")
            right_classes += " -placeholder"

        left_prefix_widget = Static(left_prefix, classes="line-prefix")
        left_code_widget = Static(left_content, classes=left_classes)
        left_row = Horizontal(
            left_prefix_widget,
            left_code_widget,
            classes="split-pane split-pane-left",
            id=f"line-{line.line_index}-old",
        )

        right_prefix_widget = Static(right_prefix, classes="line-prefix")
        right_code_widget = Static(right_content, classes=right_classes)
        right_row = Horizontal(
            right_prefix_widget,
            right_code_widget,
            classes="split-pane split-pane-right",
            id=f"line-{line.line_index}-new",
        )

        self._register_code_widgets(
            line.line_index, left_code_widget, right_code_widget
        )
        container = Horizontal(
            left_row,
            right_row,
            classes="diff-line split-container",
            id=f"line-{line.line_index}",
        )
        self._register_line_widget(line.line_index, container)
        self._register_row_anchor_widget(f"line-{line.line_index}", container)
        return container

    def _render_modified_line(self, line: DiffLine) -> Vertical:
        old_prefix_parts: list[Content] = []
        if self.show_line_numbers:
            old_prefix_parts.append(
                Content.styled(f"{line.old_line_no:>4} ", "$text-disabled")
            )
            old_prefix_parts.append(
                Content.styled("     ", "$text-disabled")
            )  # Empty new line number
        old_prefix_parts.append(Content("- "))
        old_prefix_content = Content("").join(old_prefix_parts)

        old_code_content = (
            line.highlighted_old_content
            if line.highlighted_old_content
            else Content(line.old_content)
        )

        old_prefix_widget = Static(old_prefix_content, classes="line-prefix")
        old_code_widget = Static(old_code_content, classes="code-content -removed")
        old_horizontal = Horizontal(
            old_prefix_widget,
            old_code_widget,
            classes="diff-line",
            id=f"line-{line.line_index}-old",
        )

        new_prefix_parts: list[Content] = []
        if self.show_line_numbers:
            new_prefix_parts.append(
                Content.styled("     ", "$text-disabled")
            )  # Empty old line number
            new_prefix_parts.append(
                Content.styled(f"{line.new_line_no:>4} ", "$text-disabled")
            )
        new_prefix_parts.append(Content("+ "))
        new_prefix_content = Content("").join(new_prefix_parts)

        new_code_content = (
            line.highlighted_new_content
            if line.highlighted_new_content
            else Content(line.new_content)
        )

        new_prefix_widget = Static(new_prefix_content, classes="line-prefix")
        new_code_widget = Static(new_code_content, classes="code-content -added")
        new_horizontal = Horizontal(
            new_prefix_widget,
            new_code_widget,
            classes="diff-line",
            id=f"line-{line.line_index}-new",
        )

        self._register_code_widgets(line.line_index, old_code_widget, new_code_widget)

        container = Vertical(
            old_horizontal,
            new_horizontal,
            classes="diff-line",
            id=f"line-{line.line_index}",
        )
        self._register_line_widget(line.line_index, container)
        self._register_row_anchor_widget(f"line-{line.line_index}-old", old_horizontal)
        self._register_row_anchor_widget(f"line-{line.line_index}-new", new_horizontal)
        return container

    def _compute_selection_spec_for_line(
        self,
        line_idx: int,
    ) -> tuple[int, int | None, Literal["char", "line"]] | None:
        if not self.visual_mode or self.visual_anchor_line is None:
            return None
        if not self._all_lines or not self._is_line_rendered(line_idx):
            return None

        start_line = min(self.visual_anchor_line, self.cursor_line)
        end_line = max(self.visual_anchor_line, self.cursor_line)
        if not (start_line <= line_idx <= end_line):
            return None

        if self.visual_type == "line":
            return (0, None, "line")

        start_col = (
            self.visual_anchor_column if self.visual_anchor_column is not None else 0
        )
        end_col = self.cursor_column

        if self.visual_anchor_line < self.cursor_line:
            first_line_col = start_col
            last_line_col = end_col
        elif self.visual_anchor_line > self.cursor_line:
            first_line_col = end_col
            last_line_col = start_col
        else:
            first_line_col = min(start_col, end_col)
            last_line_col = max(start_col, end_col)

        if start_line == end_line:
            return (first_line_col, last_line_col, "char")
        if line_idx == start_line:
            return (first_line_col, None, "char")
        if line_idx == end_line:
            return (0, last_line_col, "char")
        return (0, None, "char")

    def _compute_visible_selection_specs(
        self,
    ) -> dict[int, tuple[int, int | None, Literal["char", "line"]]]:
        if not self.visual_mode or self.visual_anchor_line is None:
            return {}

        if not self._all_lines:
            return {}

        start_line = min(self.visual_anchor_line, self.cursor_line)
        end_line = max(self.visual_anchor_line, self.cursor_line)

        rendered_start, rendered_end = self._get_rendered_line_bounds()
        visible_start = max(start_line, rendered_start)
        visible_end = min(end_line, rendered_end)

        if visible_start > visible_end:
            return {}

        specs: dict[int, tuple[int, int | None, Literal["char", "line"]]] = {}
        for line_idx in range(visible_start, visible_end + 1):
            spec = self._compute_selection_spec_for_line(line_idx)
            if spec is not None:
                specs[line_idx] = spec

        return specs

    def _update_selection_highlighting(
        self, dirty_lines: set[int] | None = None
    ) -> None:
        if not self.visual_mode or self.visual_anchor_line is None:
            return

        if not self._all_lines or not self.is_mounted:
            return

        old_specs = self._visual_selection_specs

        incremental = bool(dirty_lines) and bool(old_specs)
        if incremental:
            new_specs = dict(old_specs)
            candidate_lines = set(dirty_lines or ())
            for line_idx in candidate_lines:
                spec = self._compute_selection_spec_for_line(line_idx)
                if spec is None:
                    new_specs.pop(line_idx, None)
                else:
                    new_specs[line_idx] = spec
        else:
            new_specs = self._compute_visible_selection_specs()

        lines_to_clear = set(old_specs) - set(new_specs)
        lines_to_apply = {
            line_idx
            for line_idx, spec in new_specs.items()
            if old_specs.get(line_idx) != spec
        }

        if dirty_lines:
            for line_idx in dirty_lines:
                if line_idx in new_specs:
                    lines_to_apply.add(line_idx)
                elif line_idx in old_specs:
                    lines_to_clear.add(line_idx)

        for line_idx in sorted(lines_to_clear):
            self._clear_line_selection(line_idx)

        for line_idx in sorted(lines_to_apply):
            sel_start, sel_end, _ = new_specs[line_idx]
            self._apply_line_selection(line_idx, sel_start, sel_end)

        self._visual_selection_specs = new_specs

    def _clear_line_selection(self, line_idx: int) -> None:
        if line_idx < 0 or line_idx >= len(self._all_lines):
            return

        if _blocks._refresh_grouped_blocks_for_lines(self, {line_idx}):
            return

        code_widgets = self._get_code_widgets(line_idx)
        if not code_widgets:
            return

        for widget in code_widgets:
            if widget.has_class("-placeholder"):
                continue

            widget.remove_class("-selected")
            widget.remove_class("-anchor")

            line = self._all_lines[line_idx]
            side = self._get_line_side_for_widget(line, widget)
            has_cursor = (
                line_idx == self.cursor_line
                and self._widget_matches_cursor_side(line, widget)
            )
            if has_cursor:
                cursor_col = self.cursor_column
                new_content = self._build_code_content_with_cursor(
                    line,
                    True,
                    cursor_col,
                    side=side,
                )
            else:
                new_content = self._base_code_content(line, side=side)
            widget.update(new_content)

    def _apply_line_selection(
        self, line_idx: int, start_col: int, end_col: int | None
    ) -> None:
        if line_idx < 0 or line_idx >= len(self._all_lines):
            return

        if _blocks._refresh_grouped_blocks_for_lines(self, {line_idx}):
            return

        line = self._all_lines[line_idx]
        text = self._get_line_text(line)

        code_widgets = self._get_code_widgets(line_idx)
        if not code_widgets:
            return

        for widget in code_widgets:
            if widget.has_class("-placeholder"):
                continue

            actual_end = end_col if end_col is not None else len(text) - 1
            side = self._get_line_side_for_widget(line, widget)
            has_cursor = (
                line_idx == self.cursor_line
                and self._widget_matches_cursor_side(line, widget)
            )
            cursor_col = self.cursor_column if has_cursor else None
            content = self._build_code_content_with_selection(
                line,
                has_cursor,
                cursor_col,
                start_col,
                actual_end,
                side=side,
            )
            widget.update(content)

            if self.visual_type == "line":
                widget.add_class("-selected")
                if line_idx == self.visual_anchor_line:
                    widget.add_class("-anchor")
                else:
                    widget.remove_class("-anchor")
            else:
                widget.remove_class("-selected")
                widget.remove_class("-anchor")

    def _compute_base_code_content(
        self,
        line: DiffLine,
        *,
        side: Literal["old", "new", "auto"] = "auto",
        empty_fallback: str = "",
    ) -> Content:
        if side == "old":
            if line.highlighted_old_content is not None:
                return line.highlighted_old_content
            return Content(line.old_content if line.old_content else empty_fallback)
        if side == "new":
            if line.highlighted_new_content is not None:
                return line.highlighted_new_content
            return Content(line.new_content if line.new_content else empty_fallback)
        if line.highlighted_new_content is not None:
            return line.highlighted_new_content
        if line.highlighted_old_content is not None:
            return line.highlighted_old_content

        text_content = self._get_line_text(line, side)
        return Content(text_content if text_content else empty_fallback)

    def _base_code_content(
        self,
        line: DiffLine,
        *,
        side: Literal["old", "new", "auto"] = "auto",
        empty_fallback: str = "",
    ) -> Content:
        line_index = line.line_index
        if line_index < 0:
            return self._compute_base_code_content(
                line,
                side=side,
                empty_fallback=empty_fallback,
            )

        cache_key = (line_index, side, empty_fallback)
        cached = self._base_code_content_cache.get(cache_key)
        if cached is not None:
            return cached

        cached = self._compute_base_code_content(
            line,
            side=side,
            empty_fallback=empty_fallback,
        )
        self._base_code_content_cache[cache_key] = cached
        return cached

    def _build_code_content_with_selection(
        self,
        line: DiffLine,
        has_cursor: bool,
        cursor_col: int | None,
        sel_start: int,
        sel_end: int,
        *,
        side: Literal["old", "new", "auto"] = "auto",
    ) -> Content:
        base_content = self._base_code_content(line, side=side)
        base_content = _search.apply_search_highlights(
            self,
            base_content,
            line.line_index,
            side,
        )
        text_content = self._get_line_text(line, side)
        if not text_content:
            return base_content

        sel_start = max(0, min(sel_start, len(text_content) - 1))
        sel_end = max(0, min(sel_end, len(text_content) - 1))

        if sel_start > sel_end:
            sel_start, sel_end = sel_end, sel_start

        result = base_content.stylize("reverse dim", sel_start, sel_end + 1)

        if has_cursor and cursor_col is not None and cursor_col < len(text_content):
            result = result.stylize("reverse bold", cursor_col, cursor_col + 1)

        return result

    def _scroll_to_cursor(self) -> None:
        if not self._all_lines or not self.is_mounted:
            return

        row = self._current_row()
        if row is None:
            return

        bounds = self._row_vertical_bounds(row)
        if bounds is None:
            return

        top, bottom = bounds
        saved = self._suspend_scroll_virtual_window_watch
        self._suspend_scroll_virtual_window_watch = True
        try:
            self._scroll_to_vertical_span(top, bottom, animate=False)
        finally:
            self._suspend_scroll_virtual_window_watch = saved

    def _show_initial_cursor(self) -> None:
        if not self._all_lines or not self.is_mounted:
            return

        self._update_line_cursor(self.cursor_line)

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

    def action_center_cursor(self) -> None:
        row = self._current_row()
        if row is None:
            return
        bounds = self._row_vertical_bounds(row)
        if bounds is None:
            return
        top, bottom = bounds
        mid = (top + bottom) // 2
        viewport_height = max(1, self.scrollable_content_region.height)
        self.scroll_to(
            y=max(0, mid - viewport_height // 2),
            animate=False,
        )

    _DIFF_MODES: tuple[str, ...] = ("auto", "split", "unified")

    def action_cycle_diff_mode(self) -> None:
        try:
            idx = self._DIFF_MODES.index(self.mode)
        except ValueError:
            idx = 0
        new_mode = self._DIFF_MODES[(idx + 1) % len(self._DIFF_MODES)]
        self.mode = new_mode  # type: ignore[assignment]
        label = {"auto": "Auto", "split": "Split", "unified": "Unified"}[new_mode]
        self.post_message(Flash(f"Diff mode: {label}", style="success", duration=1.5))

    def next_hunk(self) -> None:
        if not self._diff or not self._diff.hunks:
            return

        total = len(self._diff.hunks)
        if self.current_hunk_index < total - 1:
            self.current_hunk_index += 1
            target_row = self._first_row_for_hunk(self.current_hunk_index)
            if target_row is not None:
                self._jump_to_row_with_anchor(target_row, viewport_offset=0)
            else:
                self._scroll_to_hunk(self.current_hunk_index)
            self.post_message(
                self.HunkNavigated(
                    hunk_index=self.current_hunk_index,
                    total_hunks=total,
                )
            )

    def prev_hunk(self) -> None:
        if not self._diff or not self._diff.hunks:
            return

        total = len(self._diff.hunks)
        if self.current_hunk_index > 0:
            self.current_hunk_index -= 1
            target_row = self._first_row_for_hunk(self.current_hunk_index)
            if target_row is not None:
                self._jump_to_row_with_anchor(target_row, viewport_offset=0)
            else:
                self._scroll_to_hunk(self.current_hunk_index)
            self.post_message(
                self.HunkNavigated(
                    hunk_index=self.current_hunk_index,
                    total_hunks=total,
                )
            )

    def _scroll_to_hunk(self, index: int) -> None:
        if self._diff is None or not (0 <= index < len(self._diff.hunks)):
            return

        if self._virtualized:
            target_range = next(
                (item for item in self._hunk_line_ranges if item[0] == index),
                None,
            )
            if target_range is not None:
                _, start, end = target_range
                target_line = start if end >= start else start
                if not (
                    self._virtual_window_start
                    <= target_line
                    <= self._virtual_window_end
                ):
                    _virtual._set_virtual_window_around(self, target_line)
                    self._window_render_pending = True
                    self.run_worker(
                        _virtual._run_virtual_window_render_for_request(
                            self, self._render_request_token
                        ),
                        exclusive=True,
                        name="diff-virtual-hunk-jump",
                    )
                    self.call_after_refresh(lambda: self._scroll_to_hunk(index))
                    return

        if 0 <= index < len(self._hunk_header_top_offsets):
            self._scroll_to_vertical_span(
                self._hunk_header_top_offsets[index],
                self._hunk_header_top_offsets[index] + 1,
                animate=True,
                top_align=True,
            )

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

    def _get_line_text(
        self,
        line: DiffLine,
        side: Literal["old", "new", "auto"] = "auto",
    ) -> str:
        if side == "old":
            return line.old_content
        if side == "new":
            return line.new_content

        if line.new_content:
            return line.new_content
        if line.old_content:
            return line.old_content
        return ""

    def _is_word_char(self, char: str) -> bool:
        return char.isalnum() or char == "_"

    def _find_first_word(self, text: str) -> int:
        pos = 0
        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    def _find_next_word_start(self, text: str, pos: int) -> int | None:
        if pos >= len(text) - 1:
            return None

        current_pos = pos

        if self._is_word_char(text[current_pos]):
            while current_pos < len(text) and self._is_word_char(text[current_pos]):
                current_pos += 1
        elif not text[current_pos].isspace():
            while (
                current_pos < len(text)
                and not text[current_pos].isspace()
                and not self._is_word_char(text[current_pos])
            ):
                current_pos += 1

        while current_pos < len(text) and text[current_pos].isspace():
            current_pos += 1

        return current_pos if current_pos < len(text) else None

    def _find_prev_word_start(self, text: str, pos: int) -> int | None:
        if pos <= 0:
            return None

        current_pos = pos - 1

        while current_pos > 0 and text[current_pos].isspace():
            current_pos -= 1

        if self._is_word_char(text[current_pos]):
            while current_pos > 0 and self._is_word_char(text[current_pos - 1]):
                current_pos -= 1
        else:
            while (
                current_pos > 0
                and not text[current_pos - 1].isspace()
                and not self._is_word_char(text[current_pos - 1])
            ):
                current_pos -= 1

        return current_pos

    def _find_next_word_end(self, text: str, pos: int) -> int | None:
        if pos >= len(text) - 1:
            return None

        current_pos = pos + 1

        while current_pos < len(text) and text[current_pos].isspace():
            current_pos += 1

        if current_pos >= len(text):
            return None

        if self._is_word_char(text[current_pos]):
            while current_pos < len(text) - 1 and self._is_word_char(
                text[current_pos + 1]
            ):
                current_pos += 1
        else:
            while (
                current_pos < len(text) - 1
                and not text[current_pos + 1].isspace()
                and not self._is_word_char(text[current_pos + 1])
            ):
                current_pos += 1

        return current_pos

    def _scroll_to_cursor_horizontal(self) -> None:
        prefix_width = (
            self.LAYOUT.split_prefix_width
            if self.split
            else self.LAYOUT.unified_prefix_width
        )
        cursor_x = prefix_width + self.cursor_column

        viewport_width = self.size.width
        current_scroll = self.scroll_x
        edge_padding = self.LAYOUT.horizontal_scroll_edge_padding
        reveal_padding = self.LAYOUT.horizontal_scroll_reveal_padding

        if cursor_x >= current_scroll + viewport_width - edge_padding:
            self.scroll_x = cursor_x - viewport_width + reveal_padding

        elif cursor_x < current_scroll + prefix_width:
            self.scroll_x = max(0, cursor_x - prefix_width - edge_padding)

    def _widget_matches_cursor_side(self, line: DiffLine, widget: Static) -> bool:
        cursor_side = self._cursor_side_for_line(line)
        widget_side = self._get_line_side_for_widget(line, widget)

        if cursor_side == "auto":
            return True
        return widget_side == cursor_side or widget_side == "auto"

    def _update_line_cursor(self, line_idx: int) -> None:
        if line_idx < 0 or line_idx >= len(self._all_lines):
            return

        if not self.is_mounted:
            return

        if _blocks._refresh_grouped_blocks_for_lines(self, {line_idx}):
            return

        code_widgets = self._get_code_widgets(line_idx)
        if not code_widgets:
            return

        line = self._all_lines[line_idx]
        has_cursor = line_idx == self.cursor_line

        for code_widget in code_widgets:
            if code_widget.has_class("-placeholder"):
                if code_widget.has_class("-cursor"):
                    code_widget.remove_class("-cursor")
                continue

            side = self._get_line_side_for_widget(line, code_widget)
            show_cursor = has_cursor and self._widget_matches_cursor_side(
                line, code_widget
            )
            had_cursor = code_widget.has_class("-cursor")

            has_search = bool(self._search_query and self._search_matches)

            if not show_cursor and not had_cursor and not has_search:
                continue

            new_content = self._build_code_content_with_cursor(
                line,
                show_cursor,
                self.cursor_column if show_cursor else None,
                side=side,
            )
            code_widget.update(new_content)

            if show_cursor:
                if not had_cursor:
                    code_widget.add_class("-cursor")
            elif had_cursor:
                code_widget.remove_class("-cursor")

    def _build_code_content_with_cursor(
        self,
        line: DiffLine,
        has_cursor: bool,
        cursor_col: int | None,
        *,
        side: Literal["old", "new", "auto"] = "auto",
    ) -> Content:
        base_content = self._base_code_content(line, side=side, empty_fallback=" ")
        base_content = _search.apply_search_highlights(
            self,
            base_content,
            line.line_index,
            side,
        )

        if not has_cursor or cursor_col is None:
            return base_content

        text_content = self._get_line_text(line, side)
        if not text_content:
            return Content(" ").stylize("reverse", 0, 1)

        if cursor_col >= len(text_content):
            return base_content

        result = base_content.stylize("reverse", cursor_col, cursor_col + 1)

        return result
