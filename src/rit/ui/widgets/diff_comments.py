"""Inline comment thread display and interaction for DiffView.

Follows the same module-function pattern as diff_search, diff_virtual, etc.
All public functions accept a DiffView instance as the first argument.

Comment display is cursor-based: DiffView keeps focus while comment widgets
receive a visual highlight (``--cursor-line`` class) when the cursor sits on
their parent diff line.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING

from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible

from rit.state.models import PRComment, ReviewThread
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


def build_comment_map(view: DiffView) -> None:
    clear_state(view)

    if not view.store or not view.current_file:
        return

    threads = view.store.state.review_threads
    if not threads:
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

    view._comment_line_indices = sorted(view._comment_threads_by_line)


def _resolve_line_index(view: DiffView, comment: PRComment) -> int | None:
    if comment.line is not None:
        idx = view._line_index_by_new_number.get(comment.line)
        if idx is not None:
            return idx
    if comment.original_line is not None:
        idx = view._line_index_by_old_number.get(comment.original_line)
        if idx is not None:
            return idx
    return None


# ---------------------------------------------------------------------------
# Mounting comment widgets into the diff DOM
# ---------------------------------------------------------------------------


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
    for w in view._comment_widgets_by_line.get(new_line, []):
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

    rows = view._rows_for_current_mode()
    target_row = None
    for row in rows:
        if row.line_index == line_index:
            target_row = row
            break

    if target_row is not None:
        view._jump_to_row_with_anchor(target_row, viewport_offset=2)
    else:
        _virtual._maybe_update_virtual_window(view, line_index)
        view._move_cursor(line=line_index)


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
            line_info = ""
            if root:
                if root.line:
                    line_info = f":{root.line}"
                elif root.original_line:
                    line_info = f":{root.original_line}"
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


def _build_inline_thread_widget(thread: ReviewThread) -> ReviewThreadItem:
    root = thread.root_comment
    line_info = ""
    if root:
        if root.line:
            line_info = f":{root.line}"
        elif root.original_line:
            line_info = f":{root.original_line}"
    file_icon = get_file_icon(thread.path)

    if thread.is_resolved:
        title = f"✓ Resolved: {file_icon} {thread.path}{line_info}"
        classes = "--thread --resolved --inline"
        collapsed = True
    else:
        title = f"{file_icon} {thread.path}{line_info}"
        classes = "--thread --inline"
        collapsed = False

    line_no = root.line or root.original_line if root else None

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
