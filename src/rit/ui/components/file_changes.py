from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from textual import getters, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import Static

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.store import PRStore
from rit.ui.widgets import DiffView, FileTree
from rit.ui.widgets.resize_handle import ResizeHandle

if TYPE_CHECKING:
    pass


COMBINED_DIFF_FILENAME = "All files"
COMBINED_FILES_THRESHOLD = 2


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
        self._combined_file_line_starts: dict[str, int] = {}
        self._showing_combined_files = False

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
        if state.files and not state.selected_file:
            if self._queue_combined_files_render():
                first_file = state.files[0].filename
                self.store.state.selected_file = first_file
                self.file_tree.select_file(first_file, emit_message=False)
                return

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

    def _queue_combined_files_render(self) -> bool:
        combined = self._build_combined_files_diff()
        if combined is None:
            return False

        self._showing_combined_files = True
        self._queue_file_render_request(
            COMBINED_DIFF_FILENAME,
            combined,
            focus_diff=False,
            sync_tree_selection=False,
        )
        return True

    def _build_combined_files_diff(self) -> FileDiff | None:
        state = self.store.state
        if len(state.files) < COMBINED_FILES_THRESHOLD:
            return None
        if not all(file.filename in state.file_diffs for file in state.files):
            return None

        hunks: list[DiffHunk] = []
        self._combined_file_line_starts = {}
        next_line_index = 0
        is_fully_refined = True

        for file in state.files:
            diff = state.file_diffs[file.filename]
            is_fully_refined = is_fully_refined and diff.is_fully_refined
            file_start_recorded = False

            if not diff.hunks:
                self._combined_file_line_starts[file.filename] = next_line_index
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
                    self._combined_file_line_starts[file.filename] = next_line_index
                    file_start_recorded = True

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
            self._showing_combined_files = filename == COMBINED_DIFF_FILENAME
            if diff is None:
                diff = await self.store.get_file_diff_async(filename)
            if diff is None:
                continue
            if self._queued_file_render is not None:
                continue

            await self.diff_view.show_diff(filename, diff)

            if sync_tree_selection:
                self.file_tree.select_file(filename, emit_message=False)
            if focus_diff:
                self.diff_view.focus()

    @on(FileTree.FileSelected)
    def on_file_tree_file_selected(self, event: FileTree.FileSelected) -> None:
        """Handle file selection from tree (Enter — focus moves to diff)."""
        event.stop()

        if self._jump_to_combined_file(event.filename, focus_diff=True):
            return

        self._queue_file_render_request(
            event.filename,
            None,
            focus_diff=True,
            sync_tree_selection=False,
        )

    @on(FileTree.FilePreviewed)
    def on_file_tree_file_previewed(self, event: FileTree.FilePreviewed) -> None:
        """Handle file preview from tree (Space — focus stays on tree)."""
        event.stop()

        if self._jump_to_combined_file(event.filename, focus_diff=False):
            return

        self._queue_file_render_request(
            event.filename,
            None,
            focus_diff=False,
            sync_tree_selection=False,
        )

    @on(PRStore.FileSelected)
    def on_store_file_selected(self, event: PRStore.FileSelected) -> None:
        """Handle file selection from store (external selection)."""
        event.stop()
        if self._jump_to_combined_file(event.filename, focus_diff=False):
            self.file_tree.select_file(event.filename, emit_message=False)
            return

        self._queue_file_render_request(
            event.filename,
            event.diff,
            focus_diff=False,
            sync_tree_selection=True,
        )

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
