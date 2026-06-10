from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive, var
from textual.widget import Widget
from textual.widgets import Input, Static, TextArea

from rit.ui.widgets.comment_editor import InlineCommentEditor

from rit.core.types import DiffLine, FileDiff
from rit.state.models import PendingReviewComment, PRFile, ReviewThread
from rit.ui.messages import Flash
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_cursor as _cursor
from rit.ui.widgets import diff_highlight as _hl
from rit.ui.widgets import diff_plan as _plan
from rit.ui.widgets import diff_render as _render
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_selection as _selection
from rit.ui.widgets import diff_virtual as _virtual
from rit.ui.widgets.diff_types import (
    DEFAULT_DIFF_LAYOUT,
    CursorUIState,
    DiffSearchMatch,
    HighlightState,
    RenderedRow,
    SplitBlockLineStaticData,
    SplitDiffBlock,
    UnifiedBlockRowStaticData,
    VirtualState,
    UnifiedDiffBlock,
)

if TYPE_CHECKING:
    from rit.state.store import PRStore


@dataclass(frozen=True)
class _FullFileRestorePosition:
    line: int
    column: int
    cursor_pane: Literal["old", "new"]
    active_pane: Literal["old", "new"]
    viewport_offset: int | None


_RENDER_REQUEST_CONTEXT: ContextVar[int | None] = ContextVar(
    "diff_view_render_request", default=None
)


class DiffView(VerticalScroll):
    can_focus = True
    LAYOUT = DEFAULT_DIFF_LAYOUT
    PREVIEW_PREFIX_WIDTH = _render.PREVIEW_PREFIX_WIDTH

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
    INLINE_COMMENT_EDITOR_HEIGHT = 8

    DEFAULT_CSS = Path(__file__).with_suffix(".tcss").read_text()

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
        Binding("}", "next_comment", "Next Comment", show=False),
        Binding("{", "prev_comment", "Prev Comment", show=False),
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

        self._search_query: str = ""
        self._search_matches: list[DiffSearchMatch] = []
        self._search_match_index: int = -1
        self._prev_search_match_lines: set[int] = set()

        self._hl_state = HighlightState()
        self._unified_block_static_rows_by_line: dict[
            int, tuple[UnifiedBlockRowStaticData, ...]
        ] = {}
        self._split_block_static_rows_by_line: dict[int, SplitBlockLineStaticData] = {}
        self._base_code_content_cache: dict[
            tuple[int, Literal["old", "new", "auto"], str], Content
        ] = {}
        self._line_index_by_new_number: dict[int, int] = {}
        self._line_index_by_old_number: dict[int, int] = {}
        self._line_index_by_file_new_number: dict[tuple[str, int], int] = {}
        self._line_index_by_file_old_number: dict[tuple[str, int], int] = {}
        self._hunk_index_by_line: list[int] = []
        self._modified_line_count: int = 0
        self._total_line_render_height: int = 0

        self._hunk_line_ranges: list[tuple[int, int, int]] = []
        self._hunk_header_top_offsets: list[int] = []
        self._line_top_offsets: list[int] = []
        self._line_heights: list[int] = []
        self._line_bottom_offsets: list[int] = []
        self._virtual_content_height: int = 0

        self._virt = VirtualState()
        self._render_request_token: int = 0
        self._suspend_split_state_rerender: bool = False
        self._suspend_scroll_virtual_window_watch: bool = False
        self._syncing_split_scroll: bool = False
        self._split_horizontal_scroll_x: float = 0.0
        self._unified_code_width: int = 1
        self._split_old_code_width: int = 1
        self._split_new_code_width: int = 1

        self._code_widgets_by_line: dict[int, tuple[Static, ...]] = {}
        self._split_scroll_widgets_by_line: dict[int, tuple[Widget, ...]] = {}
        self._unified_blocks_by_line: dict[int, UnifiedDiffBlock] = {}
        self._split_blocks_by_line: dict[int, SplitDiffBlock] = {}
        self._line_widgets_by_index: dict[int, Widget] = {}
        self._row_anchor_widgets: dict[str, Widget] = {}
        self._hunk_header_widgets: dict[int, Widget] = {}

        self._header_widget: Static | None = None
        self._search_bar_widget: Horizontal | None = None
        self._search_input_widget: Input | None = None

        self._content_widget: VerticalScroll | None = None

        self._center_padding_widget: Static | None = None
        self._center_padding_height: int = 0

        self._showing_full_file: bool = False
        self._saved_diff: FileDiff | None = None
        self._saved_filename: str | None = None
        self._saved_restore_position: _FullFileRestorePosition | None = None

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
        self._pending_comment_widgets_by_line: dict[int, list[Widget]] = {}
        self._pending_comment_layout_widgets_by_line: dict[int, list[Widget]] = {}
        self._inline_comment_editor_line_index: int | None = None
        self._inline_comment_editor_target: (
            tuple[str, int, Literal["LEFT", "RIGHT"]] | None
        ) = None
        self._inline_comment_editor_widget: InlineCommentEditor | None = None
        self._inline_comment_editor_layout_widget: Widget | None = None
        self._inline_comment_editor_initial_body: str = ""
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

    # ------------------------------------------------------------------
    # Watchers
    # ------------------------------------------------------------------

    def watch_mode(self, new_mode: Literal["split", "unified", "auto"]) -> None:
        _render._update_split_state(self)
        _search.refresh_matches(self)
        _cursor._queue_cursor_ui_flush(self, update_status_line=True)

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

    def watch_current_hunk_index(self, _old_index: int, _new_index: int) -> None:
        _cursor._queue_cursor_ui_flush(self, update_status_line=True)

    def on_resize(self) -> None:
        _render._update_split_state(self)
        _search.refresh_matches(self)
        _cursor._queue_cursor_ui_flush(self, update_status_line=True)

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

        _cursor._queue_cursor_ui_flush(
            self,
            cursor_lines={self.cursor_line},
            selection_dirty_lines={self.cursor_line} if self.visual_mode else None,
            sync_search_match=True,
            update_status_line=True,
        )

    def watch_cursor_line(self, old_line: int, new_line: int) -> None:
        if (
            not self._all_lines
            or not self.is_mounted
            or self._cursor_ui.suspend_line_watch
        ):
            return

        _cursor._clamp_cursor_column_to_current_row(self)

        hunk_index = self._get_hunk_index_for_line(new_line)
        if hunk_index is not None and hunk_index != self.current_hunk_index:
            self.current_hunk_index = hunk_index

        _virtual._maybe_update_virtual_window(self, new_line)
        self._comment_cursor_index = 0
        _comments.update_cursor_highlight(self, old_line, new_line)

        if not self.visual_mode:
            _cursor._scroll_to_cursor(self)

        _cursor._queue_cursor_ui_flush(
            self,
            cursor_lines={old_line, new_line},
            selection_dirty_lines={old_line, new_line} if self.visual_mode else None,
            sync_search_match=True,
            update_status_line=True,
        )
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

            if len(text) == 0:
                self.cursor_column = 0
                return

            max_col = len(text) - 1
            if new_col > max_col:
                self.cursor_column = max_col
                return

            _cursor._queue_cursor_ui_flush(
                self,
                cursor_lines={self.cursor_line},
                selection_dirty_lines={self.cursor_line} if self.visual_mode else None,
                sync_search_match=True,
            )
            _cursor._scroll_to_cursor_horizontal(self)

    def watch_visual_mode(self, old_mode: bool, new_mode: bool) -> None:
        if new_mode:
            self.app.sub_title = (
                "-- VISUAL LINE --" if self.visual_type == "line" else "-- VISUAL --"
            )
            _selection._update_selection_highlighting(self, {self.cursor_line})
        else:
            self.app.sub_title = ""
            for line_idx in list(self._visual_selection_specs):
                _selection._clear_line_selection(self, line_idx)
            self._visual_selection_specs = {}

        self._update_status_line()

    def watch_visual_type(
        self, old_type: Literal["char", "line"], new_type: Literal["char", "line"]
    ) -> None:
        if self.visual_mode:
            self.app.sub_title = (
                "-- VISUAL LINE --" if new_type == "line" else "-- VISUAL --"
            )
            _cursor._queue_cursor_ui_flush(
                self,
                selection_dirty_lines={self.cursor_line},
                update_status_line=True,
            )
            return
        _cursor._queue_cursor_ui_flush(self, update_status_line=True)

    def watch_visual_anchor_line(
        self, old_anchor: int | None, new_anchor: int | None
    ) -> None:
        if self.visual_mode:
            _cursor._queue_cursor_ui_flush(
                self, selection_dirty_lines={self.cursor_line}
            )

    def watch_visual_anchor_column(
        self, old_col: int | None, new_col: int | None
    ) -> None:
        if self.visual_mode:
            _cursor._queue_cursor_ui_flush(
                self, selection_dirty_lines={self.cursor_line}
            )

    # ------------------------------------------------------------------
    # Key handling
    # ------------------------------------------------------------------

    _COUNT_MOTION_KEYS = frozenset(
        {"h", "j", "k", "l", "left", "right", "up", "down", "w", "b", "$", "G"}
    )

    def on_key(self, event: events.Key) -> None:
        if self._text_entry_has_focus():
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

        if event.key not in self._COUNT_MOTION_KEYS:
            self._cursor_ui.pending_count = ""

        if event.key == "enter":
            if _comments.try_toggle_current(self):
                event.stop()
                event.prevent_default()
                return

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self._text_entry_has_focus():
            return False
        return super().check_action(action, parameters)

    def _text_entry_has_focus(self) -> bool:
        return isinstance(self.screen.focused, (Input, TextArea))

    def _search_input_has_focus(self) -> bool:
        inp = self._search_input_widget
        return inp is not None and self.screen.focused is inp

    def _close_search(self, *, clear_query: bool) -> None:
        bar = self._search_bar_widget
        if bar is None or not bar.display:
            return
        bar.display = False
        if clear_query:
            _search.clear_state(self)
        _search._refresh_search_display(self)
        self._update_status_line()
        self.focus()

    # ------------------------------------------------------------------
    # Search handlers
    # ------------------------------------------------------------------

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
        if self._search_match_index >= 0:
            _search.reveal_match(self, self._search_match_index)
        self._update_status_line()

    @on(Input.Submitted, "#diff-search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._search_bar_widget is not None:
            self._search_bar_widget.display = False
        self.focus()
        _search.handle_submitted(self, event.value)

    def action_next_search_match(self) -> None:
        _search.jump_match(self, 1)

    def action_prev_search_match(self) -> None:
        _search.jump_match(self, -1)

    # ------------------------------------------------------------------
    # Comment handlers (delegate to _comments)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Shared utility methods (used across modules)
    # ------------------------------------------------------------------

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
        pane = self.cursor_pane if pane is None else pane
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

    def _diff_line_cursor_active(self, line_index: int) -> bool:
        """Return True when the diff-line cursor block should be shown."""
        return line_index == self.cursor_line and self._comment_cursor_index == 0

    def inline_comment_target(
        self,
    ) -> tuple[str, int, Literal["LEFT", "RIGHT"]] | None:
        return self._inline_comment_editor_target

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

    def _inline_comment_editor_height(self) -> int:
        return self.INLINE_COMMENT_EDITOR_HEIGHT

    def _mount_inline_comment_editor(
        self,
        container: VerticalScroll,
        line_index: int,
        *,
        before: Widget | None = None,
    ) -> None:
        if not self.has_inline_comment_editor_for_line(line_index):
            return

        widget = InlineCommentEditor(
            kind="inline",
            title="Add inline comment",
            placeholder="Write a comment for the current line...",
            initial_text=self._inline_comment_editor_initial_body,
            id="diff-inline-comment-editor",
        )
        _, _, target_side = self._inline_comment_editor_target or ("", 0, "RIGHT")
        layout_widget = _comments.mount_side_aware_widget(
            self,
            container,
            widget,
            side="old" if target_side == "LEFT" else "new",
            before=before,
        )
        self._inline_comment_editor_widget = widget
        self._inline_comment_editor_layout_widget = layout_widget

    def _focus_inline_comment_editor(self) -> None:
        if self._inline_comment_editor_widget is not None:
            self._inline_comment_editor_widget.open(
                self._inline_comment_editor_initial_body
            )

    async def open_inline_comment_editor(self) -> bool:
        line = self._current_line()
        target = self._inline_comment_target_for_current_line()
        if line is None or target is None:
            return False

        self._inline_comment_editor_line_index = line.line_index
        self._inline_comment_editor_target = target
        if self.store is not None:
            path, target_line, side = target
            existing = self.store.get_pending_inline_comment(
                path=path,
                line=target_line,
                side=side,
            )
            self._inline_comment_editor_initial_body = existing.body if existing else ""
        else:
            self._inline_comment_editor_initial_body = ""
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

        self._inline_comment_editor_line_index = None
        self._inline_comment_editor_target = None
        self._inline_comment_editor_widget = None
        self._inline_comment_editor_layout_widget = None
        self._inline_comment_editor_initial_body = ""
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

    def _dock_header_height(self) -> int:
        return _cursor._dock_header_height(self)

    def _half_page_step(self) -> int:
        return _cursor._half_page_step(self)

    def _row_vertical_bounds(self, row: RenderedRow) -> tuple[int, int] | None:
        return _cursor._row_vertical_bounds(self, row)

    # ------------------------------------------------------------------
    # Widget registry
    # ------------------------------------------------------------------

    def _get_line_container(self, line_idx: int):
        if not self._is_line_rendered(line_idx):
            return None
        return self._line_widgets_by_index.get(line_idx)

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

    # ------------------------------------------------------------------
    # Render orchestration
    # ------------------------------------------------------------------

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
        _render._finalize_render_state(self)

    async def _run_render_diff_for_request(self, request_token: int) -> None:
        token = _RENDER_REQUEST_CONTEXT.set(request_token)
        try:
            await self._render_diff()
        finally:
            _RENDER_REQUEST_CONTEXT.reset(token)

    # ------------------------------------------------------------------
    # show_diff — main public entry point
    # ------------------------------------------------------------------

    async def show_diff(
        self,
        filename: str,
        diff: FileDiff,
        *,
        preserve_full_file_state: bool = False,
    ) -> None:
        self._render_request_token += 1
        request_token = self._render_request_token
        self._suspend_split_state_rerender = True
        self._suspend_scroll_virtual_window_watch = True
        try:
            is_new_file = filename != self.current_file
            if is_new_file:
                self._inline_comment_editor_line_index = None
                self._inline_comment_editor_target = None
                self._inline_comment_editor_widget = None
                self._inline_comment_editor_layout_widget = None
                self._inline_comment_editor_initial_body = ""
                if not preserve_full_file_state:
                    self._showing_full_file = False
                    self._saved_diff = None
                    self._saved_filename = None

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
            self._line_index_by_file_new_number = {}
            self._line_index_by_file_old_number = {}
            self._hunk_index_by_line = []
            self._modified_line_count = 0
            self._total_line_render_height = 0
            self._hunk_line_ranges = []
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

            if self.store:
                self._file = next(
                    (f for f in self.store.state.files if f.filename == filename),
                    None,
                )
            else:
                self._file = None

            plan = _plan.build_diff_plan(diff)
            self._all_lines = plan.all_lines
            self._line_index_by_new_number = plan.line_index_by_new_number
            self._line_index_by_old_number = plan.line_index_by_old_number
            self._line_index_by_file_new_number = plan.line_index_by_file_new_number
            self._line_index_by_file_old_number = plan.line_index_by_file_old_number
            self._hunk_index_by_line = plan.hunk_index_by_line
            self._modified_line_count = plan.modified_line_count
            self._hunk_line_ranges = plan.hunk_line_ranges
            self._rows_unified = plan.rendered_rows.rows_unified
            self._rows_split = plan.rendered_rows.rows_split
            self._row_lookup_unified = plan.rendered_rows.row_lookup_unified
            self._row_lookup_split = plan.rendered_rows.row_lookup_split

            if not self._showing_full_file:
                _comments.build_comment_map(self)
            (
                self._unified_code_width,
                self._split_old_code_width,
                self._split_new_code_width,
            ) = _render._code_widths_for_layout(self)
            _render._update_split_state(self)
            _virtual._rebuild_virtual_layout(self)
            _virtual._configure_virtual_window(self)

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

    async def prepare(self) -> None:
        if self._diff:
            await asyncio.to_thread(lambda: _render._precompute_diff_data(self))

    def refresh_header(self) -> None:
        """Re-render the diff header badge without re-rendering diff content."""
        self._update_status_line()

    def _reset_current_diff_highlight_state(self) -> bool:
        diff = self._diff
        filename = self.current_file
        if diff is None or filename is None:
            return False

        self._hl_state.request_token += 1
        self._hl_state.queued_window = None
        self._hl_state.queued_full = None
        self._hl_state.cache = {
            cache_key for cache_key in self._hl_state.cache if cache_key[0] != id(diff)
        }
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

    def _update_status_line(self) -> None:
        header = self._header_widget
        if header is None:
            return

        header_text = _render._build_header_text(self)
        query = self._search_query.strip()
        if not query:
            header.update(header_text)
            return

        total_matches = len(self._search_matches)
        active_match = (
            self._search_match_index + 1
            if 0 <= self._search_match_index < total_matches
            else 0
        )
        if total_matches == 0:
            suffix = f'[$warning]search "{query}" no matches[/]'
        else:
            suffix = f'[dim]search "{query}" {active_match}/{total_matches}[/]'
        header.update(f"{header_text}  {suffix}")

    # ------------------------------------------------------------------
    # Full-file toggle
    # ------------------------------------------------------------------

    def action_toggle_full_file(self) -> None:
        if not self.current_file or self.store is None:
            return
        if self._showing_full_file:
            self._restore_diff_view()
        else:
            target_file = self._full_file_preview_target()
            if target_file is None:
                return
            if target_file != self.current_file:
                self.post_message(self.FullFilePreviewRequested(target_file))
                return
            self.run_worker(
                self._load_and_show_full_file(),
                exclusive=True,
                name="diff-full-file",
            )

    def _full_file_preview_target(self) -> str | None:
        line = self._current_line()
        if line is not None and line.file_path:
            return line.file_path
        return self.current_file

    async def _load_and_show_full_file(self) -> None:
        if self.current_file is None or self.store is None:
            return
        content = await self.store.get_file_content(self.current_file)
        if content is None:
            self.post_message(
                Flash("Failed to load file content", style="error", duration=2.0)
            )
            return
        await self.show_full_file_preview(
            self.current_file,
            content,
            source_diff=self._diff,
        )

    async def show_full_file_preview(
        self,
        filename: str,
        content: str,
        *,
        source_diff: FileDiff | None = None,
        restore_filename: str | None = None,
        restore_diff: FileDiff | None = None,
    ) -> None:
        self._saved_filename = restore_filename or self.current_file
        self._saved_diff = restore_diff or self._diff
        self._saved_restore_position = self._full_file_restore_position()
        anchor_line_no = self._full_file_preview_anchor_line_no(
            filename,
            source_diff,
        )
        full_diff = _render._build_full_file_diff(
            filename,
            content,
            source_diff=source_diff,
        )
        self._showing_full_file = True
        await self.show_diff(
            filename,
            full_diff,
            preserve_full_file_state=True,
        )
        self._jump_to_full_file_preview_anchor(anchor_line_no)
        self.post_message(Flash("Full file preview", style="success", duration=1.5))

    def _full_file_preview_anchor_line_no(
        self,
        filename: str,
        source_diff: FileDiff | None,
    ) -> int | None:
        line = self._current_line()
        if line is None:
            return None
        if line.file_path and line.file_path != filename:
            return None
        if line.new_line_no is not None:
            return line.new_line_no
        if line.old_line_no is None:
            return None
        return self._full_file_preview_deleted_anchor_line_no(
            line.old_line_no,
            source_diff,
        )

    def _full_file_preview_deleted_anchor_line_no(
        self,
        old_line_no: int,
        source_diff: FileDiff | None,
    ) -> int | None:
        if source_diff is None:
            return None

        for hunk in source_diff.hunks:
            for index, line in enumerate(hunk.lines):
                if line.old_line_no != old_line_no or not line.is_deleted:
                    continue

                for next_line in hunk.lines[index + 1 :]:
                    if next_line.new_line_no is not None:
                        return next_line.new_line_no

                for previous_line in reversed(hunk.lines[:index]):
                    if previous_line.new_line_no is not None:
                        return previous_line.new_line_no + 1

                return hunk.new_start

        return None

    def _jump_to_full_file_preview_anchor(self, line_no: int | None) -> None:
        if line_no is None or not self._line_index_by_new_number:
            return

        available_lines = sorted(self._line_index_by_new_number)
        target_line_no = min(max(line_no, available_lines[0]), available_lines[-1])
        line_index = self._line_index_by_new_number.get(target_line_no)
        if line_index is None:
            return

        for row in self._rows_for_current_mode():
            if row.line_index == line_index:
                self._jump_to_row_with_anchor(
                    row,
                    pane="new",
                    viewport_offset=2,
                    update_active_pane=True,
                )
                return

        self._move_cursor(line=line_index, pane="new", update_active_pane=True)

    def _full_file_restore_position(self) -> _FullFileRestorePosition | None:
        if not self._all_lines:
            return None
        return _FullFileRestorePosition(
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
        restore_position: _FullFileRestorePosition | None,
    ) -> None:
        await self.show_diff(filename, diff)
        self._restore_full_file_position(restore_position)
        self.post_message(self.FullFilePreviewRestored(filename=filename))
        self.post_message(Flash("Diff view", style="success", duration=1.5))

    def _restore_full_file_position(
        self,
        restore_position: _FullFileRestorePosition | None,
    ) -> None:
        if restore_position is None or not self._all_lines:
            return

        line_index = max(0, min(restore_position.line, len(self._all_lines) - 1))
        target_row = self._row_for_line_and_pane(line_index, restore_position.cursor_pane)
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
        fallback = None
        for row in self._rows_for_current_mode():
            if row.line_index != line_index:
                continue
            if fallback is None:
                fallback = row
            if row.side == "auto" or row.side == pane:
                return row
        return fallback

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
        label = {"auto": "Auto", "split": "Split", "unified": "Unified"}[new_mode]
        self.post_message(Flash(f"Diff mode: {label}", style="success", duration=1.5))

    # ==================================================================
    # Wrapper methods — delegate to extracted modules
    # ==================================================================

    # --- Cursor / scroll / word motion (_cursor) ---

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

    # --- Visual mode / selection (_selection) ---

    def action_toggle_visual(self) -> None:
        _selection._toggle_visual(self)

    def action_toggle_visual_line(self) -> None:
        _selection._toggle_visual_line(self)

    def action_yank(self) -> None:
        _selection._yank(self)

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

    # --- Rendering (_render) ---

    def _update_split_state(self) -> None:
        _render._update_split_state(self)

    def _rebuild_rendered_rows(self) -> None:
        _render._rebuild_rendered_rows(self)

    async def _render_diff(self) -> None:
        await _render._render_diff(self)

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

    def _unified_prefix_width_for_layout(self) -> int:
        return _render._unified_prefix_width_for_layout(self)

    def _build_split_prefix(self, *args, **kwargs) -> Content:
        return _render._build_split_prefix(self, *args, **kwargs)

    def _build_split_prefix_content(self, line: DiffLine, **kwargs) -> Content:
        return _render._build_split_prefix_content(self, line, **kwargs)

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
        self, line_indices: set[int] | None = None
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
