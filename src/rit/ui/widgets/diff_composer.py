from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.widget import Widget

from rit.state.models import PendingReviewComment
from rit.state.review_annotations import ReviewAnnotationIndex
from rit.ui.widgets.comment_editor import (
    CommentEditorBuffer,
    EditorKind,
    InlineCommentEditor,
    SubmitMode,
)
from rit.ui.widgets.inline_review_thread import InlineReviewThread
from rit.ui.widgets.review_thread_card import ReviewThreadItem

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


@dataclass
class ComposerSession:
    """A single unfinished comment, retained across diff renders."""

    kind: EditorKind
    title: str
    context: str
    buffer: CommentEditorBuffer
    update_existing: bool = False
    root_id: int | None = None
    draft: PendingReviewComment | None = None
    comparison_refs: tuple[str, str] | None = None
    suspended: bool = False


class DiffCommentComposer:
    """Mount an unfinished comment at its stable line, file or thread anchor."""

    def __init__(self, view: DiffView) -> None:
        self.view = view
        self.session: ComposerSession | None = None
        self.widget: InlineCommentEditor | None = None
        self.layout: Widget | None = None
        self.line: int | None = None
        self.hunk: int | None = None
        self._focus_on_mount = False
        self.reveal_pending = False
        self._editor_height = 0

    async def open(self, session: ComposerSession) -> None:
        self.session = session
        store = self.view.store
        if (
            store is not None
            and (pr := store.state.pr) is not None
            and (pr.base_sha or pr.head_sha)
        ):
            session.comparison_refs = (pr.base_sha, pr.head_sha)
        self._focus_on_mount = True
        if session.root_id is not None:
            self.view._discussion_state.thread_collapsed[session.root_id] = False
        await self.rerender()

    def resume(self) -> bool:
        if self.session is None:
            return False
        if self.session.suspended and self.session.buffer.submitting:
            self.view.notify("Wait for the comment submission to finish")
            return True
        suspended = self.session.suspended
        self.session.suspended = False
        line = self._anchor_line()
        attached = self.widget is not None and self.widget.is_attached
        if line is not None:
            target = self.view.inline_comment_target()
            side = target[2] if target is not None else "RIGHT"
            self._focus_on_mount = not attached
            self.view.jump_to_line_index(
                line, side=side, focus=not attached, only_if_hidden=attached
            )
        if self.widget is not None and self.widget.is_attached:
            from rit.ui.widgets.diff_comments import select_thread_from_title

            for ancestor in self.widget.ancestors:
                if isinstance(ancestor, ReviewThreadItem):
                    ancestor.collapsed = False
                    select_thread_from_title(self.view, ancestor)
            self._restore_selection()
            self.widget.open()
            self.reveal_pending = True
        elif line is None:
            self.session.suspended = suspended
            self.view.notify(
                "An unfinished comment is kept. Return to its file to continue."
            )
        elif suspended:
            self.view.run_worker(
                self.rerender(),
                name="resume-comment-editor",
                group="diff-comment-composer",
            )
        return True

    async def suspend(self) -> None:
        """Close the input while retaining its text and editing history locally."""
        session = self.session
        if session is None or session.buffer.submitting:
            return
        self.capture()
        if not session.buffer.text.strip():
            if session.kind == "reply":
                await self.view.close_reply_comment_editor()
            elif session.kind == "file":
                await self.view.close_file_comment_editor()
            else:
                await self.view.close_inline_comment_editor()
            return
        session.suspended = True
        self._focus_on_mount = False
        self.view.screen.set_focus(self.view, scroll_visible=False)
        await self.rerender()
        if session.suspended and session.buffer.text:
            self.view.notify("Unsent text kept locally. Press c to resume.", timeout=3)

    def hide_saved_draft(self) -> None:
        """Hide locally saved input on the next render, retaining it for recovery."""
        if self.session is None:
            return
        self.capture()
        self.session.suspended = True
        self._focus_on_mount = False
        self.view.screen.set_focus(self.view, scroll_visible=False)

    def restore_saved_draft(self) -> None:
        """Restore input on the next render after draft synchronization fails."""
        if self.session is None or not self.session.suspended:
            return
        self.session.suspended = False
        self._focus_on_mount = True

    def _restore_selection(self) -> None:
        from rit.ui.widgets.diff_comments import select_comment_widget

        session = self.session
        if session is None or self.widget is None or not self.widget.is_attached:
            return
        view = self.view
        target: Widget | None = None
        drafts: list[PendingReviewComment] = []
        cards: list[Widget] = []
        if isinstance(self.layout, InlineReviewThread):
            thread = self.layout
            target = thread
            comment = (
                view.reply_comment_target()
                or view.inline_comment_edit_target()
                or view.file_comment_edit_target()
            )
            for index in range(thread.comment_count):
                card = thread.comment_card_at(index)
                if card is None:
                    continue
                if (draft := thread.pending_at(index)) is not None:
                    drafts.append(draft)
                    cards.append(card)
                current = thread.comment_at(index)
                if (
                    comment is not None
                    and current is not None
                    and current.id == comment.id
                ):
                    target = card
        elif self.line is not None:
            drafts = view._pending_comment_drafts_by_line.get(self.line, [])
            cards = list(view._pending_comment_widgets_by_line.get(self.line, []))
        elif self.hunk is not None:
            view._set_file_header_selection(self.hunk)
            path = view.file_comment_target()
            drafts = view._pending_file_comment_drafts_by_path.get(path or "", [])
            cards = list(view._pending_file_comment_widgets_by_hunk.get(self.hunk, []))
        if session.draft is not None:
            index = ReviewAnnotationIndex.from_parts(
                pending_comments=drafts
            ).index_for_comment(session.draft)
            if index is not None and index < len(cards):
                target = cards[index]
        if target is not None:
            suppress_scroll = view._cursor_ui.suppress_scroll
            view._cursor_ui.suppress_scroll = True
            try:
                select_comment_widget(view, target)
            finally:
                view._cursor_ui.suppress_scroll = suppress_scroll
        view.post_message(view.CursorLineChanged(line_index=view.cursor_line))

    def _anchor_line(self) -> int | None:
        view = self.view
        session = self.session
        if session is None:
            return None
        if session.root_id is None and session.kind == "inline":
            return view._inline_comment_editor_line_index
        hunk = view._file_comment_editor_hunk_index
        if session.root_id is not None:
            root = session.root_id
            for line, threads in view._comment_threads_by_line.items():
                if any(thread.root_comment_id == root for thread in threads):
                    return line
            for path, threads in view._file_comment_threads_by_path.items():
                if any(thread.root_comment_id == root for thread in threads):
                    hunk = view._file_header_hunk_index(path)
                    break
        if hunk is not None and view._diff is not None:
            lines = view._diff.hunks[hunk].lines
            return lines[0].line_index if lines else None
        return None

    def reveal(self) -> None:
        editor = self.widget
        if editor is None or not editor.region.height:
            return
        if editor.region.height != self._editor_height:
            self._editor_height = editor.region.height
            self.reveal_pending |= editor.has_focus_within
        if not self.reveal_pending:
            return
        self.reveal_pending = False
        viewport = self.view.scrollable_content_region
        top = viewport.y + self.view._sticky_header_inset
        region = editor.region
        available_height = max(1, viewport.bottom - top)
        if region.height > available_height:
            focused = self.view.screen.focused
            if focused is not None and editor in focused.ancestors:
                region = focused.region
                for selector in (".comment-editor-actions", ".comment-editor-status"):
                    controls = region.union(editor.query_one(selector).region)
                    if controls.height <= available_height:
                        region = controls
        delta = max(0, region.bottom - viewport.bottom)
        if region.y < top:
            delta = region.y - top
        if delta:
            self.view.scroll_to(y=self.view.scroll_y + delta, animate=False)

    def capture(self, *, evicted: bool = False) -> None:
        if self.widget is not None:
            self.widget.capture()
            if self.widget.has_focus_within:
                self._focus_on_mount = not evicted
                self.view.screen.set_focus(
                    self.view if evicted else None, scroll_visible=False
                )
        self.widget = None
        self.layout = None
        self.line = None
        self.hunk = None
        self._editor_height = 0
        self.reveal_pending = False

    async def close(self) -> None:
        self.capture()
        self.session = None
        self._focus_on_mount = False
        await self.rerender()
        if self.view.is_mounted:
            self.view.call_after_refresh(
                lambda: self.view.screen.set_focus(self.view, scroll_visible=False)
            )

    async def rerender(self) -> None:
        from rit.ui.widgets import diff_virtual

        view = self.view
        view._annotation_line_heights.clear()
        view._annotation_hunk_heights.clear()
        diff_virtual._rebuild_virtual_layout(view)
        if view.is_mounted and view._diff is not None:
            await view._run_render_diff_for_request(view._render_request_token)
            await view._await_content_mounts()
            view.call_after_refresh(self._restore_selection)

    def _editor(self) -> InlineCommentEditor:
        session = self.session
        if session is None:
            raise RuntimeError("No active comment session")
        editor = InlineCommentEditor(
            kind=session.kind,
            title=session.title,
            context=session.context,
            placeholder="Write a reply…"
            if session.kind == "reply"
            else "Write a comment…",
            buffer=session.buffer,
            compact=True,
            update_existing=session.update_existing,
            editing_draft=session.draft is not None,
            post_disabled_reason=""
            if session.update_existing
            else self._post_disabled_reason(),
            id=f"diff-{session.kind}-comment-editor",
        )
        editor.remove_class("-hidden")
        if self._focus_on_mount:
            editor.open()
            self.reveal_pending = True
            self._focus_on_mount = False
            self.view.call_after_refresh(self._restore_selection)
        self.widget = editor
        return editor

    def mount_thread(
        self, thread: ReviewThreadItem, *, draft: PendingReviewComment | None = None
    ) -> None:
        session = self.session
        if session is None or session.suspended or self.widget is not None:
            return
        if draft is None:
            if (
                not isinstance(thread, InlineReviewThread)
                or session.root_id != thread.root_id
            ):
                return
        else:
            if session.draft is None:
                return
            index = self.view._pending_draft_index(session.draft)
            if index is None or index != self.view._pending_draft_index(draft):
                return
            self.view._discussion_state.collapsed_drafts.pop(id(draft), None)
        thread.mount_composer(self._editor())
        self.layout = thread

    def mount_line(
        self, container: VerticalScroll, line: int, before: Widget | None
    ) -> None:
        from rit.ui.widgets.diff_comments import mount_side_aware_widget

        session = self.session
        if (
            session is None
            or session.suspended
            or session.root_id is not None
            or session.kind != "inline"
            or self.widget is not None
            or line != self.view._inline_comment_editor_line_index
        ):
            return
        target = self.view.inline_comment_target()
        if target is None:
            return
        self.line = line
        self.layout = mount_side_aware_widget(
            self.view,
            container,
            self._editor(),
            side="old" if target[2] == "LEFT" else "new",
            line_index=line,
            before=before,
        )

    def mount_file(
        self, container: VerticalScroll, hunk: int, before: Widget | None
    ) -> None:
        from rit.ui.widgets.diff_comments import mount_side_aware_widget

        session = self.session
        if (
            session is None
            or session.suspended
            or session.root_id is not None
            or session.kind != "file"
            or self.widget is not None
            or hunk != self.view._file_comment_editor_hunk_index
        ):
            return
        self.hunk = hunk
        self.layout = mount_side_aware_widget(
            self.view,
            container,
            self._editor(),
            side="new",
            line_index=self._anchor_line(),
            before=before,
        )
        annotations = self.view._file_comment_annotation_widgets_by_hunk
        annotations.setdefault(hunk, []).append(self.layout)

    def estimate_height(self) -> int:
        return 12 if self.session is not None and not self.session.suspended else 0

    def _post_disabled_reason(self) -> str:
        store = self.view.store
        if store is not None and store.state.pending_review.review_id is not None:
            return "Finish pending review to post."
        return ""

    def begin_submit(self, mode: SubmitMode = "queue") -> bool:
        """Validate retained targets and prevent duplicate submissions."""
        session = self.session
        if session is None or session.buffer.submitting:
            return False
        if mode == "post" and (reason := self._post_disabled_reason()):
            self.set_submitting(False, reason)
            return False
        store = self.view.store
        if store is not None:
            pr = store.state.pr
            refs = (pr.base_sha, pr.head_sha) if pr is not None else None
            if (
                session.root_id is None
                and session.comparison_refs is not None
                and refs != session.comparison_refs
            ):
                self.set_submitting(
                    False,
                    "The PR changed. Copy your text and reopen at the new location.",
                )
                return False
            if session.draft is not None:
                index = self.view._pending_draft_index(session.draft)
                if index is None:
                    self.set_submitting(
                        False, "This draft no longer exists. Your text is kept."
                    )
                    return False
                if session.kind == "reply":
                    self.view._reply_comment_draft_index = index
                elif session.kind == "file":
                    self.view._file_comment_editor_draft_index = index
                else:
                    self.view._inline_comment_editor_draft_index = index
        session.buffer.submit_mode = mode
        self.set_submitting(True)
        return True

    def set_submitting(self, submitting: bool, error: str = "") -> None:
        if self.session is not None:
            self.session.buffer.submitting = submitting
            self.session.buffer.error = error
        if self.widget is not None and self.widget.is_attached:
            self.widget.set_submitting(submitting, error=error)
