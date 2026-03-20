from __future__ import annotations

from dataclasses import dataclass

from textual import getters, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Static, TabbedContent, TabPane, Tree
from textual.worker import Worker, WorkerState

from rit.app import RitApp
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges
from rit.ui.components.pr_info import PRInfo
from rit.ui.widgets import DiffView, Header


_NAVIGATION_GROUP = Binding.Group("Navigation", compact=True)
_COMMENT_GROUP = Binding.Group("Comments", compact=True)
_FILE_GROUP = Binding.Group("Files", compact=True)
_TAB_GROUP = Binding.Group("Move Tab", compact=True)

_TAB_IDS = ("pr-info", "files")
_TAB_INDEX_BY_ID = {tab_id: index for index, tab_id in enumerate(_TAB_IDS)}

_COMMON_BINDINGS = [
    Binding("ctrl+d", "scroll_half_page_down", "Half Page Down", show=False),
    Binding("ctrl+u", "scroll_half_page_up", "Half Page Up", show=False),
    Binding("g", "scroll_to_top", "Top", show=False),
    Binding("G", "scroll_to_bottom", "Bottom", key_display="shift+g", show=False),
    Binding("enter", "toggle_item", "Toggle", show=False),
    Binding("space", "toggle_item", "Toggle", show=False),
]


_PR_INFO_BINDINGS = [
    Binding("j", "cursor_down", "", group=_NAVIGATION_GROUP),
    Binding("k", "cursor_up", "", group=_NAVIGATION_GROUP),
    Binding(
        "J", "next_comment", "Comments", key_display="shift+j/k", group=_COMMENT_GROUP
    ),
    Binding("K", "prev_comment", "", group=_COMMENT_GROUP, show=False),
    Binding("r", "toggle_resolve", "Resolve", tooltip="Toggle thread resolution"),
    Binding("L", "next_tab", "Move Tab", key_display="shift+l/h", group=_TAB_GROUP),
    Binding("H", "prev_tab", "", group=_TAB_GROUP, show=False),
]

_FILES_BINDINGS = [
    Binding("tab", "switch_pane", "Next Pane", group=_NAVIGATION_GROUP),
    Binding(
        "shift+tab", "switch_pane", "Prev Pane", group=_NAVIGATION_GROUP, show=False
    ),
    Binding("e", "focus_file_tree", "Explorer", group=_NAVIGATION_GROUP),
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
    Binding("L", "next_tab", "Move Tab", key_display="shift+l/h", group=_TAB_GROUP),
    Binding("H", "prev_tab", "", group=_TAB_GROUP, show=False),
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
                self._toggle_resolve()
            else:
                self.notify("No thread selected", severity="warning")

    @work
    async def _toggle_resolve(self) -> tuple[bool, bool]:
        return await self.pr_info.toggle_resolve()

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

    def action_switch_pane(self) -> None:
        if self.current_tab == 1:
            try:
                tree = self.query_one("#file-tree")
                if tree.has_focus:
                    self.query_one("#diff-view-main").focus()
                else:
                    tree.focus()
            except Exception:
                pass

    def action_focus_file_tree(self) -> None:
        if self.current_tab == 1:
            try:
                tree = self.file_changes.file_tree.query_one("#file-tree")
                if tree.has_focus:
                    self.file_changes.diff_view.focus()
                    return
            except Exception:
                pass
            self.file_changes.show_file_tree()
            self._focus_files_tree()

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

        from rit.ui.messages import Flash

        self.app.post_message(
            Flash("No more files with comments", style="warning", duration=2.0)
        )
