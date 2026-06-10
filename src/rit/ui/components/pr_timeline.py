"""PR Timeline: description, comments, reviews, and thread navigation."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.worker import Worker, WorkerState

from textual.widget import Widget
from textual.widgets import Collapsible

from rit.core.datetime_utils import (
    datetime_min_utc,
    datetime_sort_key,
    is_min_datetime,
)
from rit.state.models import (
    PR,
    PRReview,
    PRIssueComment,
    ReviewState,
    CommentThread,
    group_comments_into_threads,
)
from rit.ui.icons import get_file_icon
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.comment_editor import InlineCommentEditor
from rit.ui.widgets.review_thread_card import ReviewThreadItem

if TYPE_CHECKING:
    from rit.state.store import PRStore

MOUNT_BATCH_SIZE = 1
QUEUED_REFRESH_DELAY = 0.05
TIMELINE_BODY_MOUNT_DELAY = 0.85
TIMELINE_BODY_MOUNT_STAGGER_DELAY = 0.08
INITIAL_TIMELINE_BODY_COUNT = 3


class PRTimeline(Vertical):
    DEFAULT_CSS = """
    PRTimeline {
        height: auto;
    }

    PRTimeline MarkdownH1 {
        content-align: left middle;
    }
    """

    @dataclass
    class ResolveToggled(Message):
        thread_id: str
        root_comment_id: int
        is_resolved: bool  # New state after toggle

    def __init__(self, store: "PRStore", id: str | None = None) -> None:
        super().__init__(id=id)
        self.store = store
        self._navigable_items: list[Widget] = []
        self._current_index: int = -1  # -1 means no selection
        self._navigable_items_valid: bool = False  # Cache validity flag
        self._thread_widget_info: dict[Widget, tuple[str, int, bool]] = {}
        self._pending_resolve_threads: set[str] = set()
        self._scroll_container: VerticalScroll | None = None
        self._refresh_worker: Worker[None] | None = None
        self._refresh_requested: bool = False
        self._queued_refresh_pending: bool = False
        self._preserve_initial_scroll_home: bool = True

    def compose(self) -> ComposeResult:
        yield CommentCard(
            "[#6e738d]Loading PR details[/]",
            "Fetching title and description...",
            id="pr-description-card",
            classes="description-container timeline-loading",
            header_id="pr-author-header",
            content_id="pr-description",
        )

        with Vertical(id="comments-container"):
            yield from self._compose_loading_cards()
        yield InlineCommentEditor(
            kind="issue",
            title="Add comment",
            placeholder="Write a PR-level comment...",
            id="issue-comment-editor",
        )

    def _compose_loading_cards(self) -> ComposeResult:
        yield CommentCard(
            "[#6e738d]Loading discussion[/]",
            "Fetching comments and reviews...",
            classes="timeline-loading",
        )
        yield CommentCard(
            "[#6e738d]Preparing review threads[/]",
            "Resolving inline conversations...",
            classes="timeline-loading",
        )

    def on_mount(self) -> None:
        try:
            self._scroll_container = self.query_ancestor(VerticalScroll)
        except Exception:
            pass

        self.call_after_refresh(self._select_first_item)

    def set_scroll_container(self, container: VerticalScroll) -> None:
        self._scroll_container = container

    def start_issue_comment(self) -> None:
        self.query_one("#issue-comment-editor", InlineCommentEditor).open()

    def close_issue_comment(self) -> None:
        self.query_one("#issue-comment-editor", InlineCommentEditor).close()

    def refresh_description(self, pr: PR | None) -> None:
        if not pr:
            return

        author_name = pr.user.login if pr.user else "unknown"
        description_card = self.query_one("#pr-description-card", CommentCard)
        description_card.remove_class("timeline-loading")
        description_card.set_content(
            f"[bold]{author_name}[/] [#6e738d]opened this PR[/]",
            pr.body or "*No description provided.*",
            markdown_base_url=self._markdown_base_url(),
        )

    def refresh_timeline(self) -> None:
        if self._refresh_worker is not None and self._refresh_worker.state in {
            WorkerState.PENDING,
            WorkerState.RUNNING,
        }:
            self._refresh_requested = True
            return

        self._refresh_worker = self.run_worker(
            self._build_timeline_async(),
            exclusive=True,
            name="pr-timeline-refresh",
        )
        self._invalidate_navigable_items()

    def cancel_refresh(self) -> bool:
        worker = self._refresh_worker
        self._refresh_requested = False
        self._queued_refresh_pending = False
        if worker is None or worker.state not in {
            WorkerState.PENDING,
            WorkerState.RUNNING,
        }:
            return False
        worker.cancel()
        return True

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is self._refresh_worker and event.state in {
            WorkerState.CANCELLED,
            WorkerState.ERROR,
            WorkerState.SUCCESS,
        }:
            should_refresh_again = (
                self._refresh_requested and event.state == WorkerState.SUCCESS
            )
            self._refresh_worker = None
            self._refresh_requested = False
            if should_refresh_again:
                self._queued_refresh_pending = True
                self.set_timer(QUEUED_REFRESH_DELAY, self._run_queued_refresh)

    def _run_queued_refresh(self) -> None:
        if not self._queued_refresh_pending:
            return
        self._queued_refresh_pending = False
        self.refresh_timeline()

    async def _build_timeline_async(self) -> None:
        state = self.store.state

        container = self.query_one("#comments-container", Vertical)

        await container.remove_children()
        self._thread_widget_info.clear()

        threads = group_comments_into_threads(state.comments)
        threads_by_review: dict[int, list[CommentThread]] = {}
        orphan_threads: list[CommentThread] = []

        for thread in threads:
            if not thread.root_comment.body or not thread.root_comment.body.strip():
                continue
            review_id = thread.root_comment.pull_request_review_id
            if review_id:
                threads_by_review.setdefault(review_id, []).append(thread)
            else:
                orphan_threads.append(thread)

        timeline_items: list[tuple[datetime, str, Any, Any]] = []

        for comment in state.issue_comments:
            if comment.body and comment.body.strip():
                timeline_items.append(
                    (comment.created_at, "issue_comment", comment, None)
                )

        for review in state.reviews:
            review_threads = threads_by_review.get(review.id, [])
            if (review.body and review.body.strip()) or review_threads:
                submitted_at = review.submitted_at or datetime_min_utc()
                timeline_items.append((submitted_at, "review", review, review_threads))

        for thread in orphan_threads:
            timeline_items.append((thread.created_at, "thread", thread, None))

        timeline_items.sort(key=lambda x: datetime_sort_key(x[0]))

        if not timeline_items:
            return

        for i, (item_time, item_type, item, extra) in enumerate(timeline_items):
            body_mount_delay = self._body_mount_delay_for_index(i)
            if item_type == "issue_comment":
                self._mount_issue_comment(
                    container,
                    item,
                    body_mount_delay=body_mount_delay,
                )
            elif item_type == "review":
                self._mount_review_with_threads(
                    container,
                    item,
                    extra or [],
                    body_mount_delay=body_mount_delay,
                )
            elif item_type == "thread":
                self._mount_comment_thread(
                    container,
                    item,
                    body_mount_delay=body_mount_delay,
                )

            if (i + 1) % MOUNT_BATCH_SIZE == 0:
                await asyncio.sleep(0)

        self._invalidate_navigable_items()

    def _body_mount_delay_for_index(self, index: int) -> float:
        if index < INITIAL_TIMELINE_BODY_COUNT:
            return TIMELINE_BODY_MOUNT_DELAY
        return TIMELINE_BODY_MOUNT_DELAY + (
            index - INITIAL_TIMELINE_BODY_COUNT + 1
        ) * TIMELINE_BODY_MOUNT_STAGGER_DELAY

    def _mount_issue_comment(
        self,
        container: Vertical,
        comment: PRIssueComment,
        *,
        body_mount_delay: float = TIMELINE_BODY_MOUNT_DELAY,
    ) -> None:
        if not comment.body or not comment.body.strip():
            return

        time_str = self._format_time(comment.created_at)
        user_name = comment.user.login if comment.user else "unknown"
        header_text = f"[bold]{user_name}[/] [#6e738d]commented {time_str}[/]"

        container.mount(
            CommentCard(
                header_text,
                comment.body,
                classes="comment-box",
                markdown_base_url=self._markdown_base_url(),
                body_mount_delay=body_mount_delay,
            )
        )

    def _mount_review(
        self,
        container: Vertical,
        review: PRReview,
        *,
        body_mount_delay: float = TIMELINE_BODY_MOUNT_DELAY,
    ) -> None:
        if not review.body or not review.body.strip():
            return

        time_str = self._format_time(review.submitted_at or datetime_min_utc())
        state_display = {
            ReviewState.APPROVED: "[#a6da95]approved[/]",
            ReviewState.CHANGES_REQUESTED: "[#ed8796]requested changes[/]",
            ReviewState.COMMENTED: "[#6e738d]reviewed[/]",
            ReviewState.PENDING: "[#eed49f]pending[/]",
            ReviewState.DISMISSED: "[#6e738d]dismissed[/]",
        }.get(review.state, "reviewed")

        user_name = review.user.login if review.user else "unknown"
        header_text = f"[bold]{user_name}[/] {state_display} {time_str}"

        container.mount(
            CommentCard(
                header_text,
                review.body,
                classes="comment-box",
                markdown_base_url=self._markdown_base_url(),
                body_mount_delay=body_mount_delay,
            )
        )

    def _mount_review_with_threads(
        self,
        container: Vertical,
        review: PRReview,
        threads: list[CommentThread],
        *,
        body_mount_delay: float = TIMELINE_BODY_MOUNT_DELAY,
    ) -> None:
        if review.body and review.body.strip():
            self._mount_review(
                container,
                review,
                body_mount_delay=body_mount_delay,
            )

        for thread in sorted(threads, key=lambda t: datetime_sort_key(t.created_at)):
            self._mount_comment_thread(
                container,
                thread,
                body_mount_delay=body_mount_delay,
            )

    def _mount_comment_thread(
        self,
        container: Vertical,
        thread: CommentThread,
        *,
        body_mount_delay: float = TIMELINE_BODY_MOUNT_DELAY,
    ) -> None:
        root = thread.root_comment

        thread_info = self.store.get_thread_info(root.id)
        is_resolved = thread_info.is_resolved if thread_info else False
        thread_id = thread_info.thread_id if thread_info else ""

        line_info = f":{root.anchor_line}" if root.anchor_line else ""
        file_icon = get_file_icon(root.path)

        if is_resolved:
            collapsible_title = f"✓ Resolved: {file_icon} {root.path}{line_info}"
            collapsible_classes = "--thread --resolved"
            collapsed = True
        else:
            collapsible_title = f"{file_icon} {root.path}{line_info}"
            collapsible_classes = "--thread"
            collapsed = False

        line_no = root.anchor_line
        collapsible = ReviewThreadItem(
            title=collapsible_title,
            path=root.path,
            line=line_no,
            comments=thread.all_comments,
            diff_hunk=root.diff_hunk,
            is_resolved=is_resolved,
            compact=False,
            show_diff_hunk=bool(root.diff_hunk),
            show_path_header=False,
            collapsed=collapsed,
            classes=collapsible_classes,
            markdown_base_url=self._markdown_base_url(),
            body_mount_delay=body_mount_delay,
        )
        container.mount(collapsible)

        self._thread_widget_info[collapsible] = (thread_id, root.id, is_resolved)

    def _markdown_base_url(self) -> str | None:
        pr = self.store.state.pr
        if pr and pr.html_url:
            return pr.html_url

        service = getattr(self.store, "_service", None)
        owner = getattr(service, "_owner", None)
        repo = getattr(service, "_repo", None)
        if owner and repo and self.store.pr_number:
            return f"https://github.com/{owner}/{repo}/pull/{self.store.pr_number}"
        return None

    def _select_first_item(self) -> None:
        self._collect_navigable_items()
        if self._navigable_items and self._current_index == -1:
            self._update_selection(0, scroll_to_view=False)

    def _is_visible_collapsible(self, collapsible: Collapsible) -> bool:
        for ancestor in collapsible.ancestors:
            if isinstance(ancestor, Collapsible):
                if ancestor.collapsed:
                    return False  # Parent is collapsed, so this is not visible
        return True

    def _invalidate_navigable_items(self) -> None:
        self._navigable_items_valid = False

    def _collect_navigable_items(self) -> None:
        if self._navigable_items_valid:
            return

        current_widget = None
        if 0 <= self._current_index < len(self._navigable_items):
            current_widget = self._navigable_items[self._current_index]

        self._navigable_items = []

        try:
            # Note: .thread-container is excluded because it's inside Collapsible
            # and returns y=0 when collapsed, causing sorting issues
            all_items = list(
                self.query(".description-container, .comment-box, Collapsible")
            )

            filtered_items: list[Widget] = [
                item
                for item in all_items
                if not isinstance(item, Collapsible)
                or self._is_visible_collapsible(item)
            ]

            def get_y_position(widget: Widget) -> int:
                try:
                    return widget.region.y
                except Exception:
                    return 0

            self._navigable_items = sorted(filtered_items, key=get_y_position)

            if current_widget and current_widget in self._navigable_items:
                self._current_index = self._navigable_items.index(current_widget)
            elif current_widget:
                self._current_index = -1

            self._navigable_items_valid = True
        except Exception:
            self._navigable_items = []
            self._current_index = -1
            self._navigable_items_valid = False

    def _update_selection(self, new_index: int, scroll_to_view: bool = True) -> None:
        if scroll_to_view:
            self._preserve_initial_scroll_home = False

        if 0 <= self._current_index < len(self._navigable_items):
            old_item = self._navigable_items[self._current_index]
            old_item.remove_class("--selected")

        self._current_index = new_index

        if 0 <= self._current_index < len(self._navigable_items):
            new_item = self._navigable_items[self._current_index]
            new_item.add_class("--selected")

            if scroll_to_view and self._scroll_container:
                try:
                    self._scroll_container.scroll_to_widget(new_item, animate=False)
                except Exception:
                    pass

    def next_item(self) -> None:
        self._collect_navigable_items()

        if not self._navigable_items:
            return

        if self._current_index < len(self._navigable_items) - 1:
            self._update_selection(self._current_index + 1)
        elif self._current_index == -1 and self._navigable_items:
            self._update_selection(0)

    def prev_item(self) -> None:
        self._collect_navigable_items()

        if not self._navigable_items:
            return

        if self._current_index > 0:
            self._update_selection(self._current_index - 1)
        elif self._current_index == -1 and self._navigable_items:
            self._update_selection(len(self._navigable_items) - 1)

    def toggle_current(self) -> None:
        self._preserve_initial_scroll_home = False
        if not (0 <= self._current_index < len(self._navigable_items)):
            return

        current_item = self._navigable_items[self._current_index]

        if isinstance(current_item, Collapsible):
            current_item.collapsed = not current_item.collapsed
            self._invalidate_navigable_items()

    def clear_selection(self) -> None:
        if 0 <= self._current_index < len(self._navigable_items):
            old_item = self._navigable_items[self._current_index]
            old_item.remove_class("--selected")
        self._current_index = -1

    @property
    def has_selection(self) -> bool:
        return 0 <= self._current_index < len(self._navigable_items)

    @property
    def current_item(self) -> Widget | None:
        if 0 <= self._current_index < len(self._navigable_items):
            return self._navigable_items[self._current_index]
        return None

    def _is_unresolved_thread_or_comment(self, widget: Widget) -> bool:
        if widget in self._thread_widget_info:
            _, _, is_resolved = self._thread_widget_info[widget]
            if is_resolved:
                return False
            return True
        if isinstance(widget, Collapsible):
            return False
        return True

    def next_comment(self) -> None:
        self._collect_navigable_items()

        if not self._navigable_items:
            return

        start = self._current_index + 1 if self._current_index >= 0 else 0
        for i in range(start, len(self._navigable_items)):
            if self._is_unresolved_thread_or_comment(self._navigable_items[i]):
                self._update_selection(i)
                return

    def prev_comment(self) -> None:
        self._collect_navigable_items()

        if not self._navigable_items:
            return

        start = (
            self._current_index - 1
            if self._current_index >= 0
            else len(self._navigable_items) - 1
        )
        for i in range(start, -1, -1):
            if self._is_unresolved_thread_or_comment(self._navigable_items[i]):
                self._update_selection(i)
                return

    def select_first_item(self) -> None:
        self._collect_navigable_items()

        if self._navigable_items:
            self._update_selection(0)

    def select_last_item(self) -> None:
        self._collect_navigable_items()

        if self._navigable_items:
            self._update_selection(len(self._navigable_items) - 1)

    def select_first_visible_item(self) -> None:
        self._collect_navigable_items()

        if not self._navigable_items:
            return

        if not self._scroll_container:
            if self._navigable_items:
                self._update_selection(0, scroll_to_view=False)
            return

        try:
            scroll_y = self._scroll_container.scroll_offset.y
            viewport_height = self._scroll_container.size.height

            for i, item in enumerate(self._navigable_items):
                item_y = item.region.y
                item_height = item.region.height

                item_bottom = item_y + item_height
                viewport_bottom = scroll_y + viewport_height

                if item_bottom > scroll_y and item_y < viewport_bottom:
                    self._update_selection(i, scroll_to_view=False)
                    return

            for i, item in enumerate(self._navigable_items):
                if item.region.y >= scroll_y:
                    self._update_selection(i, scroll_to_view=False)
                    return

        except Exception:
            if self._navigable_items:
                self._update_selection(0, scroll_to_view=False)

    def get_current_thread_info(self) -> tuple[str, int, bool] | None:
        if not (0 <= self._current_index < len(self._navigable_items)):
            return None

        current = self._navigable_items[self._current_index]

        if current in self._thread_widget_info:
            return self._thread_widget_info[current]

        return None

    def get_current_thread_location(
        self,
    ) -> tuple[str, int, Literal["LEFT", "RIGHT"]] | None:
        thread_info = self.get_current_thread_info()
        if thread_info is None:
            return None

        _, root_comment_id, _ = thread_info
        thread = self.store.get_review_thread(root_comment_id)
        if thread is None:
            return None

        if thread.anchor_side == "old":
            line = (
                thread.original_line
                if thread.original_line is not None
                else thread.line
            )
            side: Literal["LEFT", "RIGHT"] = "LEFT"
        else:
            line = thread.line if thread.line is not None else thread.original_line
            side = "RIGHT"

        if not thread.path or line is None:
            return None
        return (thread.path, line, side)

    def refresh_thread_metadata(self) -> None:
        """Update rendered thread state after authoritative metadata arrives."""
        keep_view_at_top = self._preserve_initial_scroll_home or self._is_scroll_at_top()
        for widget, (_, root_comment_id, _) in list(
            self._thread_widget_info.items()
        ):
            thread_info = self.store.get_thread_info(root_comment_id)
            if thread_info is None:
                continue

            self._thread_widget_info[widget] = (
                thread_info.thread_id,
                root_comment_id,
                thread_info.is_resolved,
            )

            if isinstance(widget, Collapsible):
                self._apply_thread_resolved_ui(
                    widget,
                    root_comment_id,
                    thread_info.is_resolved,
                )
        if keep_view_at_top and self._scroll_container is not None:
            self._schedule_scroll_home_restore()

    def _is_scroll_at_top(self) -> bool:
        if self._scroll_container is None:
            return False
        try:
            return self._scroll_container.scroll_offset.y <= 1
        except Exception:
            return False

    def _restore_scroll_home(self) -> None:
        if self._scroll_container is None:
            return
        try:
            self._scroll_container.scroll_home(animate=False)
        except Exception:
            pass

    def _schedule_scroll_home_restore(self) -> None:
        self._restore_scroll_home()
        self.call_after_refresh(self._restore_scroll_home)
        for delay in (0.2, 0.6, 1.2):
            self.set_timer(delay, self._restore_scroll_home)

    def _find_collapsible_by_thread_id(self, thread_id: str) -> Collapsible | None:
        for widget, (tid, _, _) in self._thread_widget_info.items():
            if tid == thread_id and isinstance(widget, Collapsible):
                return widget
        return None

    def _find_collapsible_by_root_comment_id(
        self, root_comment_id: int
    ) -> Collapsible | None:
        for widget, (_, rid, _) in self._thread_widget_info.items():
            if rid == root_comment_id and isinstance(widget, Collapsible):
                return widget
        return None

    def _update_thread_resolved_ui(
        self, thread_id: str, root_comment_id: int, is_resolved: bool
    ) -> None:
        collapsible = self._find_collapsible_by_thread_id(
            thread_id
        ) or self._find_collapsible_by_root_comment_id(root_comment_id)
        if not collapsible:
            return

        self._thread_widget_info[collapsible] = (
            thread_id,
            root_comment_id,
            is_resolved,
        )

        self._apply_thread_resolved_ui(collapsible, root_comment_id, is_resolved)

    def _apply_thread_resolved_ui(
        self,
        collapsible: Collapsible,
        root_comment_id: int,
        is_resolved: bool,
    ) -> None:
        new_title: str | None = None
        thread_info = self.store.get_thread_info(root_comment_id)
        if thread_info is not None:
            line_info = f":{thread_info.line}" if thread_info.line else ""
            file_icon = get_file_icon(thread_info.path)
            if is_resolved:
                new_title = f"✓ Resolved: {file_icon} {thread_info.path}{line_info}"
            else:
                new_title = f"{file_icon} {thread_info.path}{line_info}"
        else:
            for comment in self.store.state.comments:
                if comment.id == root_comment_id:
                    line_info = (
                        f":{comment.anchor_line}" if comment.anchor_line else ""
                    )
                    file_icon = get_file_icon(comment.path)
                    if is_resolved:
                        new_title = (
                            f"✓ Resolved: {file_icon} {comment.path}{line_info}"
                        )
                    else:
                        new_title = f"{file_icon} {comment.path}{line_info}"
                    break

        if isinstance(collapsible, ReviewThreadItem):
            collapsible.set_resolved(is_resolved, title=new_title)
        else:
            if is_resolved:
                collapsible.add_class("--resolved")
                collapsible.collapsed = True
            else:
                collapsible.remove_class("--resolved")
                collapsible.collapsed = False
            if new_title is not None:
                collapsible.title = new_title

        self._invalidate_navigable_items()

    async def toggle_resolve(self) -> tuple[bool, bool]:
        thread_info = self.get_current_thread_info()
        if not thread_info:
            return (False, False)

        thread_id, root_comment_id, is_resolved = thread_info
        if not thread_id:
            return (False, False)

        if thread_id in self._pending_resolve_threads:
            return (False, is_resolved)

        self._pending_resolve_threads.add(thread_id)
        new_resolved_state = not is_resolved

        self._update_thread_resolved_ui(thread_id, root_comment_id, new_resolved_state)

        try:
            if is_resolved:
                success = await self.store.unresolve_thread(thread_id, root_comment_id)
            else:
                success = await self.store.resolve_thread(thread_id, root_comment_id)

            if success:
                self.post_message(
                    self.ResolveToggled(
                        thread_id=thread_id,
                        root_comment_id=root_comment_id,
                        is_resolved=new_resolved_state,
                    )
                )
                return (True, new_resolved_state)
            else:
                self._update_thread_resolved_ui(thread_id, root_comment_id, is_resolved)
                return (False, is_resolved)
        except Exception as e:
            self.log.error(f"Failed to toggle resolve: {e}")
            self._update_thread_resolved_ui(thread_id, root_comment_id, is_resolved)
            return (False, is_resolved)
        finally:
            self._pending_resolve_threads.discard(thread_id)

    @staticmethod
    def _format_time(dt: datetime) -> str:
        if is_min_datetime(dt):
            return ""

        now = datetime.now(timezone.utc)
        dt = datetime_sort_key(dt)

        diff = now - dt
        seconds = diff.total_seconds()

        if seconds < 60:
            return "[#6e738d]just now[/]"
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f"[#6e738d]{mins}m ago[/]"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"[#6e738d]{hours}h ago[/]"
        elif seconds < 604800:
            days = int(seconds / 86400)
            return f"[#6e738d]{days}d ago[/]"
        else:
            return f"[#6e738d]{dt.strftime('%b %d, %Y')}[/]"
