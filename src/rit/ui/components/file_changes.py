from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import TYPE_CHECKING, ClassVar, Literal

from rich.text import Text
from textual import events, getters, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Static, Tree
from textual.worker import Worker, WorkerState

from rit.core.types import FileDiff
from rit.state.models import LoadingState
from rit.state.review_scope import ReviewScope
from rit.state.store import PRStore
from rit.ui.components.combined_diff import (
    COMBINED_DIFF_FILENAME,
    build_combined_diff_document,
    load_missing_combined_file_diffs,
)
from rit.ui.components.files_render_session import FilesRenderSession
from rit.ui.messages import Flash
from rit.ui.screens.review_scope import ReviewScopePicker
from rit.ui.widgets import DiffView, FileTree
from rit.ui.widgets.resize_handle import ResizeHandle

if TYPE_CHECKING:
    from rit.ui.components.combined_diff import CombinedDiffDocument


__all__ = (
    "COMBINED_DIFF_FILENAME",
    "FileChanges",
)


COMBINED_DIFF_LOAD_CONCURRENCY = 8


def _diff_mode_setting(value: object) -> Literal["auto", "split", "unified"] | None:
    if value == "auto":
        return "auto"
    if value == "split":
        return "split"
    if value == "unified":
        return "unified"
    return None


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


class FileChanges(Vertical):
    """File Changes tab with file tree and diff view."""

    DEFAULT_CSS = """
    FileChanges {
        width: 100%;
        height: 100%;
        layers: base overlay;
    }
    
    #files-workspace { height: 1fr; layers: base overlay; }
    #review-scope-bar { height: 3; padding: 1 0; background: $surface; }
    #review-scope-controls { height: 1; }
    #review-scope-label { width: 10; padding-left: 1; color: $text-muted; }
    #review-scope-controls Button {
        height: 1; min-width: 3; width: auto; border: none; padding: 0 1;
        background: transparent; margin: 0;
    }
    #review-scope-controls Button:focus { background: $primary 25%; text-style: bold; }
    #review-scope-controls Button:hover { background: $primary 15%; }
    #review-scope-controls #review-scope-open {
        max-width: 45%; padding: 0 2; margin-right: 1;
        background: $primary 12%; text-style: bold;
    }
    #review-scope-index { width: auto; }
    #review-scope-stats { width: auto; color: $text-muted; padding-right: 1; }
    #review-scope-bar.-compact #review-scope-stats { display: none; }
    #review-scope-detail {
        width: 1fr; min-width: 0; height: 1; color: $text-muted;
        padding: 0 1; text-wrap: nowrap; text-overflow: ellipsis;
    }
    #review-scope-detail.-error { color: $warning; }
    #review-scope-empty { display: none; width: 1fr; height: 100%; content-align: center middle; }
    GhostHandle { layer: overlay; }
    FileTree, ResizeHandle, DiffView { layer: base; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding(">", "expand_sidebar", "Expand Sidebar", show=False),
        Binding("<", "collapse_sidebar", "Collapse Sidebar", show=False),
        Binding("[", "prev_file", "Prev File", show=False),
        Binding("]", "next_file", "Next File", show=False),
    ]

    class WorkspaceReady(Message):
        """A current-revision diff finished rendering."""

    sidebar_width = reactive(35)  # Default width

    file_tree = getters.query_one(FileTree)
    diff_view = getters.query_one(DiffView)
    resize_handle = getters.query_one(ResizeHandle)
    ghost_handle = getters.query_one(GhostHandle)

    @property
    def _showing_combined_files(self) -> bool:
        return self._render_session.showing_combined_files

    @property
    def _combined_document(self) -> CombinedDiffDocument | None:
        return self._render_session.combined_document

    @property
    def _combined_file_line_starts(self) -> dict[str, int]:
        document = self._render_session.combined_document
        return document.file_line_starts if document is not None else {}

    def __init__(self, store: PRStore) -> None:
        super().__init__()
        self.store = store
        self._drag_delta = 0
        self._is_dragging = False
        self._queued_file_render: (
            tuple[int, str, FileDiff | None, bool, bool, bool] | None
        ) = None
        self._file_render_request_revision = 0
        self._file_render_worker_active = False
        self._render_session = FilesRenderSession()
        self._combined_render_worker_active = False
        self._displayed_file_revision = 0
        self._scope_render_pending = False
        self._restore_scope_focus = False

    def compose(self) -> ComposeResult:
        with Vertical(id="review-scope-bar"), Horizontal(id="review-scope-controls"):
            yield Static("Changes", id="review-scope-label")
            yield Button("All changes ▾", id="review-scope-open")
            yield Button("‹", id="review-scope-prev", tooltip="Previous commit (,)")
            yield Static("", id="review-scope-index")
            yield Button("›", id="review-scope-next", tooltip="Next commit (.)")
            yield Button("Reset", id="review-scope-all", tooltip="Back to All changes")
            yield Button("Retry", id="review-scope-retry")
            yield Static("", id="review-scope-detail", markup=False)
            yield Static("", id="review-scope-stats", markup=False)
        with Horizontal(id="files-workspace"):
            yield FileTree(store=self.store, id="file-tree-sidebar")
            yield ResizeHandle(id="resize-handle")
            yield DiffView(store=self.store, id="diff-view-main")
            yield Static("", id="review-scope-empty", markup=False)
            yield GhostHandle(id="ghost-handle")

    def on_mount(self) -> None:
        self.refresh_scope()
        self._apply_diff_settings_from_app()
        signal = getattr(self.app, "settings_changed_signal", None)
        if signal is not None:
            signal.subscribe(self, self._on_settings_changed)

    def on_resize(self, event: events.Resize) -> None:
        self.query_one("#review-scope-bar").set_class(event.size.width < 90, "-compact")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.diff_view.has_focus_within and not self.diff_view.has_focus:
            return False
        return super().check_action(action, parameters)

    def on_unmount(self) -> None:
        signal = getattr(self.app, "settings_changed_signal", None)
        if signal is not None:
            signal.unsubscribe(self)

    def watch_sidebar_width(self, width: int) -> None:
        try:
            self.file_tree.styles.width = width
        except NoMatches:
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
        if key == "ui.diff_mode":
            mode = _diff_mode_setting(value)
            if mode is not None:
                self.diff_view.mode = mode
        elif key == "ui.show_line_numbers" and isinstance(value, bool):
            self.diff_view.show_line_numbers = value
        elif key == "ui.word_diff" and isinstance(value, bool):
            self.diff_view.word_diff_enabled = value
        elif key == "ui.theme" and isinstance(value, str):
            self.diff_view.refresh_syntax_theme()

    @on(Worker.StateChanged)
    def _file_render_finished(self, event: Worker.StateChanged) -> None:
        if (
            event.worker.name == "file-diff-render"
            and event.state == WorkerState.SUCCESS
            and self.workspace_ready
        ):
            self._scope_render_pending = False
            self.diff_view.display = True
            self.query_one("#review-scope-empty").display = False
            self.post_message(self.WorkspaceReady())
            self.call_after_refresh(self._restore_comparison_focus)

    def reset_workspace(self) -> None:
        """Discard render requests and cached documents for a superseded PR revision."""
        self.workers.cancel_group(self, "default")
        self.diff_view.reset_comparison()
        self._file_render_request_revision += 1
        self._queued_file_render = None
        self._file_render_worker_active = False
        self._combined_render_worker_active = False
        self._render_session = FilesRenderSession()

    @property
    def workspace_ready(self) -> bool:
        if (
            self.store.state.files_loading == LoadingState.LOADED
            and not self.store.state.files
        ):
            return not self.store.state.scope_loading
        document = self._render_session.combined_document
        return (
            document is not None
            and self.diff_view._source_diff is document.diff
            and not self._file_render_worker_active
        )

    def refresh_files(self) -> None:
        state = self.store.state
        if self._displayed_file_revision != state.file_revision:
            self._displayed_file_revision = state.file_revision
            self.reset_workspace()
            self._scope_render_pending = bool(state.files)
            self.diff_view.display = False
            empty = self.query_one("#review-scope-empty", Static)
            empty.display = True
            empty.update(
                "Preparing comparison…"
                if state.files
                else "No changes since your last review"
                if state.scope.kind == "since"
                else "No changes in this comparison"
            )
            if not state.files:
                self.post_message(self.WorkspaceReady())
                self.call_after_refresh(self._restore_comparison_focus)
        self.refresh_scope()
        self.file_tree.refresh_files()

        if (
            state.files
            and not self._files_are_still_loading()
            and self._queue_combined_files_render()
        ):
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

    def refresh_scope(self) -> None:
        """Keep the toolbar attached to the comparison actually on screen."""
        state = self.store.state
        scope = state.scope
        self.diff_view.disabled = state.scope_loading
        scope_button = self.query_one("#review-scope-open", Button)
        label = f"{scope.label} ▾"
        if str(scope_button.label) != label:
            scope_button.label = label
            scope_button.refresh(layout=True)
        scope_button.tooltip = f"Change review scope (s)\n{scope.detail}"
        scope_button.disabled = state.files_loading == LoadingState.LOADING
        context = (
            ""
            if scope.kind == "all"
            else f"Read-only · {scope.base_sha[:7]} → {scope.head_sha[:7]} · {scope.title}"
        )
        detail = (
            "Loading comparison…"
            if state.scope_loading
            else state.scope_error or context
        )
        detail_widget = self.query_one("#review-scope-detail", Static)
        detail_widget.update(detail)
        detail_widget.tooltip = detail or None
        detail_widget.set_class(bool(state.scope_error), "-error")
        additions = sum(file.additions for file in state.files)
        deletions = sum(file.deletions for file in state.files)
        self.query_one("#review-scope-stats", Static).update(
            Text.assemble(
                f"{len(state.files)} files  ",
                (f"+{additions}", "green"),
                " ",
                (f"−{deletions}", "red"),
            )
        )
        self.query_one("#review-scope-all").display = scope.kind != "all"
        self.query_one("#review-scope-retry").display = bool(state.scope_error)
        history = state.review_history.loaded_value
        for selector, direction in (
            ("#review-scope-prev", -1),
            ("#review-scope-next", 1),
        ):
            button = self.query_one(selector, Button)
            button.display = scope.kind == "commit"
            try:
                adjacent = history.adjacent(scope, direction) if history else None
            except ValueError:
                adjacent = None
            button.disabled = adjacent is None
        position = ""
        if history and scope.kind == "commit":
            for index, commit in enumerate(history.commits):
                if commit.sha == scope.head_sha:
                    position = f"{index + 1}/{len(history.commits)}"
                    break
        self.query_one("#review-scope-index", Static).update(position)

    def open_review_scope(self) -> None:
        if not self._can_change_scope():
            return
        self._restore_scope_focus = self.diff_view.has_focus
        self.app.push_screen(
            ReviewScopePicker(self.store, self.query_one("#review-scope-open").region),
            self.select_review_scope,
        )

    def _can_change_scope(self) -> bool:
        if (
            self.diff_view.inline_comment_target() is not None
            or self.diff_view.file_comment_target() is not None
        ):
            self.notify(
                "Save or cancel the comment editor before changing scope",
                severity="warning",
            )
            return False
        if self.store.state.files_loading == LoadingState.LOADING:
            self.notify("Wait for the initial file load to finish", severity="warning")
            return False
        return True

    def select_review_scope(self, scope: ReviewScope | None) -> None:
        if scope is None or not self._can_change_scope():
            self._restore_scope_focus = False
            return
        self._restore_scope_focus |= self.diff_view.has_focus
        self.run_worker(self._load_scope(scope), group="review-scope", exclusive=True)

    async def _load_scope(self, scope: ReviewScope) -> None:
        changed = await self.store.select_review_scope(scope)
        if changed:
            self.refresh_files()
        self.refresh_scope()
        if not changed:
            self.call_after_refresh(self._restore_comparison_focus)
        if self.store.state.scope_error:
            self.notify(self.store.state.scope_error, severity="warning", markup=False)

    def _restore_comparison_focus(self) -> None:
        if not self._restore_scope_focus:
            return
        self._restore_scope_focus = False
        target = (
            self.diff_view
            if self.store.state.files
            else self.query_one("#review-scope-open")
        )
        if (
            self.app.screen is self.screen
            and self.screen.focused is None
            and target in self.screen.focus_chain
        ):
            target.focus()

    def step_commit(self, direction: int) -> None:
        history = self.store.state.review_history.loaded_value
        if history is None:
            return
        try:
            scope = history.adjacent(self.store.state.scope, direction)
        except ValueError as error:
            self.notify(str(error), severity="warning", markup=False)
            return
        self.select_review_scope(scope)

    @on(Button.Pressed, "#review-scope-open")
    def _open_scope(self, event: Button.Pressed) -> None:
        event.stop()
        self.open_review_scope()

    @on(Button.Pressed, "#review-scope-all")
    def _all_changes(self, event: Button.Pressed) -> None:
        event.stop()
        self.select_review_scope(ReviewScope())

    @on(Button.Pressed, "#review-scope-retry")
    def _retry_comparison(self, event: Button.Pressed) -> None:
        event.stop()
        self.select_review_scope(self.store.state.requested_scope)

    @on(Button.Pressed, "#review-scope-prev")
    def _previous_commit(self, event: Button.Pressed) -> None:
        event.stop()
        self.step_commit(-1)

    @on(Button.Pressed, "#review-scope-next")
    def _next_commit(self, event: Button.Pressed) -> None:
        event.stop()
        self.step_commit(1)

    def select_next_file(self) -> None:
        self._select_relative_file(1)

    def select_prev_file(self) -> None:
        self._select_relative_file(-1)

    def select_file_after(self, filename: str) -> None:
        """Advance without wrapping or changing the next file's fold state."""
        for current, following in pairwise(self.store.state.files):
            if current.filename == filename:
                self.open_file(
                    following.filename, focus_diff=True, expand=False, top_align=True
                )
                return

    def _select_relative_file(self, direction: Literal[-1, 1]) -> None:
        files = self.store.state.files
        file_count = len(files)
        if file_count == 0:
            return

        current_filename = self.current_diff_file_target()
        current_index = next(
            (
                index
                for index, file in enumerate(files)
                if file.filename == current_filename
            ),
            self.file_tree.get_current_index(),
        )
        target_index = (current_index + direction) % file_count
        self.open_file(
            files[target_index].filename,
            focus_diff=True,
            preserve_scroll_if_near_center=True,
        )

    def next_hunk(self) -> None:
        self.diff_view.next_hunk()

    def prev_hunk(self) -> None:
        self.diff_view.prev_hunk()

    def open_file(
        self,
        filename: str,
        *,
        focus_diff: bool,
        preserve_scroll_if_near_center: bool = False,
        expand: bool = True,
        top_align: bool = False,
    ) -> None:
        """Open a file diff, optionally retaining its fold state."""
        if not filename:
            return

        if expand:
            self.diff_view.expand_file(filename)
        self._render_session.clear_pending_location_jump()
        if self._jump_to_combined_file(
            filename,
            focus_diff=focus_diff,
            preserve_scroll_if_near_center=preserve_scroll_if_near_center,
            top_align=top_align,
        ):
            self.file_tree.select_file(filename, emit_message=False)
            return

        if self._queue_combined_file_jump(
            filename, focus_diff=focus_diff, top_align=top_align
        ):
            return

        if self.diff_view.current_file == filename:
            self.store.state.selected_file = filename
            self.file_tree.select_file(filename, emit_message=False)
            if focus_diff:
                self.diff_view.focus()
            if top_align:
                self.diff_view.scroll_file_to_top(filename)
            return

        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)
        self._queue_file_render_request(
            filename,
            None,
            focus_diff=focus_diff,
            sync_tree_selection=False,
            top_align=top_align,
        )

    def _uses_combined_files(self) -> bool:
        return (
            not self._files_are_still_loading()
        ) and self._render_session.uses_combined_files(self.store.state.files)

    def _files_are_still_loading(self) -> bool:
        return self.store.state.files_loading == LoadingState.LOADING

    def _queue_combined_file_jump(
        self, filename: str, *, focus_diff: bool, top_align: bool = False
    ) -> bool:
        if self._files_are_still_loading():
            return False
        if not self._render_session.queue_combined_file_jump(
            self.store.state.files,
            filename,
            focus_diff=focus_diff,
            top_align=top_align,
        ):
            return False

        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)
        self._queue_combined_files_render(focus_diff=False)
        return True

    def _queue_combined_files_render(
        self,
        *,
        focus_diff: bool = False,
        force: bool = False,
    ) -> bool:
        if self._files_are_still_loading() and not force:
            return False
        if not self._render_session.queue_combined_render(
            self.store.state.files,
            current_file=self.diff_view.current_file,
            focus_diff=focus_diff,
            force=force,
        ):
            return False
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
            request = self._render_session.take_queued_combined_render()
            if request is None:
                self._combined_render_worker_active = False
                if not self._render_session.has_queued_combined_render():
                    return
                self._combined_render_worker_active = True
                continue

            signature = request.signature
            focus_diff = request.focus_diff
            await self._ensure_combined_file_diffs_loaded(signature)
            if self._render_session.has_queued_combined_render():
                continue

            document = await asyncio.to_thread(
                self._build_combined_document,
                signature,
            )
            if document is None or self._render_session.has_queued_combined_render():
                continue

            self._render_session.record_combined_document(signature, document)
            self._queue_file_render_request(
                COMBINED_DIFF_FILENAME,
                document.diff,
                focus_diff=focus_diff,
                sync_tree_selection=False,
            )

    async def _ensure_combined_file_diffs_loaded(
        self,
        signature: tuple[str, ...],
    ) -> None:
        await load_missing_combined_file_diffs(
            signature,
            self.store.state.file_diffs,
            self.store.get_file_diff_async,
            concurrency=COMBINED_DIFF_LOAD_CONCURRENCY,
        )

    def _build_combined_document(
        self,
        signature: tuple[str, ...],
    ) -> CombinedDiffDocument | None:
        state = self.store.state
        if not signature:
            return None

        files = []
        for filename in signature:
            file = state.files_by_filename.get(filename) or next(
                (file for file in state.files if file.filename == filename),
                None,
            )
            if file is None:
                return None
            files.append(file)

        document = build_combined_diff_document(files, state.file_diffs)
        if document is None:
            return None
        return document

    def _jump_to_combined_file(
        self,
        filename: str,
        *,
        focus_diff: bool,
        preserve_scroll_if_near_center: bool = False,
        top_align: bool = False,
    ) -> bool:
        if not self._render_session.showing_combined_files:
            return False

        document = self._render_session.combined_document
        if document is None:
            return False

        line_index = self.diff_view.file_start_line_index(filename)
        if line_index is None:
            line_index = document.file_line_starts.get(filename)
        if line_index is None:
            return False

        self.store.state.selected_file = filename
        if self.diff_view._should_collapse_file(filename):
            self.diff_view._restore_file_fold_target(
                filename,
                preserve_header_position=False,
                viewport_offset=None,
            )
            if focus_diff:
                self.diff_view.focus()
        else:
            self.diff_view.jump_to_line_index(
                line_index,
                side="RIGHT",
                focus=focus_diff,
                preserve_scroll_if_near_center=preserve_scroll_if_near_center,
            )
        if top_align:
            self.diff_view.scroll_file_to_top(filename)
        return True

    def _combined_file_for_line(self, line_index: int) -> str | None:
        return self._render_session.combined_file_for_line(line_index)

    def current_diff_file_target(self) -> str | None:
        """Return the real file represented by the current diff cursor."""
        header_path = self.diff_view.selected_file_header_path()
        if header_path is not None:
            return header_path
        if self._showing_combined_files:
            return (
                self.diff_view.file_for_line_index(self.diff_view.cursor_line)
                or self._combined_file_for_line(self.diff_view.cursor_line)
                or self.store.state.selected_file
            )
        return self.diff_view.current_file

    def sync_file_tree_to_diff_cursor(self) -> None:
        """Move the file tree selection to the file under the diff cursor."""
        filename = self.current_diff_file_target()
        if filename is None:
            return
        if not any(file.filename == filename for file in self.store.state.files):
            return
        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)

    def _sync_combined_selection_for_cursor(self) -> None:
        filename = self.current_diff_file_target()
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
        if self._render_session.showing_combined_files:
            line_index = self._line_index_for_location(filename, line, side)
            if line_index is None:
                return False
            self.store.state.selected_file = filename
            self.file_tree.select_file(filename, emit_message=False)
            self._jump_to_diff_line(line_index, side=side, focus_diff=focus_diff)
            return True

        if self._uses_combined_files():
            self._render_session.queue_location_jump(
                filename,
                line,
                side,
                focus_diff=focus_diff,
            )
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

        render_revision = self._queue_file_render_request(
            filename,
            None,
            focus_diff=False,
            sync_tree_selection=True,
        )
        self._render_session.queue_location_jump(
            filename,
            line,
            side,
            focus_diff=focus_diff,
            render_revision=render_revision,
        )
        return True

    def _line_index_for_location(
        self,
        filename: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> int | None:
        diff = self.diff_view.current_diff
        if diff is None:
            return None

        return self.diff_view.line_index_for_location(filename, line, side)

    def _jump_to_diff_line(
        self,
        line_index: int,
        *,
        side: Literal["LEFT", "RIGHT"],
        focus_diff: bool,
    ) -> None:
        self.diff_view.jump_to_line_index(line_index, side=side, focus=focus_diff)

    def _apply_pending_combined_file_jump(self) -> bool:
        pending = self._render_session.take_pending_combined_file_jump()
        if pending is None:
            return False

        filename = pending.filename
        focus_diff = pending.focus_diff
        self.file_tree.select_file(filename, emit_message=False)
        return self._jump_to_combined_file(
            filename, focus_diff=focus_diff, top_align=pending.top_align
        )

    def _apply_pending_location_jump(self, filename: str) -> bool:
        pending = self._render_session.take_pending_location_jump(filename)
        if pending is None:
            return False

        line_index = self._line_index_for_location(
            pending.filename,
            pending.line,
            pending.side,
        )
        if line_index is None:
            return False
        self._jump_to_diff_line(
            line_index,
            side=pending.side,
            focus_diff=pending.focus_diff,
        )
        return True

    def _sync_combined_render_target(
        self, *, focus_diff: bool, navigation_revision: int
    ) -> None:
        if self._apply_pending_location_jump(COMBINED_DIFF_FILENAME):
            return
        if self._apply_pending_combined_file_jump():
            return
        if self.diff_view.view_revision[1] != navigation_revision:
            return

        selected_file = self.store.state.selected_file
        if selected_file:
            self.file_tree.select_file(selected_file, emit_message=False)
            if self.current_diff_file_target() != selected_file:
                self._jump_to_combined_file(selected_file, focus_diff=focus_diff)

    def _queue_file_render_request(
        self,
        filename: str,
        diff: FileDiff | None,
        *,
        focus_diff: bool,
        sync_tree_selection: bool,
        top_align: bool = False,
    ) -> int:
        self._file_render_request_revision += 1
        self._queued_file_render = (
            self._file_render_request_revision,
            filename,
            diff,
            focus_diff,
            sync_tree_selection,
            top_align,
        )
        if self._file_render_worker_active:
            return self._file_render_request_revision

        self._file_render_worker_active = True
        self.run_worker(
            self._drain_queued_file_render_requests(),
            exclusive=False,
            name="file-diff-render",
        )
        return self._file_render_request_revision

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
            (
                request_revision,
                filename,
                diff,
                focus_diff,
                sync_tree_selection,
                top_align,
            ) = request
            if filename != COMBINED_DIFF_FILENAME and self._queue_combined_file_jump(
                filename,
                focus_diff=focus_diff,
                top_align=top_align,
            ):
                continue

            self._render_session.set_showing_combined_files(
                filename == COMBINED_DIFF_FILENAME
            )
            if diff is None:
                diff = await self.store.get_file_diff_async(filename)
            if diff is None:
                self._render_session.discard_pending_location_jump(request_revision)
                continue
            if request_revision != self._file_render_request_revision:
                self._render_session.discard_pending_location_jump(request_revision)
                continue

            navigation_revision = self.diff_view.view_revision[1]
            await self.diff_view.show_diff(filename, diff)
            if request_revision != self._file_render_request_revision:
                self._render_session.discard_pending_location_jump(request_revision)
                continue
            if filename == COMBINED_DIFF_FILENAME:
                self._sync_combined_render_target(
                    focus_diff=focus_diff, navigation_revision=navigation_revision
                )
            else:
                self._apply_pending_location_jump(filename)

            if sync_tree_selection:
                self.file_tree.select_file(filename, emit_message=False)
            if focus_diff:
                self.diff_view.focus()
            if top_align:
                self.diff_view.scroll_file_to_top(filename)

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
        self._render_session.clear_pending_location_jump()
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
            self._show_full_file_preview(event.filename, event.view_revision),
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

        self._render_session.set_showing_combined_files(True)
        self._sync_combined_selection_for_cursor()

    async def _show_full_file_preview(
        self,
        filename: str,
        view_revision: tuple[int, int],
    ) -> None:
        if self.diff_view.view_revision != view_revision:
            return
        diff = await self.store.get_file_diff_async(filename)
        if self.diff_view.view_revision != view_revision:
            return
        if diff is None:
            self.post_message(
                Flash("Failed to load file diff", style="error", duration=2.0)
            )
            return

        content = await self.store.get_file_content(filename)
        if self.diff_view.view_revision != view_revision:
            return
        if content is None:
            self.post_message(
                Flash("Failed to load file content", style="error", duration=2.0)
            )
            return

        restore_target = self._render_session.full_file_preview_restore_target(
            filename=filename,
            file_diff=diff,
            current_file=self.diff_view.current_file,
            current_diff=self.diff_view.current_diff,
        )

        accepted = await self.diff_view.show_full_file_preview(
            filename,
            content,
            source_diff=diff,
            restore_filename=restore_target.filename,
            restore_diff=restore_target.diff,
            expected_view_revision=view_revision,
        )
        if not accepted:
            return
        self._render_session.set_showing_combined_files(False)
        self.store.state.selected_file = filename
        self.file_tree.select_file(filename, emit_message=False)

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

    def move_focus(self, direction: Literal["left", "right"]) -> bool:
        """Request horizontal focus; False means there is no internal target."""
        if not self.is_mounted:
            return False
        if self.file_tree.has_focus_within:
            return direction == "right" and self.diff_view.move_focus(direction)
        if self.diff_view.has_focus_within:
            if self.diff_view.move_focus(direction):
                return True
            if direction == "right" or not self.file_tree.display:
                return False
        elif direction == "right" or not self.file_tree.display:
            return self.diff_view.move_focus(direction)
        self.focus_file_tree()
        return True

    def restore_focus(self) -> None:
        """Prefer the diff without disturbing focus already in the file content."""
        if self.is_mounted and not (
            self.file_tree.has_focus_within or self.diff_view.has_focus_within
        ):
            self.diff_view.focus()

    def focus_file_tree(self) -> None:
        """Reveal the tree and focus the file at the diff cursor."""
        if not self.is_mounted:
            return
        self.file_tree.display = True
        self.resize_handle.display = True
        self.sync_file_tree_to_diff_cursor()
        self.file_tree.query_one("#file-tree", Tree).focus()

    def toggle_file_tree(self) -> None:
        if self.file_tree.display:
            self.file_tree.display = False
            self.resize_handle.display = False
            self.diff_view.focus()
        else:
            self.focus_file_tree()

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
        if self.diff_view.current_file == filename or self._showing_combined_files:
            self.diff_view.refresh_header()
            self.diff_view.refresh_viewed_folds()

    def _update_sidebar_width(self, new_width: int) -> None:
        MIN_WIDTH = 20
        MAX_WIDTH = 80

        self.sidebar_width = max(MIN_WIDTH, min(new_width, MAX_WIDTH))
