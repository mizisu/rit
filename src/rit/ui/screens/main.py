from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal

from textual import events, getters, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Input, TabbedContent, TabPane, TextArea, Tree
from textual.worker import Worker, WorkerState

from rit.app import RitApp
from rit.state.models import FileViewedState
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges
from rit.ui.components.pr_info import PRInfo
from rit.ui.messages import Flash
from rit.ui.screens.branch_picker import BranchPickerScreen
from rit.ui.screens.review_submit import ReviewSubmitScreen
from rit.ui.widgets import DiffView, Header
from rit.ui.widgets.comment_editor import InlineCommentEditor


_NAVIGATION_GROUP = Binding.Group("Navigation", compact=True)
_COMMENT_GROUP = Binding.Group("Comments", compact=True)
_REVIEW_GROUP = Binding.Group("Reviews", compact=True)
_FILE_GROUP = Binding.Group("Files", compact=True)
_TAB_GROUP = Binding.Group("Move Tab", compact=True)

_TAB_IDS = ("pr-info", "files")
_TAB_INDEX_BY_ID = {tab_id: index for index, tab_id in enumerate(_TAB_IDS)}
_SUBPROCESS_ERRORS = (OSError, subprocess.CalledProcessError)


def _resolve_repo_root() -> Path | None:
    if configured_root := os.environ.get("RIT_REPO_PATH"):
        return Path(configured_root).expanduser()

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except _SUBPROCESS_ERRORS:
        return None

    repo_root = result.stdout.strip()
    return Path(repo_root) if repo_root else None


def _open_in_parent_nvim(path: Path, *, line: int, column: int) -> None:
    server = os.environ.get("NVIM") or os.environ.get("NVIM_LISTEN_ADDRESS")
    if not server:
        raise RuntimeError("Parent Neovim server not available")

    target_path = json.dumps(str(path))
    target_line = max(1, line)
    target_column = max(0, column - 1)
    remote_command = (
        "<C-\\><C-N>:lua "
        f"local path = {target_path}; "
        f"local target_line = {target_line}; "
        f"local target_column = {target_column}; "
        "local origin = vim.api.nvim_get_current_win(); "
        "local origin_cfg = vim.api.nvim_win_get_config(origin); "
        "vim.cmd('tabnew'); "
        "vim.cmd('edit ' .. vim.fn.fnameescape(path)); "
        "local last_line = math.max(1, vim.api.nvim_buf_line_count(0)); "
        "local line_number = math.min(target_line, last_line); "
        "local line_text = vim.api.nvim_buf_get_lines(0, line_number - 1, line_number, true)[1] or ''; "
        "local column_number = math.min(target_column, #line_text); "
        "vim.api.nvim_win_set_cursor(0, { line_number, column_number }); "
        "vim.cmd('normal! zz'); "
        "if origin_cfg.relative ~= '' and vim.api.nvim_win_is_valid(origin) then vim.api.nvim_win_close(origin, true) end"
        "<CR>"
    )

    subprocess.run(
        ["nvim", "--server", server, "--remote-send", remote_command],
        check=True,
        capture_output=True,
    )


_COMMON_BINDINGS = [
    Binding("ctrl+d", "scroll_half_page_down", "Half Page Down", show=False),
    Binding("ctrl+u", "scroll_half_page_up", "Half Page Up", show=False),
    Binding("g", "scroll_to_top", "Top", show=False),
    Binding("G", "scroll_to_bottom", "Bottom", key_display="shift+g", show=False),
    Binding("ctrl+b", "copy_branch", "Copy Branch", show=False),
    Binding("enter", "toggle_item", "Toggle", show=False),
    Binding("space", "toggle_item", "Toggle", show=False),
    Binding(
        "tab",
        "next_tab",
        "Move Tab",
        key_display="tab/shift+tab",
        group=_TAB_GROUP,
        priority=True,
    ),
    Binding("shift+tab", "prev_tab", "", group=_TAB_GROUP, show=False, priority=True),
]


_PR_INFO_BINDINGS = [
    Binding("j", "cursor_down", "", group=_NAVIGATION_GROUP),
    Binding("k", "cursor_up", "", group=_NAVIGATION_GROUP),
    Binding(
        "J", "next_comment", "Comments", key_display="shift+j/k", group=_COMMENT_GROUP
    ),
    Binding("K", "prev_comment", "", group=_COMMENT_GROUP, show=False),
    Binding("c", "comment", "Comment", group=_COMMENT_GROUP),
    Binding("S", "review", "Review", group=_REVIEW_GROUP),
    Binding("r", "toggle_resolve", "Resolve", tooltip="Toggle thread resolution"),
]

_FILES_BINDINGS = [
    Binding(
        "H",
        "focus_left",
        "Move Focus",
        key_display="shift+h/l",
        group=_NAVIGATION_GROUP,
    ),
    Binding("c", "comment", "Comment", group=_COMMENT_GROUP),
    Binding("d", "delete_pending_comment", "Delete Draft", group=_COMMENT_GROUP),
    Binding("S", "review", "Review", group=_REVIEW_GROUP),
    Binding("L", "focus_right", "", group=_NAVIGATION_GROUP, show=False),
    Binding("e", "open_file_in_editor", "Edit", group=_FILE_GROUP),
    Binding(
        "E",
        "toggle_file_tree",
        "Toggle Tree",
        key_display="shift+e",
        group=_NAVIGATION_GROUP,
    ),
    Binding("[", "prev_file", "Files", key_display="[/]", group=_FILE_GROUP),
    Binding("]", "next_file", "", group=_FILE_GROUP, show=False),
    Binding("n", "next_hunk", "Hunks", key_display="n/N", group=_FILE_GROUP),
    Binding("N", "prev_hunk", "", group=_FILE_GROUP, show=False),
    Binding(
        "}",
        "next_comment",
        "Comments",
        key_display="{/}",
        group=_COMMENT_GROUP,
    ),
    Binding("{", "prev_comment", "", group=_COMMENT_GROUP, show=False),
    Binding("r", "toggle_resolve_file", "Resolve", group=_COMMENT_GROUP),
    Binding("m", "toggle_file_viewed", "Mark Viewed", group=_FILE_GROUP),
]


class MainScreen(Screen):
    BINDINGS = _COMMON_BINDINGS + _PR_INFO_BINDINGS

    current_tab: reactive[int] = reactive(0)

    header = getters.query_one(Header)
    pr_info = getters.query_one(PRInfo)
    file_changes = getters.query_one(FileChanges)
    tabbed_content = getters.query_one(TabbedContent)

    app: RitApp = getters.app(RitApp)  # type: ignore

    def __init__(
        self,
        owner: str | None = None,
        repo: str | None = None,
        pr_number: int = 0,
    ) -> None:
        super().__init__()
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number

        self.store = PRStore(owner=owner, repo=repo, pr_number=pr_number)

    def compose(self) -> ComposeResult:
        yield Header(
            owner=self.owner,
            repo=self.repo,
            pr_number=self.pr_number,
            id="main-header",
        )

        with TabbedContent(initial="pr-info", id="main-tabs"):
            with TabPane("PR Info", id="pr-info"):
                yield PRInfo(store=self.store)
            with TabPane("Files", id="files"):
                yield FileChanges(store=self.store)

        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self._load_data(), exclusive=True)

    async def _load_data(self) -> None:
        await self.store.load_all()
        self._update_ui_after_load()
        await self.store.load_file_view_states()
        self._apply_viewed_states()

    def _update_ui_after_load(self) -> None:
        state = self.store.state

        if state.error:
            self.notify(state.error, title="Error", severity="error")
            return

        if state.pr:
            self.header.update_from_pr(state.pr)

        self.pr_info.refresh_pr_data()
        self.pr_info.refresh_comments()
        self.file_changes.refresh_files()

    def switch_tab(self, tab_index: int) -> None:
        if not 0 <= tab_index < len(_TAB_IDS):
            return

        target_tab_id = _TAB_IDS[tab_index]

        if self.tabbed_content.active != target_tab_id:
            self.tabbed_content.active = target_tab_id

        self.current_tab = tab_index

    def next_tab(self) -> None:
        next_index = (self.current_tab + 1) % len(_TAB_IDS)
        self.switch_tab(next_index)

    def prev_tab(self) -> None:
        prev_index = (self.current_tab - 1) % len(_TAB_IDS)
        self.switch_tab(prev_index)

    @on(TabbedContent.TabActivated)
    def on_tab_activated(self, _event: TabbedContent.TabActivated) -> None:
        tab_id = self.tabbed_content.active
        if tab_id is None:
            return

        tab_index = _TAB_INDEX_BY_ID.get(tab_id)
        if tab_index is None:
            return

        self.current_tab = tab_index

        if tab_id == "files":
            self.call_after_refresh(self._focus_files_tree)

    def _focus_files_tree(self) -> None:
        try:
            tree = self.file_changes.file_tree.query_one("#file-tree", Tree)
            tree.focus()
        except Exception:
            pass

    def watch_current_tab(self, old_tab: int, new_tab: int) -> None:
        self._refresh_tab_bindings()

    def _refresh_tab_bindings(self) -> None:
        self._bindings.key_to_bindings.clear()

        for binding in _COMMON_BINDINGS:
            self._bindings._add_binding(binding)

        tab_bindings = _PR_INFO_BINDINGS if self.current_tab == 0 else _FILES_BINDINGS
        for binding in tab_bindings:
            self._bindings._add_binding(binding)

        self.refresh_bindings()

    @on(PRStore.PRLoaded)
    def on_pr_loaded(self, event: PRStore.PRLoaded) -> None:
        self.header.update_from_pr(event.pr)

    @on(PRStore.ErrorOccurred)
    def on_store_error(self, event: PRStore.ErrorOccurred) -> None:
        self.notify(event.error, title="Error", severity="error")

    def _copy_branch(self, branch: str | None, *, label: str) -> None:
        if not branch:
            self.post_message(
                Flash(f"No {label} branch available", style="warning", duration=2.0)
            )
            return

        self.app.copy_to_clipboard(branch)
        self.post_message(
            Flash(
                f"Copied {label} branch: {branch}",
                style="success",
                duration=2.0,
            )
        )

    def _handle_branch_pick(self, selection: str | None) -> None:
        pr = self.store.state.pr
        if selection == "head":
            self._copy_branch(pr.head_ref if pr else None, label="head")
        elif selection == "base":
            self._copy_branch(pr.base_ref if pr else None, label="base")

    def action_copy_branch(self) -> None:
        pr = self.store.state.pr
        if pr is None or (not pr.head_ref and not pr.base_ref):
            self.post_message(
                Flash("No branches available", style="warning", duration=2.0)
            )
            return

        self.app.push_screen(
            BranchPickerScreen(head=pr.head_ref, base=pr.base_ref),
            self._handle_branch_pick,
        )

    def action_cursor_down(self) -> None:
        if self.current_tab == 0:
            self.pr_info.next_item()

    def action_cursor_up(self) -> None:
        if self.current_tab == 0:
            self.pr_info.prev_item()

    def action_toggle_item(self) -> None:
        if self.current_tab == 0:
            self.pr_info.toggle_current()

    def action_next_comment(self) -> None:
        if self.current_tab == 0:
            self.pr_info.next_comment()
        elif self.current_tab == 1:
            dv = self.file_changes.diff_view
            dv.focus()
            dv.action_next_comment()

    def action_prev_comment(self) -> None:
        if self.current_tab == 0:
            self.pr_info.prev_comment()
        elif self.current_tab == 1:
            dv = self.file_changes.diff_view
            dv.focus()
            dv.action_prev_comment()

    def action_scroll_half_page_down(self) -> None:
        if self.current_tab == 0:
            scroll = self.pr_info.query_one("#main-scroll", VerticalScroll)
            scroll.scroll_relative(
                y=scroll.size.height // 2,
                duration=0.2,
                easing="out_cubic",
                on_complete=self.pr_info.select_first_visible_item,
            )

    def action_scroll_half_page_up(self) -> None:
        if self.current_tab == 0:
            scroll = self.pr_info.query_one("#main-scroll", VerticalScroll)
            scroll.scroll_relative(
                y=-(scroll.size.height // 2),
                duration=0.2,
                easing="out_cubic",
                on_complete=self.pr_info.select_first_visible_item,
            )

    def action_scroll_to_top(self) -> None:
        if self.current_tab == 0:
            scroll = self.pr_info.query_one("#main-scroll", VerticalScroll)
            scroll.scroll_home(animate=False)
            self.pr_info.select_first_item()

    def action_scroll_to_bottom(self) -> None:
        if self.current_tab == 0:
            scroll = self.pr_info.query_one("#main-scroll", VerticalScroll)
            scroll.scroll_end(animate=False)
            self.pr_info.select_last_item()

    def action_toggle_resolve(self) -> None:
        if self.current_tab == 0:
            thread_info = self.pr_info.get_current_thread_info()
            if thread_info:
                self.run_worker(
                    self._toggle_resolve(),
                    exclusive=False,
                    name="_toggle_resolve",
                )
            else:
                self.notify("No thread selected", severity="warning")

    def action_comment(self) -> None:
        if self.store.state.pr is None:
            self.post_message(Flash("PR not loaded yet", style="warning", duration=2.0))
            return

        if self.current_tab == 0:
            self.pr_info.start_issue_comment()
            return

        if self.current_tab != 1:
            return

        self.run_worker(
            self.file_changes.diff_view.open_inline_comment_editor(),
            exclusive=False,
            name="_open_inline_comment_editor",
        )

    def action_review(self) -> None:
        if self.current_tab not in {0, 1} or self.store.state.pr is None:
            return
        self.app.push_screen(
            ReviewSubmitScreen(
                pending_comments_count=len(self.store.state.pending_review_comments),
                pending_comments=list(self.store.state.pending_review_comments),
                initial_body=self.store.state.pending_review_body,
            ),
            self._handle_review_submit,
        )

    def action_delete_pending_comment(self) -> None:
        if self.current_tab != 1:
            return
        self.run_worker(
            self._delete_pending_inline_comment(),
            exclusive=False,
            name="_delete_pending_inline_comment",
        )

    def _handle_review_submit(
        self,
        result: tuple[Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"], str] | None,
    ) -> None:
        if result is None:
            return
        event, body = result
        self.run_worker(
            self._submit_review(event, body),
            exclusive=False,
            name="_submit_review",
        )

    @on(InlineCommentEditor.Submitted)
    def on_inline_comment_submitted(
        self,
        event: InlineCommentEditor.Submitted,
    ) -> None:
        event.stop()
        if event.kind == "issue":
            self.pr_info.close_issue_comment()
            self.run_worker(
                self._submit_issue_comment(event.body),
                exclusive=False,
                name="_submit_issue_comment",
            )
            return

        target = self.file_changes.diff_view.inline_comment_target()
        if target is None:
            self.post_message(
                Flash("No diff line selected", style="warning", duration=2.0)
            )
            return

        path, line, side = target
        self.run_worker(
            self._save_inline_comment_draft(
                event.body, path=path, line=line, side=side
            ),
            exclusive=False,
            name="_save_inline_comment_draft",
        )

    @on(InlineCommentEditor.Cancelled)
    def on_inline_comment_cancelled(
        self,
        event: InlineCommentEditor.Cancelled,
    ) -> None:
        event.stop()
        if event.kind == "issue":
            self.pr_info.close_issue_comment()
            return
        self.run_worker(
            self.file_changes.diff_view.close_inline_comment_editor(),
            exclusive=False,
            name="_close_inline_comment_editor",
        )

    async def _toggle_resolve(self) -> tuple[bool, bool]:
        return await self.pr_info.toggle_resolve()

    async def _submit_issue_comment(self, body: str) -> bool:
        await self.store.submit_issue_comment(body)
        self.pr_info.refresh_comments()
        return True

    async def _submit_review(
        self,
        event: Literal["APPROVE", "COMMENT", "REQUEST_CHANGES"],
        body: str,
    ) -> bool:
        diff_view = self.file_changes.diff_view
        current_file = diff_view.current_file
        current_line = diff_view.cursor_line
        current_pane = diff_view.active_pane

        await self.store.submit_review(event, body)
        await self.store.refresh_review_data()
        self.pr_info.refresh_pr_data()
        self.pr_info.refresh_comments()
        self.file_changes.file_tree.refresh_files()

        if current_file is not None:
            diff = self.store.get_file_diff(current_file)
            if diff is not None:
                await diff_view.show_diff(current_file, diff)
                if 0 <= current_line < len(diff_view._all_lines):
                    diff_view._move_cursor(line=current_line, pane=current_pane)
        return True

    async def _save_inline_comment_draft(
        self,
        body: str,
        *,
        path: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
    ) -> bool:
        diff_view = self.file_changes.diff_view
        current_file = diff_view.current_file
        current_line = diff_view.cursor_line
        current_pane = diff_view.active_pane

        await diff_view.close_inline_comment_editor()
        await self.store.upsert_pending_inline_comment(
            body,
            path=path,
            line=line,
            side=side,
        )
        self.file_changes.file_tree.refresh_files()

        if current_file is None:
            return True

        diff = self.store.get_file_diff(current_file)
        if diff is None:
            return True

        await diff_view.show_diff(current_file, diff)
        if 0 <= current_line < len(diff_view._all_lines):
            diff_view._move_cursor(line=current_line, pane=current_pane)
        diff_view.focus()
        return True

    async def _delete_pending_inline_comment(self) -> bool:
        diff_view = self.file_changes.diff_view
        current_file = diff_view.current_file
        current_line = diff_view.cursor_line
        current_pane = diff_view.active_pane
        target = diff_view._inline_comment_target_for_current_line()
        if current_file is None or target is None:
            return False

        path, line, side = target
        deleted = await self.store.remove_pending_inline_comment(
            path=path,
            line=line,
            side=side,
        )
        if not deleted:
            return False

        self.file_changes.file_tree.refresh_files()
        diff = self.store.get_file_diff(current_file)
        if diff is None:
            return True

        await diff_view.show_diff(current_file, diff)
        if 0 <= current_line < len(diff_view._all_lines):
            diff_view._move_cursor(line=current_line, pane=current_pane)
        diff_view.focus()
        return True

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if (
            event.worker.name == "_toggle_resolve"
            and event.state == WorkerState.SUCCESS
        ):
            result = event.worker.result
            if result is not None:
                success, _ = result
                if not success:
                    self.notify("Failed to toggle resolve status", severity="error")
            return

        if event.worker.name == "_submit_issue_comment":
            if event.state == WorkerState.SUCCESS:
                self.post_message(
                    Flash("Comment posted", style="success", duration=2.0)
                )
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to post comment", severity="error")
            return

        if event.worker.name == "_submit_review":
            if event.state == WorkerState.SUCCESS:
                self.post_message(
                    Flash("Review submitted", style="success", duration=2.0)
                )
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to submit review", severity="error")
            return

        if event.worker.name == "_save_inline_comment_draft":
            if event.state == WorkerState.SUCCESS:
                self.post_message(Flash("Draft saved", style="success", duration=2.0))
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to save draft", severity="error")
            return

        if event.worker.name == "_delete_pending_inline_comment":
            if event.state == WorkerState.SUCCESS:
                if event.worker.result:
                    self.post_message(
                        Flash("Draft deleted", style="success", duration=2.0)
                    )
                else:
                    self.post_message(
                        Flash("No draft on this line", style="warning", duration=2.0)
                    )
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to delete draft", severity="error")
            return

        if event.worker.name == "_open_inline_comment_editor":
            if event.state == WorkerState.SUCCESS and event.worker.result is False:
                self.post_message(
                    Flash("No diff line selected", style="warning", duration=2.0)
                )
            return

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, (Input, TextArea)):
            return
        if event.key == "tab":
            self.next_tab()
            event.stop()
            event.prevent_default()
            return
        if event.key == "shift+tab":
            self.prev_tab()
            event.stop()
            event.prevent_default()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {
            "next_tab",
            "prev_tab",
            "focus_left",
            "focus_right",
        } and isinstance(self.focused, (Input, TextArea)):
            return False
        return super().check_action(action, parameters)

    def _current_files_focus_target(self) -> str | None:
        if self.current_tab != 1:
            return None

        diff_view = self.file_changes.diff_view
        if diff_view.has_focus:
            return diff_view.active_pane if diff_view.split else "diff"

        try:
            tree = self.file_changes.file_tree.query_one("#file-tree", Tree)
        except Exception:
            return None
        return "tree" if tree.has_focus else None

    def action_focus_left(self) -> None:
        target = self._current_files_focus_target()
        if target is None:
            return

        diff_view = self.file_changes.diff_view
        if target == "new":
            diff_view.active_pane = "old"
            diff_view.focus()
            return

        if target in {"old", "diff"} and self.file_changes.file_tree.display:
            self._focus_files_tree()

    def action_focus_right(self) -> None:
        target = self._current_files_focus_target()
        if target is None:
            return

        diff_view = self.file_changes.diff_view
        if target == "tree":
            if diff_view.split:
                diff_view.active_pane = "old"
            diff_view.focus()
            return

        if target == "old" and diff_view.split:
            diff_view.active_pane = "new"
            diff_view.focus()

    def action_open_file_in_editor(self) -> None:
        if self.current_tab != 1:
            return

        filename = (
            self.file_changes.diff_view.current_file or self.store.state.selected_file
        )
        if not filename:
            self.post_message(Flash("No file selected", style="warning", duration=2.0))
            return

        repo_root = _resolve_repo_root()
        if repo_root is None:
            self.post_message(
                Flash(
                    "Could not determine local repository path",
                    style="warning",
                    duration=3.0,
                )
            )
            return

        path = repo_root / filename
        if not path.exists():
            self.post_message(
                Flash(
                    f"Local file not found: {filename}", style="warning", duration=3.0
                )
            )
            return

        current_line = self.file_changes.diff_view._current_line()
        line = current_line.new_line_no if current_line else None
        if line is None and current_line is not None:
            line = current_line.old_line_no
        column = self.file_changes.diff_view.cursor_column + 1

        try:
            _open_in_parent_nvim(
                path,
                line=line or 1,
                column=max(1, column),
            )
        except RuntimeError:
            self.post_message(
                Flash(
                    "Open in editor requires running rit inside Neovim terminal",
                    style="warning",
                    duration=3.0,
                )
            )
            return
        except _SUBPROCESS_ERRORS:
            self.post_message(
                Flash("Failed to open file in Neovim", style="error", duration=3.0)
            )
            return

    def action_toggle_file_tree(self) -> None:
        if self.current_tab == 1:
            self.file_changes.toggle_file_tree()

    def action_prev_file(self) -> None:
        self.file_changes.select_prev_file()

    def action_next_file(self) -> None:
        self.file_changes.select_next_file()

    def action_next_hunk(self) -> None:
        self.file_changes.next_hunk()

    def action_prev_hunk(self) -> None:
        self.file_changes.prev_hunk()

    def action_toggle_resolve_file(self) -> None:
        if self.current_tab == 1:
            self.file_changes.diff_view.action_toggle_resolve()

    @on(DiffView.CrossFileComment)
    def on_cross_file_comment(self, event: DiffView.CrossFileComment) -> None:
        event.stop()
        self._navigate_to_file_with_comments(event.direction)

    def _navigate_to_file_with_comments(self, direction: int) -> None:
        """Find and switch to the next file with review threads.

        Sets ``_pending_comment_jump`` on the DiffView so the jump executes
        after ``show_diff`` completes in ``_finalize_render_state``.  If the
        target file turns out to have no *visible* comment lines (threads
        exist but don't map to diff lines), the DiffView automatically
        re-posts ``CrossFileComment`` to continue the search.
        """
        store = self.store
        files = store.state.files
        if not files:
            return

        current = self.file_changes.diff_view.current_file
        current_idx = next(
            (i for i, f in enumerate(files) if f.filename == current), -1
        )

        n = len(files)
        for offset in range(1, n + 1):
            idx = (current_idx + direction * offset) % n
            filename = files[idx].filename
            has_threads = any(
                t for t in store.state.review_threads if t.path == filename
            )
            if has_threads:
                diff = store.get_file_diff(filename)
                if diff:
                    dv = self.file_changes.diff_view
                    dv._pending_comment_jump = "first" if direction == 1 else "last"
                    self.file_changes._queue_file_render_request(
                        filename,
                        diff,
                        focus_diff=True,
                        sync_tree_selection=True,
                    )
                return

        self.app.post_message(
            Flash("No more files with comments", style="warning", duration=2.0)
        )

    def _apply_viewed_states(self) -> None:
        """Refresh tree and diff header after background viewed-state load."""
        self.file_changes.file_tree.refresh_files()
        self.file_changes.diff_view.refresh_header()

    def _resolve_file_view_target(self) -> str | None:
        diff_view = self.file_changes.diff_view
        if diff_view.has_focus and diff_view.current_file:
            return diff_view.current_file

        file_tree = self.file_changes.file_tree
        tree = file_tree.query_one("#file-tree", Tree)
        search = file_tree.query_one("#file-search", Input)
        if tree.has_focus or search.has_focus:
            node = tree.cursor_node
            if node is not None and node.data:
                return node.data

        return self.store.state.selected_file or diff_view.current_file

    def action_toggle_file_viewed(self) -> None:
        if self.current_tab != 1:
            return
        filename = self._resolve_file_view_target()
        if not filename:
            self.post_message(Flash("No file selected", style="warning", duration=2.0))
            return

        file = next((f for f in self.store.state.files if f.filename == filename), None)
        if file is None:
            return

        if self.store.state.pr is None:
            self.post_message(Flash("PR not loaded yet", style="warning", duration=2.0))
            return

        old_state = file.viewer_viewed_state
        new_state = (
            FileViewedState.UNVIEWED
            if old_state == FileViewedState.VIEWED
            else FileViewedState.VIEWED
        )

        file.viewer_viewed_state = new_state
        self.file_changes.update_file_view_state(filename)

        self.run_worker(
            self._sync_file_viewed(filename, old_state, new_state),
            exclusive=False,
            name="sync-file-viewed",
        )

    async def _sync_file_viewed(
        self,
        filename: str,
        old_state: FileViewedState,
        new_state: FileViewedState,
    ) -> None:
        try:
            await self.store.set_file_viewed(
                filename, viewed=new_state == FileViewedState.VIEWED
            )
            label = "Viewed" if new_state == FileViewedState.VIEWED else "Unviewed"
            self.post_message(Flash(f"Marked {label}", style="success", duration=1.5))
        except Exception:
            file = next(
                (f for f in self.store.state.files if f.filename == filename),
                None,
            )
            if file:
                file.viewer_viewed_state = old_state
            self.file_changes.update_file_view_state(filename)
            self.post_message(
                Flash(
                    "Failed to update viewed state",
                    style="error",
                    duration=3.0,
                )
            )
