"""Inline comment thread display and interaction for DiffView.

Follows the same module-function pattern as diff_search, diff_virtual, etc.
All public functions accept a DiffView instance as the first argument.

Comment display is cursor-based: DiffView keeps focus while comment widgets
receive a visual highlight (``--cursor-line`` class) when the cursor sits on
their parent diff line.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING, Literal

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine
from rit.state.models import PendingReviewComment, PRComment, ReviewThread
from rit.ui.icons import get_file_icon
from rit.ui.messages import Flash
from rit.ui.widgets.review_thread_card import ReviewThreadItem

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


# ---------------------------------------------------------------------------
# Height estimation for virtual layout
# ---------------------------------------------------------------------------

COLLAPSED_THREAD_HEIGHT = 1
COMMENT_HEIGHT_ESTIMATE = 3  # header + ~2 body lines
PENDING_DRAFT_HEIGHT_ESTIMATE = 4


def estimate_thread_height(thread: ReviewThread) -> int:
    if thread.is_resolved:
        return COLLAPSED_THREAD_HEIGHT
    n = len(thread.comments)
    if n == 0:
        return COLLAPSED_THREAD_HEIGHT
    return 2 + n * COMMENT_HEIGHT_ESTIMATE


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def clear_state(view: DiffView) -> None:
    view._comment_threads_by_line.clear()
    view._comment_line_indices.clear()
    view._comment_widgets_by_line.clear()
    view._comment_side_by_line.clear()
    view._pending_comment_drafts_by_line.clear()
    view._pending_comment_widgets_by_line.clear()


def build_comment_map(view: DiffView) -> None:
    clear_state(view)

    if not view.store or not view.current_file:
        return

    get_pending_file_comments = getattr(view.store, "get_pending_file_comments", None)
    if callable(get_pending_file_comments):
        drafts = get_pending_file_comments(view.current_file)
        if isinstance(drafts, list):
            for draft in drafts:
                line_index = _resolve_pending_line_index(view, draft)
                if line_index is not None:
                    view._pending_comment_drafts_by_line.setdefault(
                        line_index, []
                    ).append(draft)

    threads = view.store.state.review_threads
    if not threads:
        view._comment_line_indices = sorted(view._pending_comment_drafts_by_line)
        return

    for thread in threads:
        if thread.path != view.current_file:
            continue
        root = thread.root_comment
        if root is None:
            continue

        line_index = _resolve_line_index(view, root)
        if line_index is not None:
            view._comment_threads_by_line.setdefault(line_index, []).append(thread)
            existing_side = view._comment_side_by_line.get(line_index)
            root_side = _comment_target_side(root)
            if existing_side is None:
                view._comment_side_by_line[line_index] = root_side
            elif existing_side == "auto":
                view._comment_side_by_line[line_index] = root_side
            elif root_side != "auto" and existing_side != root_side:
                view._comment_side_by_line[line_index] = "auto"

    view._comment_line_indices = sorted(
        set(view._comment_threads_by_line) | set(view._pending_comment_drafts_by_line)
    )


def _comment_target_side(comment: PRComment) -> Literal["old", "new", "auto"]:
    return comment.anchor_side


def _resolve_line_index(view: DiffView, comment: PRComment) -> int | None:
    target_side = _comment_target_side(comment)

    if target_side != "new" and comment.original_line is not None:
        idx = view._line_index_by_old_number.get(comment.original_line)
        if idx is not None:
            return idx
    if target_side != "old" and comment.line is not None:
        idx = view._line_index_by_new_number.get(comment.line)
        if idx is not None:
            return idx
    if target_side == "new" and comment.original_line is not None:
        idx = view._line_index_by_old_number.get(comment.original_line)
        if idx is not None:
            return idx
    if target_side == "old" and comment.line is not None:
        idx = view._line_index_by_new_number.get(comment.line)
        if idx is not None:
            return idx

    return _resolve_line_index_from_diff_hunk(view, comment, target_side)


def _resolve_pending_line_index(
    view: DiffView,
    comment: PendingReviewComment,
) -> int | None:
    if comment.side == "LEFT":
        return view._line_index_by_old_number.get(comment.line)
    return view._line_index_by_new_number.get(comment.line)


def _resolve_line_index_from_diff_hunk(
    view: DiffView,
    comment: PRComment,
    target_side: Literal["old", "new", "auto"],
) -> int | None:
    if view._diff is None or not comment.diff_hunk:
        return None

    hunk_diff = parse_patch(comment.diff_hunk, comment.path)
    if not hunk_diff.hunks:
        return None

    best_hunk = None
    best_score = 0
    for target_hunk in hunk_diff.hunks:
        for current_hunk in view._diff.hunks:
            score = _hunk_overlap_score(target_hunk, current_hunk)
            if score > best_score:
                best_score = score
                best_hunk = current_hunk

    if best_hunk is None:
        return None

    return _nearest_line_index_in_hunk(best_hunk, target_side, comment.anchor_line)


def _hunk_overlap_score(target_hunk: DiffHunk, current_hunk: DiffHunk) -> int:
    return _range_overlap(
        target_hunk.old_start,
        target_hunk.old_start + target_hunk.old_count - 1,
        current_hunk.old_start,
        current_hunk.old_start + current_hunk.old_count - 1,
    ) + _range_overlap(
        target_hunk.new_start,
        target_hunk.new_start + target_hunk.new_count - 1,
        current_hunk.new_start,
        current_hunk.new_start + current_hunk.new_count - 1,
    )


def _range_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> int:
    if end_a < start_a or end_b < start_b:
        return 0
    return max(0, min(end_a, end_b) - max(start_a, start_b) + 1)


def _nearest_line_index_in_hunk(
    hunk: DiffHunk,
    target_side: Literal["old", "new", "auto"],
    anchor_line: int | None,
) -> int | None:
    primary_candidates = [
        line
        for line in hunk.lines
        if not line.is_context and _line_number_for_side(line, target_side) is not None
    ]
    fallback_candidates = [
        line
        for line in hunk.lines
        if _line_number_for_side(line, target_side) is not None
    ]
    candidates = primary_candidates if primary_candidates else fallback_candidates
    if not candidates:
        return None

    if anchor_line is None:
        return candidates[0].line_index

    nearest = min(
        candidates,
        key=lambda line: _line_distance_from_anchor(line, target_side, anchor_line),
    )
    return nearest.line_index


def _line_distance_from_anchor(
    line: DiffLine,
    target_side: Literal["old", "new", "auto"],
    anchor_line: int,
) -> int:
    line_number = _line_number_for_side(line, target_side)
    if line_number is None:
        return 10**9
    return abs(line_number - anchor_line)


def _line_number_for_side(
    line: DiffLine,
    target_side: Literal["old", "new", "auto"],
) -> int | None:
    if target_side == "old":
        return line.old_line_no
    if target_side == "new":
        return line.new_line_no
    return line.new_line_no if line.new_line_no is not None else line.old_line_no


# ---------------------------------------------------------------------------
# Mounting comment widgets into the diff DOM
# ---------------------------------------------------------------------------


def mount_pending_drafts_for_line(
    view: DiffView,
    container: VerticalScroll,
    line_index: int,
    *,
    before: Widget | None = None,
) -> None:
    drafts = view._pending_comment_drafts_by_line.get(line_index)
    if not drafts:
        return

    mounted: list[Widget] = []
    for index, draft in enumerate(drafts):
        widget = _build_pending_draft_widget(draft, line_index=line_index, index=index)
        if before is not None:
            container.mount(widget, before=before)
        else:
            container.mount(widget)
        mounted.append(widget)

    view._pending_comment_widgets_by_line[line_index] = mounted


def mount_comments_for_line(
    view: DiffView,
    container: VerticalScroll,
    line_index: int,
    *,
    before: Widget | None = None,
) -> None:
    threads = view._comment_threads_by_line.get(line_index)
    if not threads:
        return

    mounted: list[Widget] = []
    for thread in threads:
        widget = _build_inline_thread_widget(thread)
        if before is not None:
            container.mount(widget, before=before)
        else:
            container.mount(widget)
        mounted.append(widget)

    view._comment_widgets_by_line[line_index] = mounted


# ---------------------------------------------------------------------------
# Cursor-based visual highlight
# ---------------------------------------------------------------------------


def update_cursor_highlight(view: DiffView, old_line: int, new_line: int) -> None:
    if old_line != new_line:
        for w in view._comment_widgets_by_line.get(old_line, []):
            w.remove_class("--cursor-line")
        for w in view._pending_comment_widgets_by_line.get(old_line, []):
            w.remove_class("--cursor-line")
    for w in view._comment_widgets_by_line.get(new_line, []):
        w.add_class("--cursor-line")
    for w in view._pending_comment_widgets_by_line.get(new_line, []):
        w.add_class("--cursor-line")


def try_toggle_current(view: DiffView) -> bool:
    widgets = view._comment_widgets_by_line.get(view.cursor_line)
    if not widgets:
        return False
    w = widgets[0]
    if isinstance(w, Collapsible):
        w.collapsed = not w.collapsed
        return True
    return False


# ---------------------------------------------------------------------------
# Navigation: jump between comment lines (with cross-file support)
# ---------------------------------------------------------------------------


def next_comment(view: DiffView) -> None:
    indices = view._comment_line_indices
    if not indices:
        view.post_message(view.CrossFileComment(direction=1))
        return

    pos = bisect_right(indices, view.cursor_line)
    if pos < len(indices):
        _jump_to_comment_line(view, indices[pos])
    else:
        view.post_message(view.CrossFileComment(direction=1))


def prev_comment(view: DiffView) -> None:
    indices = view._comment_line_indices
    if not indices:
        view.post_message(view.CrossFileComment(direction=-1))
        return

    pos = bisect_left(indices, view.cursor_line) - 1
    if pos >= 0:
        _jump_to_comment_line(view, indices[pos])
    else:
        view.post_message(view.CrossFileComment(direction=-1))


def _jump_to_comment_line(view: DiffView, line_index: int) -> None:
    from rit.ui.widgets import diff_virtual as _virtual

    target_side = view._comment_side_by_line.get(line_index, "auto")
    target_pane = None if target_side == "auto" else target_side

    rows = view._rows_for_current_mode()
    target_row = None
    for row in rows:
        if row.line_index != line_index:
            continue
        if row.side == target_side:
            target_row = row
            break
        if target_row is None:
            target_row = row

    if target_row is not None:
        view._jump_to_row_with_anchor(
            target_row,
            pane=target_pane,
            viewport_offset=2,
        )
    else:
        _virtual._maybe_update_virtual_window(view, line_index)
        view._move_cursor(line=line_index, pane=target_pane)


# ---------------------------------------------------------------------------
# Resolve / unresolve
# ---------------------------------------------------------------------------


async def toggle_resolve(view: DiffView) -> None:
    threads = view._comment_threads_by_line.get(view.cursor_line)
    if not threads:
        view.post_message(
            Flash("No comment thread on this line", style="warning", duration=2.0)
        )
        return

    if not view.store:
        return

    thread = threads[0]
    thread_id = thread.id
    root_id = thread.root_comment_id
    new_resolved = not thread.is_resolved

    _update_thread_widget_resolved(view, view.cursor_line, thread, new_resolved)

    try:
        if new_resolved:
            success = await view.store.resolve_thread(thread_id, root_id)
        else:
            success = await view.store.unresolve_thread(thread_id, root_id)

        if success:
            verb = "Resolved" if new_resolved else "Unresolved"
            view.post_message(Flash(f"{verb} thread", style="success", duration=2.0))
        else:
            _update_thread_widget_resolved(
                view, view.cursor_line, thread, not new_resolved
            )
            view.post_message(
                Flash("Failed to toggle resolve", style="error", duration=3.0)
            )
    except Exception as e:
        _update_thread_widget_resolved(view, view.cursor_line, thread, not new_resolved)
        view.post_message(Flash(f"Error: {e}", style="error", duration=3.0))


def _update_thread_widget_resolved(
    view: DiffView,
    line_index: int,
    thread: ReviewThread,
    is_resolved: bool,
) -> None:
    from rit.ui.widgets import diff_virtual as _virtual

    widgets = view._comment_widgets_by_line.get(line_index, [])
    for w in widgets:
        if isinstance(w, ReviewThreadItem) and w.is_resolved != is_resolved:
            root = thread.root_comment
            line_info = f":{root.anchor_line}" if root and root.anchor_line else ""
            file_icon = get_file_icon(thread.path)
            if is_resolved:
                new_title = f"✓ Resolved: {file_icon} {thread.path}{line_info}"
            else:
                new_title = f"{file_icon} {thread.path}{line_info}"
            w.set_resolved(is_resolved, title=new_title)
            break

    _virtual._rebuild_virtual_layout(view)


# ---------------------------------------------------------------------------
# Build inline thread widget (shared ReviewThreadItem with cursor-line CSS)
# ---------------------------------------------------------------------------


def estimate_pending_draft_height(draft: PendingReviewComment) -> int:
    body_lines = max(1, len(draft.body.splitlines()))
    return max(PENDING_DRAFT_HEIGHT_ESTIMATE, body_lines + 2)


class PendingDraftItem(Vertical):
    DEFAULT_CSS = """
    PendingDraftItem {
        height: auto;
        margin-left: 2;
        border: round $warning;
        background: $surface;
        padding: 0 1;
        width: 1fr;
    }

    PendingDraftItem.--cursor-line {
        tint: $accent 10%;
    }

    PendingDraftItem .pending-draft-title {
        color: $warning;
        text-style: bold;
    }

    PendingDraftItem .pending-draft-body {
        height: auto;
    }
    """

    def __init__(self, draft: PendingReviewComment, *, id: str) -> None:
        super().__init__(id=id, classes="--pending-draft")
        self._draft = draft

    def compose(self) -> ComposeResult:
        side = "LEFT" if self._draft.side == "LEFT" else "RIGHT"
        yield Static(
            f"Pending comment ({side}) • c edit • d delete",
            classes="pending-draft-title",
        )
        yield Static(self._draft.body, classes="pending-draft-body", markup=False)


def _build_pending_draft_widget(
    draft: PendingReviewComment,
    *,
    line_index: int,
    index: int,
) -> PendingDraftItem:
    side = draft.side.lower()
    return PendingDraftItem(
        draft,
        id=f"pending-draft-{line_index}-{side}-{index}",
    )


def _build_inline_thread_widget(thread: ReviewThread) -> ReviewThreadItem:
    root = thread.root_comment
    line_info = f":{root.anchor_line}" if root and root.anchor_line else ""
    file_icon = get_file_icon(thread.path)

    if thread.is_resolved:
        title = f"✓ Resolved: {file_icon} {thread.path}{line_info}"
        classes = "--thread --resolved --inline"
        collapsed = True
    else:
        title = f"{file_icon} {thread.path}{line_info}"
        classes = "--thread --inline"
        collapsed = False

    line_no = root.anchor_line if root else None

    return ReviewThreadItem(
        title=title,
        path=thread.path,
        line=line_no,
        comments=thread.comments,
        diff_hunk=root.diff_hunk if root else "",
        is_resolved=thread.is_resolved,
        compact=False,
        show_diff_hunk=bool(root and root.diff_hunk),
        show_path_header=False,
        collapsed=collapsed,
        classes=classes,
        id=f"inline-thread-{thread.root_comment_id}",
    )
