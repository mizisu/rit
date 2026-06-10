from __future__ import annotations

import asyncio
from bisect import bisect_right
from typing import TYPE_CHECKING, Literal, cast

from textual import getters, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Static

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.store import PRStore
from rit.ui.messages import Flash
from rit.ui.widgets import DiffView, FileTree
from rit.ui.widgets.resize_handle import ResizeHandle

if TYPE_CHECKING:
    pass


COMBINED_DIFF_FILENAME = "All files"
COMBINED_FILES_THRESHOLD = 2
COMBINED_DIFF_LOAD_CONCURRENCY = 8


class GhostHandle(Static):
    DEFAULT_CSS = """
    GhostHandle {
        width: 1;
        height: 100%;
        background: $primary 60%;
        display: none;
        dock: left;
    }
    """


class FileChanges(Horizontal):
    """File Changes tab with file tree and diff view."""

    DEFAULT_CSS = """
    FileChanges {
        width: 100%;
        height: 100%;
        layers: base overlay;
    }
    
    GhostHandle {
        layer: overlay;
    }
    
    FileTree, ResizeHandle, DiffView {
        layer: base;
    }
    """

    BINDINGS = [
        Binding(">", "expand_sidebar", "Expand Sidebar", show=False),
        Binding("<", "collapse_sidebar", "Collapse Sidebar", show=False),
        Binding("[", "prev_file", "Prev File", show=False),
        Binding("]", "next_file", "Next File", show=False),
    ]

    sidebar_width = reactive(35)  # Default width

    file_tree = getters.query_one(FileTree)
    diff_view = getters.query_one(DiffView)
    resize_handle = getters.query_one(ResizeHandle)
    ghost_handle = getters.query_one(GhostHandle)

    def __init__(self, store: PRStore) -> None:
        super().__init__()
        self.store = store
        self._drag_delta = 0
        self._is_dragging = False
        self._queued_file_render: tuple[str, FileDiff | None, bool, bool] | None = None
        self._file_render_worker_active = False
        self._queued_combined_render: tuple[tuple[str, ...], bool] | None = None
        self._combined_render_worker_active = False
        self._combined_files_signature: tuple[str, ...] | None = None
        self._combined_file_line_starts: dict[str, int] = {}
        self._combined_file_start_lines: list[int] = []
        self._combined_file_start_names: list[str] = []
        self._combined_line_lookup: dict[
            tuple[str, int, Literal["LEFT", "RIGHT"]], int
        ] = {}
        self._showing_combined_files = False
        self._pending_combined_file_jump: tuple[str, bool] | None = None
        self._pending_location_jump: (
            tuple[str, int, Literal["LEFT", "RIGHT"], bool] | None
        ) = None

    def compose(self) -> ComposeResult:
        yield FileTree(store=self.store, id="file-tree-sidebar")
        yield ResizeHandle(id="resize-handle")
        yield DiffView(store=self.store, id="diff-view-main")
        yield GhostHandle(id="ghost-handle")

    def on_mount(self) -> None:
        self._apply_diff_settings_from_app()
        signal = getattr(self.app, "settings_changed_signal", None)
        if signal is not None:
            signal.subscribe(self, self._on_settings_changed)

    def on_unmount(self) -> None:
        signal = getattr(self.app, "settings_changed_signal", None)
        if signal is not None:
            signal.unsubscribe(self)

    def watch_sidebar_width(self, width: int) -> None:
        try:
            self.file_tree.styles.width = width
        except Exception:
            pass

    def _apply_diff_settings_from_app(self) -> None:
        settings = getattr(self.app, "settings", None)
        if settings is None:
            return

        self._apply_setting("ui.diff_mode", settings.diff_mode)
        self._apply_setting("ui.show_line_numbers", settings.show_line_numbers)
        self._apply_setting("ui.word_diff", settings.word_diff)

    def _on_settings_changed(self, data: tuple[str, object, object | None]) -> None:
        key, value, _old_value = data
        self._apply_setting(key, value)

    def _apply_setting(self, key: str, value: object) -> None:
        if (
            key == "ui.diff_mode"
            and isinstance(value, str)
            and value in {"auto", "split", "unified"}
        ):
            self.diff_view.mode = cast(Literal["auto", "split", "unified"], value)
        elif key == "ui.show_line_numbers" and isinstance(value, bool):
            self.diff_view.show_line_numbers = value
        elif key == "ui.word_diff" and isinstance(value, bool):
            self.diff_view.word_diff_enabled = value
        elif key == "ui.theme" and isinstance(value, str):
            self.diff_view.refresh_syntax_theme()

    def refresh_files(self) -> None:
        self.file_tree.refresh_files()

        state = self.store.state
        if state.files and self._queue_combined_files_render():
            selected_file = state.selected_file or state.files[0].filename
            self.store.state.selected_file = selected_file
            self.file_tree.select_file(selected_file, emit_message=False)
            return

        if state.files and not state.selected_file:
            filename = state.files[0].filename
            self.store.select_file(filename)
            self._queue_file_render_request(
                filename,
                None,
                focus_diff=False,
                sync_tree_selection=True,
            )

    def select_next_file(self) -> None:
        self.file_tree.next_item()

    def select_prev_file(self) -> None:
        self.file_tree.prev_item()

    def next_hunk(self) -> None:
        self.diff_view.next_hunk()

    def prev_hunk(self) -> None:
        self.diff_view.prev_hunk()

    def open_file(self, filename: str, *, focus_diff: bool) -> None:
        """Open a file diff or jump within the combined diff."""
        if not filename:
            return

        if self._jump_to_combined_file(filename, focus_diff=focus_diff):
            self.file_tree.select_file(filename, emit_message=False)
            return

        if self._queue_combined_file_jump(filename, focus_diff=focus_diff):
            return

        if self.diff_view.current_file == filename:
            self.store.state.selected_file = filename
            self.file_tree.select_file(filename, emit_message=False)
            if focus_diff:
                self.diff_view.focus()
            return

        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)
        self._queue_file_render_request(
            filename,
            None,
            focus_diff=focus_diff,
            sync_tree_selection=False,
        )

    def _uses_combined_files(self) -> bool:
        return len(self.store.state.files) >= COMBINED_FILES_THRESHOLD

    def _queue_combined_file_jump(self, filename: str, *, focus_diff: bool) -> bool:
        if not self._uses_combined_files():
            return False

        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)
        self._pending_combined_file_jump = (filename, focus_diff)
        self._queue_combined_files_render(focus_diff=False)
        return True

    def _queue_combined_files_render(
        self,
        *,
        focus_diff: bool = False,
        force: bool = False,
    ) -> bool:
        signature = tuple(file.filename for file in self.store.state.files)
        if len(signature) < COMBINED_FILES_THRESHOLD:
            return False

        if (
            not force
            and self._showing_combined_files
            and self.diff_view.current_file == COMBINED_DIFF_FILENAME
            and self._combined_files_signature == signature
        ):
            return True

        self._queued_combined_render = (signature, focus_diff)
        if self._combined_render_worker_active:
            return True

        self._combined_render_worker_active = True
        self.run_worker(
            self._drain_queued_combined_render_requests(),
            exclusive=False,
            name="combined-diff-render",
        )
        return True

    async def _drain_queued_combined_render_requests(self) -> None:
        while True:
            request = self._queued_combined_render
            if request is None:
                self._combined_render_worker_active = False
                if self._queued_combined_render is None:
                    return
                self._combined_render_worker_active = True
                continue

            self._queued_combined_render = None
            signature, focus_diff = request
            await self._ensure_combined_file_diffs_loaded(signature)
            if self._queued_combined_render is not None:
                continue

            combined = self._build_combined_files_diff(signature)
            if combined is None:
                continue

            self._combined_files_signature = signature
            self._showing_combined_files = True
            self._queue_file_render_request(
                COMBINED_DIFF_FILENAME,
                combined,
                focus_diff=focus_diff,
                sync_tree_selection=False,
            )

    async def _ensure_combined_file_diffs_loaded(
        self,
        signature: tuple[str, ...],
    ) -> None:
        missing = [
            filename
            for filename in signature
            if filename not in self.store.state.file_diffs
        ]
        if not missing:
            return

        semaphore = asyncio.Semaphore(COMBINED_DIFF_LOAD_CONCURRENCY)

        async def load(filename: str) -> None:
            async with semaphore:
                await self.store.get_file_diff_async(filename)

        await asyncio.gather(*(load(filename) for filename in missing))

    def _reset_combined_indexes(self) -> None:
        self._combined_file_line_starts = {}
        self._combined_file_start_lines = []
        self._combined_file_start_names = []
        self._combined_line_lookup = {}

    def _record_combined_file_start(self, filename: str, line_index: int) -> None:
        self._combined_file_line_starts[filename] = line_index
        self._combined_file_start_lines.append(line_index)
        self._combined_file_start_names.append(filename)

    def _record_combined_line_lookup(
        self,
        filename: str,
        line: DiffLine,
        line_index: int,
    ) -> None:
        if line.old_line_no is not None:
            self._combined_line_lookup[
                (filename, line.old_line_no, "LEFT")
            ] = line_index
        if line.new_line_no is not None:
            self._combined_line_lookup[
                (filename, line.new_line_no, "RIGHT")
            ] = line_index

    def _build_combined_files_diff(
        self,
        signature: tuple[str, ...],
    ) -> FileDiff | None:
        state = self.store.state
        if len(signature) < COMBINED_FILES_THRESHOLD:
            return None

        files = [
            state.files_by_filename.get(filename)
            or next(
                (file for file in state.files if file.filename == filename),
                None,
            )
            for filename in signature
        ]
        if any(file is None for file in files):
            return None
        if not all(filename in state.file_diffs for filename in signature):
            return None

        hunks: list[DiffHunk] = []
        self._reset_combined_indexes()
        next_line_index = 0
        is_fully_refined = True

        for file in files:
            if file is None:
                return None
            diff = state.file_diffs[file.filename]
            is_fully_refined = is_fully_refined and diff.is_fully_refined
            file_start_recorded = False

            if not diff.hunks:
                self._record_combined_file_start(file.filename, next_line_index)
                hunks.append(
                    DiffHunk(
                        old_start=0,
                        old_count=0,
                        new_start=0,
                        new_count=0,
                        header="no textual changes",
                        lines=[
                            DiffLine(
                                old_line_no=None,
                                new_line_no=None,
                                old_content="",
                                new_content="No textual changes",
                                file_path=file.filename,
                            )
                        ],
                        starts_file=True,
                        file_path=file.filename,
                        file_old_path=file.previous_filename,
                        file_status=file.status,
                        file_additions=file.additions,
                        file_deletions=file.deletions,
                    )
                )
                next_line_index += 1
                continue

            for hunk in diff.hunks:
                starts_file = not file_start_recorded
                if starts_file:
                    self._record_combined_file_start(file.filename, next_line_index)
                    file_start_recorded = True

                for offset, line in enumerate(hunk.lines):
                    line.file_path = file.filename
                    self._record_combined_line_lookup(
                        file.filename,
                        line,
                        next_line_index + offset,
                    )

                hunks.append(
                    DiffHunk(
                        old_start=hunk.old_start,
                        old_count=hunk.old_count,
                        new_start=hunk.new_start,
                        new_count=hunk.new_count,
                        header=hunk.header,
                        lines=hunk.lines,
                        starts_file=starts_file,
                        file_path=file.filename if starts_file else None,
                        file_old_path=file.previous_filename if starts_file else None,
                        file_status=file.status,
                        file_additions=file.additions,
                        file_deletions=file.deletions,
                    )
                )
                next_line_index += len(hunk.lines)

        return FileDiff(
            filename=COMBINED_DIFF_FILENAME,
            hunks=hunks,
            is_fully_refined=is_fully_refined,
            show_hunk_headers=False,
        )

    def _jump_to_combined_file(self, filename: str, *, focus_diff: bool) -> bool:
        if not self._showing_combined_files:
            return False

        line_index = self._combined_file_line_starts.get(filename)
        if line_index is None:
            return False

        self.store.state.selected_file = filename
        self.diff_view.cursor_line = line_index
        if 0 <= line_index < len(self.diff_view._line_top_offsets):
            self.diff_view.scroll_to(
                y=max(0, self.diff_view._line_top_offsets[line_index] - 2),
                animate=False,
            )
        if focus_diff:
            self.diff_view.focus()
        return True

    def _combined_file_for_line(self, line_index: int) -> str | None:
        if not self._showing_combined_files or not self._combined_file_start_lines:
            return None

        index = bisect_right(self._combined_file_start_lines, line_index) - 1
        if index < 0:
            return None
        return self._combined_file_start_names[index]

    def _sync_combined_selection_for_cursor(self) -> None:
        filename = self._combined_file_for_line(self.diff_view.cursor_line)
        if filename is None:
            return
        if (
            self.store.state.selected_file == filename
            and self.file_tree.selected_file == filename
        ):
            return

        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)

    def jump_to_file_location(
        self,
        filename: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        *,
        focus_diff: bool,
    ) -> bool:
        """Jump to a file/line location, preserving combined-diff navigation."""
        if self._showing_combined_files:
            line_index = self._line_index_for_location(filename, line, side)
            if line_index is None:
                return False
            self.store.state.selected_file = filename
            self.file_tree.select_file(filename, emit_message=False)
            self._jump_to_diff_line(line_index, side=side, focus_diff=focus_diff)
            return True

        if self._uses_combined_files():
            self._pending_location_jump = (filename, line, side, focus_diff)
            self.store.state.selected_file = filename
            self.file_tree.select_file(filename, emit_message=False)
            self._queue_combined_files_render(focus_diff=False)
            return True

        if self.diff_view.current_file == filename:
            line_index = self._line_index_for_location(filename, line, side)
            if line_index is None:
                return False
            self._jump_to_diff_line(line_index, side=side, focus_diff=focus_diff)
            return True

        self._pending_location_jump = (filename, line, side, focus_diff)
        self._queue_file_render_request(
            filename,
            None,
            focus_diff=False,
            sync_tree_selection=True,
        )
        return True

    def _line_index_for_location(
        self,
        filename: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> int | None:
        diff = self.diff_view._diff
        if diff is None:
            return None

        if diff.filename == COMBINED_DIFF_FILENAME:
            cached = self._combined_line_lookup.get((filename, line, side))
            if cached is not None:
                return cached
            current_file = None
        else:
            current_file = diff.filename
            if current_file == filename:
                line_map = (
                    self.diff_view._line_index_by_old_number
                    if side == "LEFT"
                    else self.diff_view._line_index_by_new_number
                )
                cached = line_map.get(line)
                if cached is not None:
                    return cached

        active_file = current_file
        target_attr = "old_line_no" if side == "LEFT" else "new_line_no"
        found_target_file = False

        for hunk in diff.hunks:
            if hunk.starts_file and hunk.file_path:
                if found_target_file and hunk.file_path != filename:
                    break
                active_file = hunk.file_path
            if active_file != filename:
                continue
            found_target_file = True
            for diff_line in hunk.lines:
                if getattr(diff_line, target_attr) == line:
                    return diff_line.line_index

        return None

    def _jump_to_diff_line(
        self,
        line_index: int,
        *,
        side: Literal["LEFT", "RIGHT"],
        focus_diff: bool,
    ) -> None:
        diff_view = self.diff_view
        target_pane: Literal["old", "new"] = "old" if side == "LEFT" else "new"
        target_row = None
        for row in diff_view._rows_for_current_mode():
            if row.line_index != line_index:
                continue
            if row.side == target_pane:
                target_row = row
                break
            if target_row is None:
                target_row = row

        if target_row is not None:
            diff_view._jump_to_row_with_anchor(
                target_row,
                pane=target_pane,
                viewport_offset=2,
            )
        else:
            diff_view.cursor_line = line_index
            if 0 <= line_index < len(diff_view._line_top_offsets):
                diff_view.scroll_to(
                    y=max(0, diff_view._line_top_offsets[line_index] - 2),
                    animate=False,
                )

        if focus_diff:
            diff_view.focus()

    def _apply_pending_combined_file_jump(self) -> bool:
        pending = self._pending_combined_file_jump
        if pending is None:
            return False

        filename, focus_diff = pending
        self._pending_combined_file_jump = None
        self.file_tree.select_file(filename, emit_message=False)
        return self._jump_to_combined_file(filename, focus_diff=focus_diff)

    def _apply_pending_location_jump(self, filename: str) -> bool:
        pending = self._pending_location_jump
        if pending is None:
            return False
        target_filename, line, side, focus_diff = pending
        if filename != COMBINED_DIFF_FILENAME and target_filename != filename:
            return False

        self._pending_location_jump = None
        line_index = self._line_index_for_location(target_filename, line, side)
        if line_index is None:
            return False
        self._jump_to_diff_line(line_index, side=side, focus_diff=focus_diff)
        return True

    def _sync_combined_render_target(self, *, focus_diff: bool) -> None:
        if self._apply_pending_location_jump(COMBINED_DIFF_FILENAME):
            return
        if self._apply_pending_combined_file_jump():
            return

        selected_file = self.store.state.selected_file
        if selected_file:
            self.file_tree.select_file(selected_file, emit_message=False)
            self._jump_to_combined_file(selected_file, focus_diff=focus_diff)

    def _queue_file_render_request(
        self,
        filename: str,
        diff: FileDiff | None,
        *,
        focus_diff: bool,
        sync_tree_selection: bool,
    ) -> None:
        self._queued_file_render = (
            filename,
            diff,
            focus_diff,
            sync_tree_selection,
        )
        if self._file_render_worker_active:
            return

        self._file_render_worker_active = True
        self.run_worker(
            self._drain_queued_file_render_requests(),
            exclusive=False,
            name="file-diff-render",
        )

    async def _drain_queued_file_render_requests(self) -> None:
        while True:
            request = self._queued_file_render
            if request is None:
                self._file_render_worker_active = False
                if self._queued_file_render is None:
                    return
                self._file_render_worker_active = True
                continue

            self._queued_file_render = None
            filename, diff, focus_diff, sync_tree_selection = request
            if filename != COMBINED_DIFF_FILENAME and self._queue_combined_file_jump(
                filename,
                focus_diff=focus_diff,
            ):
                continue

            self._showing_combined_files = filename == COMBINED_DIFF_FILENAME
            if diff is None:
                diff = await self.store.get_file_diff_async(filename)
            if diff is None:
                continue
            if self._queued_file_render is not None:
                continue

            await self.diff_view.show_diff(filename, diff)
            if filename == COMBINED_DIFF_FILENAME:
                self._sync_combined_render_target(focus_diff=focus_diff)
            else:
                self._apply_pending_location_jump(filename)

            if sync_tree_selection:
                self.file_tree.select_file(filename, emit_message=False)
            if focus_diff:
                self.diff_view.focus()

    @on(FileTree.FileSelected)
    def on_file_tree_file_selected(self, event: FileTree.FileSelected) -> None:
        """Handle file selection from tree (Enter — focus moves to diff)."""
        event.stop()
        self.open_file(event.filename, focus_diff=True)

    @on(FileTree.FilePreviewed)
    def on_file_tree_file_previewed(self, event: FileTree.FilePreviewed) -> None:
        """Handle file preview from tree (Space — focus stays on tree)."""
        event.stop()
        self.open_file(event.filename, focus_diff=False)

    @on(PRStore.FileSelected)
    def on_store_file_selected(self, event: PRStore.FileSelected) -> None:
        """Handle file selection from store (external selection)."""
        event.stop()
        if self._jump_to_combined_file(event.filename, focus_diff=False):
            self.file_tree.select_file(event.filename, emit_message=False)
            return
        if self._queue_combined_file_jump(event.filename, focus_diff=False):
            return
        if self.diff_view.current_file == event.filename:
            self.store.state.selected_file = event.filename
            self.file_tree.select_file(event.filename, emit_message=False)
            return

        self._queue_file_render_request(
            event.filename,
            event.diff,
            focus_diff=False,
            sync_tree_selection=True,
        )

    @on(DiffView.CursorLineChanged)
    def on_diff_cursor_line_changed(self, event: DiffView.CursorLineChanged) -> None:
        event.stop()
        self._sync_combined_selection_for_cursor()

    @on(DiffView.FullFilePreviewRequested)
    def on_diff_full_file_preview_requested(
        self,
        event: DiffView.FullFilePreviewRequested,
    ) -> None:
        event.stop()
        self.run_worker(
            self._show_full_file_preview(event.filename),
            exclusive=True,
            name="file-full-preview",
        )

    @on(DiffView.FullFilePreviewRestored)
    def on_diff_full_file_preview_restored(
        self,
        event: DiffView.FullFilePreviewRestored,
    ) -> None:
        event.stop()
        if event.filename != COMBINED_DIFF_FILENAME:
            return

        self._showing_combined_files = True
        self._sync_combined_selection_for_cursor()

    async def _show_full_file_preview(self, filename: str) -> None:
        diff = await self.store.get_file_diff_async(filename)
        if diff is None:
            self.post_message(
                Flash("Failed to load file diff", style="error", duration=2.0)
            )
            return

        content = await self.store.get_file_content(filename)
        if content is None:
            self.post_message(
                Flash("Failed to load file content", style="error", duration=2.0)
            )
            return

        restore_filename = filename
        restore_diff = diff
        if (
            self._showing_combined_files
            and self.diff_view.current_file == COMBINED_DIFF_FILENAME
            and self.diff_view._diff is not None
        ):
            restore_filename = COMBINED_DIFF_FILENAME
            restore_diff = self.diff_view._diff

        self._showing_combined_files = False
        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)
        await self.diff_view.show_full_file_preview(
            filename,
            content,
            source_diff=diff,
            restore_filename=restore_filename,
            restore_diff=restore_diff,
        )

    @on(DiffView.HunkNavigated)
    def on_diff_hunk_navigated(self, event: DiffView.HunkNavigated) -> None:
        event.stop()
        self._sync_combined_selection_for_cursor()

    @on(ResizeHandle.Drag)
    def on_resize_handle_drag(self, event: ResizeHandle.Drag) -> None:
        if not self._is_dragging:
            self._is_dragging = True
            self.ghost_handle.display = "block"
            self.ghost_handle.styles.offset = (self.sidebar_width, 0)
            self._drag_delta = 0

        self._drag_delta += event.delta_x

        new_width = self.sidebar_width + self._drag_delta
        MIN_WIDTH = 20
        MAX_WIDTH = 80
        constrained_width = max(MIN_WIDTH, min(new_width, MAX_WIDTH))
        self.ghost_handle.styles.offset = (constrained_width, 0)

    @on(ResizeHandle.DragEnd)
    def on_resize_handle_drag_end(self, event: ResizeHandle.DragEnd) -> None:
        if self._is_dragging:
            self._is_dragging = False
            self.ghost_handle.display = "none"
            new_width = self.sidebar_width + self._drag_delta
            self._update_sidebar_width(new_width)
            self._drag_delta = 0

    def show_file_tree(self) -> None:
        self.file_tree.display = True
        self.resize_handle.display = True

    def toggle_file_tree(self) -> None:
        if self.file_tree.display:
            self.file_tree.display = False
            self.resize_handle.display = False
            self.diff_view.focus()
        else:
            self.show_file_tree()
            self.file_tree.focus()

    def action_prev_file(self) -> None:
        self.select_prev_file()

    def action_next_file(self) -> None:
        self.select_next_file()

    def action_expand_sidebar(self) -> None:
        self._update_sidebar_width(self.sidebar_width + 2)

    def action_collapse_sidebar(self) -> None:
        self._update_sidebar_width(self.sidebar_width - 2)

    def update_file_view_state(self, filename: str) -> None:
        """Update viewed badge for a single file (no full tree rebuild)."""
        self.file_tree.update_view_state(filename)
        if self.diff_view.current_file == filename:
            self.diff_view.refresh_header()

    def _update_sidebar_width(self, new_width: int) -> None:
        MIN_WIDTH = 20
        MAX_WIDTH = 80

        self.sidebar_width = max(MIN_WIDTH, min(new_width, MAX_WIDTH))
