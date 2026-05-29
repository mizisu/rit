from __future__ import annotations

from typing import Literal, cast

from textual import events, getters, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Input, TabbedContent, TabPane, TextArea, Tree
from textual.worker import Worker, WorkerState

from rit.app import RitApp
from rit.state.models import FileViewedState, PRTeam, PRUser
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges
from rit.ui.components.pr_info import PRInfo
from rit.ui.messages import Flash
from rit.ui.screens.branch_picker import BranchPickerScreen
from rit.ui.screens.multi_select_picker import (
    MultiSelectItem,
    MultiSelectPickerScreen,
    MultiSelectResult,
)
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
    Binding(
        "R",
        "edit_reviewers",
        "Reviewers",
        key_display="shift+r",
        group=_REVIEW_GROUP,
    ),
    Binding("a", "edit_assignees", "Assignees", group=_REVIEW_GROUP),
    Binding("r", "toggle_resolve", "Resolve", tooltip="Toggle thread resolution"),
]

_FILES_BINDINGS = [
    Binding(
        "ctrl+h",
        "focus_left",
        "Move Focus",
        key_display="ctrl+h/l",
        group=_NAVIGATION_GROUP,
    ),
    Binding("H", "focus_left", "", group=_NAVIGATION_GROUP, show=False),
    Binding("c", "comment", "Comment", group=_COMMENT_GROUP),
    Binding("d", "delete_pending_comment", "Delete Draft", group=_COMMENT_GROUP),
    Binding("S", "review", "Review", group=_REVIEW_GROUP),
    Binding("ctrl+l", "focus_right", "", group=_NAVIGATION_GROUP, show=False),
    Binding("L", "focus_right", "", group=_NAVIGATION_GROUP, show=False),
    Binding("e", "focus_file_tree", "File Tree", group=_NAVIGATION_GROUP),
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


class MainScreen(Screen[None]):
    BINDINGS = _COMMON_BINDINGS + _PR_INFO_BINDINGS

    current_tab: reactive[int] = reactive(0)

    header = getters.query_one(Header)
    pr_info = getters.query_one(PRInfo)
    file_changes = getters.query_one(FileChanges)
    tabbed_content = getters.query_one(TabbedContent)

    app = getters.app(RitApp)

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
        self.store.set_message_sink(self._post_store_message)
        self._pr_info_refresh_pending = False

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
        await self.store.load_file_view_states()
        self._apply_viewed_states()

    def _post_store_message(self, message) -> None:
        if isinstance(message, PRStore.FileSelected):
            return
        self.post_message(message)

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

        if tab_id == "pr-info":
            self._refresh_pending_pr_info()
        elif tab_id == "files":
            if self.pr_info.cancel_comment_refresh():
                self._pr_info_refresh_pending = True
            self.call_after_refresh(
                lambda: self._focus_files_tree(preserve_existing_focus=True)
            )

    def _focus_files_tree(self, *, preserve_existing_focus: bool = False) -> None:
        if self.current_tab != 1:
            return
        if preserve_existing_focus and self._current_files_focus_target() is not None:
            return

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
        self.pr_info.refresh_summary()

    @on(PRStore.PRDiscussionLoaded)
    def on_pr_discussion_loaded(self, _event: PRStore.PRDiscussionLoaded) -> None:
        self.file_changes.file_tree.refresh_files()
        self.run_worker(
            self._refresh_current_diff_after_discussion(),
            exclusive=False,
            name="_refresh_current_diff_after_discussion",
        )
        self._pr_info_refresh_pending = True
        if self.current_tab == 0:
            self._refresh_pending_pr_info()

    def _refresh_pending_pr_info(self) -> None:
        if not self._pr_info_refresh_pending:
            return

        self._pr_info_refresh_pending = False
        self.pr_info.refresh_pr_data()
        self.pr_info.refresh_comments()

    @on(PRStore.FilesLoaded)
    def on_files_loaded(self, _event: PRStore.FilesLoaded) -> None:
        self.file_changes.refresh_files()

    @on(PRStore.ErrorOccurred)
    def on_store_error(self, event: PRStore.ErrorOccurred) -> None:
        self.notify(event.error, title="Error", severity="error", markup=False)

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

    def action_edit_reviewers(self) -> None:
        if self.current_tab != 0:
            return
        if self.store.state.pr is None:
            self.post_message(Flash("PR not loaded yet", style="warning", duration=2.0))
            return
        self.run_worker(
            self.store.get_reviewer_candidates(),
            exclusive=False,
            name="_load_reviewer_candidates",
        )

    def action_edit_assignees(self) -> None:
        if self.current_tab != 0:
            return
        if self.store.state.pr is None:
            self.post_message(Flash("PR not loaded yet", style="warning", duration=2.0))
            return
        self.run_worker(
            self.store.get_assignee_candidates(),
            exclusive=False,
            name="_load_assignee_candidates",
        )

    def _show_reviewer_picker(self, users: list[PRUser], teams: list[PRTeam]) -> None:
        pr = self.store.state.pr
        if pr is None:
            return

        items_by_key: dict[str, MultiSelectItem] = {}
        author_login = pr.user.login if pr.user else ""
        for user in users:
            if user.login and user.login != author_login:
                key = f"user:{user.login}"
                items_by_key[key] = MultiSelectItem(
                    key=key,
                    label=f"@{user.login}",
                    search_text=f"user {user.login}",
                )
        for team in teams:
            team_key = team.slug or team.name
            if team_key:
                label_name = team.name or team.slug
                key = f"team:{team_key}"
                items_by_key[key] = MultiSelectItem(
                    key=key,
                    label=label_name,
                    search_text=f"team {team.name} {team.slug}",
                )

        selected_keys: set[str] = set()
        for request in pr.requested_reviewers:
            reviewer = request.requested_reviewer
            if isinstance(reviewer, PRUser) and reviewer.login:
                key = f"user:{reviewer.login}"
                selected_keys.add(key)
                items_by_key.setdefault(
                    key,
                    MultiSelectItem(
                        key=key,
                        label=f"@{reviewer.login}",
                        search_text=f"user {reviewer.login}",
                    ),
                )
            elif isinstance(reviewer, PRTeam):
                team_key = reviewer.slug or reviewer.name
                if team_key:
                    key = f"team:{team_key}"
                    selected_keys.add(key)
                    label_name = reviewer.name or reviewer.slug
                    items_by_key.setdefault(
                        key,
                        MultiSelectItem(
                            key=key,
                            label=label_name,
                            search_text=f"team {reviewer.name} {reviewer.slug}",
                        ),
                    )

        self.app.push_screen(
            MultiSelectPickerScreen(
                title="Edit requested reviewers",
                items=sorted(items_by_key.values(), key=lambda item: item.label),
                selected_keys=selected_keys,
                placeholder="Filter reviewers or teams...",
                empty_label="No reviewer candidates",
            ),
            self._handle_reviewer_picker,
        )

    def _show_assignee_picker(self, users: list[PRUser]) -> None:
        pr = self.store.state.pr
        if pr is None:
            return

        items_by_key: dict[str, MultiSelectItem] = {}
        for user in users:
            if user.login:
                key = f"user:{user.login}"
                items_by_key[key] = MultiSelectItem(
                    key=key,
                    label=f"@{user.login}",
                    search_text=user.login,
                )

        selected_keys: set[str] = set()
        for user in pr.assignees:
            if user.login:
                key = f"user:{user.login}"
                selected_keys.add(key)
                items_by_key.setdefault(
                    key,
                    MultiSelectItem(
                        key=key,
                        label=f"@{user.login}",
                        search_text=user.login,
                    ),
                )

        self.app.push_screen(
            MultiSelectPickerScreen(
                title="Edit assignees",
                items=sorted(items_by_key.values(), key=lambda item: item.label),
                selected_keys=selected_keys,
                placeholder="Filter assignees...",
                empty_label="No assignee candidates",
            ),
            self._handle_assignee_picker,
        )

    def _handle_reviewer_picker(self, result: MultiSelectResult | None) -> None:
        if result is None:
            return
        users = []
        teams = []
        for key in result.selected_keys:
            kind, _, value = key.partition(":")
            if kind == "user" and value:
                users.append(value)
            elif kind == "team" and value:
                teams.append(value)
        self.run_worker(
            self._set_requested_reviewers(users=users, teams=teams),
            exclusive=False,
            name="_set_requested_reviewers",
        )

    def _handle_assignee_picker(self, result: MultiSelectResult | None) -> None:
        if result is None:
            return
        logins = [
            value
            for key in result.selected_keys
            for kind, _, value in [key.partition(":")]
            if kind == "user" and value
        ]
        self.run_worker(
            self._set_assignees(logins),
            exclusive=False,
            name="_set_assignees",
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
        if event.mode == "post":
            self.run_worker(
                self._post_inline_comment(
                    event.body, path=path, line=line, side=side
                ),
                exclusive=False,
                name="_post_inline_comment",
            )
            return
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

    async def _refresh_current_diff_after_discussion(self) -> None:
        diff_view = self.file_changes.diff_view
        current_file = diff_view.current_file
        if current_file is None:
            return

        diff = self.store.get_file_diff(current_file)
        if diff is None:
            return

        current_line = diff_view.cursor_line
        current_pane = diff_view.active_pane
        await diff_view.show_diff(current_file, diff)
        if 0 <= current_line < len(diff_view._all_lines):
            diff_view._move_cursor(line=current_line, pane=current_pane)

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

    async def _set_requested_reviewers(
        self,
        *,
        users: list[str],
        teams: list[str],
    ) -> bool:
        changed = await self.store.set_requested_reviewers(users=users, teams=teams)
        self.pr_info.refresh_reviewers()
        return changed

    async def _set_assignees(self, logins: list[str]) -> bool:
        changed = await self.store.set_assignees(logins)
        self.pr_info.refresh_summary()
        return changed

    async def _post_inline_comment(
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
        await self.store.submit_inline_comment(
            body,
            path=path,
            line=line,
            side=side,
        )
        await self.store.remove_pending_inline_comment(
            path=path,
            line=line,
            side=side,
        )
        await self.store.refresh_review_data()
        self.pr_info.refresh_comments()
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

        if event.worker.name == "_load_reviewer_candidates":
            if event.state == WorkerState.SUCCESS:
                users, teams = cast(
                    tuple[list[PRUser], list[PRTeam]], event.worker.result
                )
                self._show_reviewer_picker(users, teams)
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to load reviewer candidates", severity="error")
            return

        if event.worker.name == "_load_assignee_candidates":
            if event.state == WorkerState.SUCCESS:
                users = cast(list[PRUser], event.worker.result)
                self._show_assignee_picker(users)
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to load assignee candidates", severity="error")
            return

        if event.worker.name == "_set_requested_reviewers":
            if event.state == WorkerState.SUCCESS:
                if event.worker.result:
                    self.post_message(
                        Flash("Reviewers updated", style="success", duration=2.0)
                    )
                else:
                    self.post_message(
                        Flash("Reviewers unchanged", style="warning", duration=2.0)
                    )
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to update reviewers", severity="error")
            return

        if event.worker.name == "_set_assignees":
            if event.state == WorkerState.SUCCESS:
                if event.worker.result:
                    self.post_message(
                        Flash("Assignees updated", style="success", duration=2.0)
                    )
                else:
                    self.post_message(
                        Flash("Assignees unchanged", style="warning", duration=2.0)
                    )
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to update assignees", severity="error")
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

        if event.worker.name == "_post_inline_comment":
            if event.state == WorkerState.SUCCESS:
                self.post_message(
                    Flash("Comment posted", style="success", duration=2.0)
                )
            elif event.state == WorkerState.ERROR:
                self.notify("Failed to post comment", severity="error")
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
        if self._text_entry_has_focus():
            return
        if self.current_tab == 1 and event.key in {"ctrl+h", "ctrl+l", "H", "L"}:
            if event.key in {"ctrl+h", "H"}:
                self.action_focus_left()
            else:
                self.action_focus_right()
            event.stop()
            event.prevent_default()
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
        if self._text_entry_has_focus():
            return False
        return super().check_action(action, parameters)

    def _text_entry_has_focus(self) -> bool:
        return isinstance(self.focused, (Input, TextArea))

    def _current_files_focus_target(self) -> str | None:
        if self.current_tab != 1:
            return None

        diff_view = self.file_changes.diff_view
        if diff_view.has_focus_within:
            return diff_view.active_pane if diff_view.split else "diff"

        try:
            self.file_changes.file_tree.query_one("#file-tree", Tree)
        except Exception:
            return None
        return "tree" if self.file_changes.file_tree.has_focus_within else None

    def action_focus_left(self) -> None:
        target = self._current_files_focus_target()
        if target is None:
            if self.current_tab == 1 and self.file_changes.file_tree.display:
                self._focus_files_tree()
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
            if self.current_tab == 1:
                self.file_changes.diff_view.focus()
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

    def action_focus_file_tree(self) -> None:
        if self.current_tab != 1:
            return
        if not self.file_changes.file_tree.display:
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
