"""Inline comment thread display and interaction for DiffView.

Follows the same module-function pattern as diff_search, diff_virtual, etc.
All public functions accept a DiffView instance as the first argument.

Comment display is cursor-based: DiffView keeps focus while comment widgets
receive a visual highlight (``--cursor-line`` class) when the cursor sits on
their parent diff line.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from collections.abc import Set as AbstractSet
from typing import TYPE_CHECKING, Literal

from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine
from rit.state.models import PendingReviewComment, PRComment, ReviewThread
from rit.ui.icons import get_file_icon
from rit.ui.messages import Flash
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.review_thread_card import ReviewThreadItem

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


__all__ = (
    "COLLAPSED_THREAD_HEIGHT",
    "COMMENT_HEIGHT_ESTIMATE",
    "PENDING_DRAFT_HEIGHT_ESTIMATE",
    "active_comment_widget",
    "active_pending_draft",
    "active_thread",
    "build_comment_map",
    "clear_state",
    "comment_widgets_in_order",
    "estimate_pending_draft_height",
    "estimate_thread_height",
    "mount_comments_for_line",
    "mount_pending_drafts_for_line",
    "mount_side_aware_widget",
    "next_comment",
    "prev_comment",
    "toggle_resolve",
    "total_comments_at_line",
    "try_toggle_current",
    "update_cursor_highlight",
)


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
    view._comment_layout_widgets_by_line.clear()
    view._comment_side_by_line.clear()
    view._pending_comment_drafts_by_line.clear()
    view._pending_comment_widgets_by_line.clear()
    view._pending_comment_layout_widgets_by_line.clear()


def build_comment_map(view: DiffView) -> None:
    clear_state(view)

    if not view.store or not view.current_file:
        return

    file_paths = _file_paths_for_current_diff(view)
    for draft in _pending_comments_for_current_diff(view, file_paths):
        line_index = _resolve_pending_line_index(view, draft)
        if line_index is not None:
            view._pending_comment_drafts_by_line.setdefault(line_index, []).append(
                draft
            )

    threads = view.store.state.review_threads
    if not threads:
        view._comment_line_indices = _comment_line_indices_for_keys(
            view._pending_comment_drafts_by_line.keys()
        )
        return

    for thread in threads:
        if thread.path in file_paths and not _pending_review_thread_is_draft(
            view,
            thread,
        ):
            _add_thread_to_comment_map(view, thread)

    view._comment_line_indices = _comment_line_indices_for_keys(
        view._comment_threads_by_line.keys()
        | view._pending_comment_drafts_by_line.keys()
    )


def refresh_thread_metadata(view: DiffView) -> None:
    if not view.store or not view.current_file:
        return

    file_paths = _file_paths_for_current_diff(view)
    updated_by_root = {
        thread.root_comment_id: thread
        for thread in view.store.state.review_threads
        if thread.path in file_paths
    }
    if not updated_by_root:
        return

    layout_changed = False
    for line_index, threads in list(view._comment_threads_by_line.items()):
        updated_threads: list[ReviewThread] = []
        for index, thread in enumerate(threads):
            updated = updated_by_root.get(thread.root_comment_id)
            if updated is None:
                updated_threads.append(thread)
                continue

            updated_threads.append(updated)
            if updated.is_resolved != thread.is_resolved:
                layout_changed = True
            if _update_mounted_thread_widget(view, line_index, index, updated):
                layout_changed = True
        view._comment_threads_by_line[line_index] = updated_threads

    if layout_changed:
        from rit.ui.widgets import diff_virtual as _virtual

        _virtual._rebuild_virtual_layout(view)


def _add_thread_to_comment_map(view: DiffView, thread: ReviewThread) -> None:
    root = thread.root_comment
    if root is None:
        return

    line_index = _resolve_line_index(view, root, thread=thread)
    if line_index is None:
        return

    view._comment_threads_by_line.setdefault(line_index, []).append(thread)
    existing_side = view._comment_side_by_line.get(line_index)
    root_side = _comment_target_side(root, thread=thread)
    if existing_side is None:
        view._comment_side_by_line[line_index] = root_side
    elif existing_side == "auto":
        view._comment_side_by_line[line_index] = root_side
    elif root_side != "auto" and existing_side != root_side:
        view._comment_side_by_line[line_index] = "auto"


def _pending_review_thread_is_draft(view: DiffView, thread: ReviewThread) -> bool:
    state = getattr(view.store, "state", None)
    pending_review_id = getattr(state, "pending_review_id", None)
    if not pending_review_id:
        return False

    drafts = getattr(state, "pending_review_comments", [])
    if not isinstance(drafts, list) or not drafts:
        return False

    draft_ids = {draft.review_comment_id for draft in drafts if draft.review_comment_id}
    for comment in thread.comments:
        if comment.pull_request_review_id != pending_review_id:
            continue
        if comment.id in draft_ids:
            return True
        if any(
            _pending_draft_matches_review_comment(draft, comment, thread=thread)
            for draft in drafts
        ):
            return True
    return False


def _pending_draft_matches_review_comment(
    draft: PendingReviewComment,
    comment: PRComment,
    *,
    thread: ReviewThread,
) -> bool:
    if draft.path != comment.path or draft.body != comment.body:
        return False

    target_side = _comment_target_side(comment, thread=thread)
    if draft.anchor_side != target_side:
        return False

    return _anchor_line_for_side(comment, target_side, thread=thread) == draft.line


def _update_mounted_thread_widget(
    view: DiffView,
    line_index: int,
    index: int,
    thread: ReviewThread,
) -> bool:
    widgets = view._comment_widgets_by_line.get(line_index, [])
    if index >= len(widgets):
        return False

    widget = widgets[index]
    if not isinstance(widget, ReviewThreadItem):
        return False
    if widget.is_resolved == thread.is_resolved:
        return False

    widget.set_resolved(thread.is_resolved, title=_inline_thread_title(thread))
    return True


def _comment_line_indices_for_keys(keys: AbstractSet[int]) -> list[int]:
    count = len(keys)
    if count == 0:
        return []
    if count == 1:
        return [next(iter(keys))]
    return sorted(keys)


def _file_paths_for_current_diff(view: DiffView) -> AbstractSet[str]:
    planned_paths = getattr(view, "_diff_file_paths", frozenset())
    if planned_paths:
        return planned_paths

    paths = {line.file_path for line in view._all_lines if line.file_path}
    if paths:
        return paths
    return {view.current_file} if view.current_file else set()


def _pending_comments_for_current_diff(
    view: DiffView,
    file_paths: AbstractSet[str],
) -> Sequence[PendingReviewComment]:
    get_pending_file_comments = getattr(view.store, "get_pending_file_comments", None)
    if callable(get_pending_file_comments) and len(file_paths) == 1:
        drafts = get_pending_file_comments(next(iter(file_paths)))
        if isinstance(drafts, list):
            return drafts

    state = getattr(view.store, "state", None)
    drafts = getattr(state, "pending_review_comments", [])
    if not isinstance(drafts, list):
        return ()
    if not drafts:
        return ()
    return [draft for draft in drafts if draft.path in file_paths]


def _comment_target_side(
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> Literal["old", "new", "auto"]:
    if thread is not None and thread.anchor_side != "auto":
        return thread.anchor_side
    return comment.anchor_side


def _resolve_line_index(
    view: DiffView,
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    target_side = _comment_target_side(comment, thread=thread)
    old_line = _old_anchor_line(comment, thread=thread)
    new_line = _new_anchor_line(comment, thread=thread)
    file_old_map = getattr(view, "_line_index_by_file_old_number", {})
    file_new_map = getattr(view, "_line_index_by_file_new_number", {})

    if target_side != "new" and old_line is not None:
        idx = file_old_map.get((comment.path, old_line))
        if idx is not None:
            return idx
        idx = view._line_index_by_old_number.get(old_line)
        if idx is not None:
            return idx
    if target_side != "old" and new_line is not None:
        idx = file_new_map.get((comment.path, new_line))
        if idx is not None:
            return idx
        idx = view._line_index_by_new_number.get(new_line)
        if idx is not None:
            return idx
    if target_side == "new" and old_line is not None:
        idx = file_old_map.get((comment.path, old_line))
        if idx is not None:
            return idx
        idx = view._line_index_by_old_number.get(old_line)
        if idx is not None:
            return idx
    if target_side == "old" and new_line is not None:
        idx = file_new_map.get((comment.path, new_line))
        if idx is not None:
            return idx
        idx = view._line_index_by_new_number.get(new_line)
        if idx is not None:
            return idx

    return _resolve_line_index_from_diff_hunk(
        view,
        comment,
        target_side,
        _anchor_line_for_side(comment, target_side, thread=thread),
    )


def _old_anchor_line(
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if thread is not None and thread.original_line is not None:
        return thread.original_line
    return comment.original_line


def _new_anchor_line(
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if thread is not None and thread.line is not None:
        return thread.line
    return comment.line


def _anchor_line_for_side(
    comment: PRComment,
    target_side: Literal["old", "new", "auto"],
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if target_side == "old":
        old_line = _old_anchor_line(comment, thread=thread)
        if old_line is not None:
            return old_line
        return _new_anchor_line(comment, thread=thread)
    if target_side == "new":
        new_line = _new_anchor_line(comment, thread=thread)
        if new_line is not None:
            return new_line
        return _old_anchor_line(comment, thread=thread)
    if thread is not None and thread.anchor_line is not None:
        return thread.anchor_line
    return comment.anchor_line


def _resolve_pending_line_index(
    view: DiffView,
    comment: PendingReviewComment,
) -> int | None:
    file_old_map = getattr(view, "_line_index_by_file_old_number", {})
    file_new_map = getattr(view, "_line_index_by_file_new_number", {})
    if comment.side == "LEFT":
        idx = file_old_map.get((comment.path, comment.line))
        if idx is not None:
            return idx
        idx = view._line_index_by_old_number.get(comment.line)
        if idx is not None:
            return idx
        return _resolve_pending_line_index_from_rows(view, comment)
    idx = file_new_map.get((comment.path, comment.line))
    if idx is not None:
        return idx
    idx = view._line_index_by_new_number.get(comment.line)
    if idx is not None:
        return idx
    return _resolve_pending_line_index_from_rows(view, comment)


def _resolve_pending_line_index_from_rows(
    view: DiffView,
    comment: PendingReviewComment,
) -> int | None:
    for line in view._all_lines:
        if line.file_path and line.file_path != comment.path:
            continue
        if comment.side == "LEFT" and line.old_line_no == comment.line:
            return line.line_index
        if comment.side == "RIGHT" and line.new_line_no == comment.line:
            return line.line_index
    return None


def _resolve_line_index_from_diff_hunk(
    view: DiffView,
    comment: PRComment,
    target_side: Literal["old", "new", "auto"],
    anchor_line: int | None,
) -> int | None:
    if view._diff is None or not comment.diff_hunk:
        return None

    hunk_diff = parse_patch(comment.diff_hunk, comment.path)
    if not hunk_diff.hunks:
        return None

    best_hunk = None
    best_score = 0
    for target_hunk in hunk_diff.hunks:
        active_file = view._diff.filename
        for current_hunk in view._diff.hunks:
            if current_hunk.starts_file and current_hunk.file_path:
                active_file = current_hunk.file_path
            if comment.path and active_file != comment.path:
                continue
            score = _hunk_overlap_score(target_hunk, current_hunk)
            if score > best_score:
                best_score = score
                best_hunk = current_hunk

    if best_hunk is None:
        return None

    return _nearest_line_index_in_hunk(best_hunk, target_side, anchor_line)


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
    fallback_index: int | None = None
    fallback_distance: int | None = None
    primary_index: int | None = None
    primary_distance: int | None = None

    for line in hunk.lines:
        line_number = _line_number_for_side(line, target_side)
        if line_number is None:
            continue

        distance = 0 if anchor_line is None else abs(line_number - anchor_line)
        if fallback_index is None or (
            fallback_distance is not None and distance < fallback_distance
        ):
            fallback_index = line.line_index
            fallback_distance = distance

        if line.is_context:
            continue
        if primary_index is None or (
            primary_distance is not None and distance < primary_distance
        ):
            primary_index = line.line_index
            primary_distance = distance

    return primary_index if primary_index is not None else fallback_index


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


def mount_side_aware_widget(
    view: DiffView,
    container: VerticalScroll,
    widget: Widget,
    *,
    side: Literal["old", "new", "auto"],
    before: Widget | None = None,
) -> Widget:
    layout_widget = _build_side_aware_layout(view, widget, side=side)
    if before is not None:
        container.mount(layout_widget, before=before)
    else:
        container.mount(layout_widget)
    return layout_widget


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

    mount_before = before
    if (
        getattr(view, "_inline_comment_editor_line_index", None) == line_index
        and getattr(view, "_inline_comment_editor_draft_index", None) is None
    ):
        mount_before = (
            getattr(view, "_inline_comment_editor_layout_widget", None) or before
        )

    mounted: list[Widget] = []
    layout_widgets: list[Widget] = []
    for index, draft in enumerate(drafts):
        widget = _build_pending_draft_widget(draft, line_index=line_index, index=index)
        layout_widget = mount_side_aware_widget(
            view,
            container,
            widget,
            side=draft.anchor_side,
            before=mount_before,
        )
        mounted.append(widget)
        layout_widgets.append(layout_widget)

    view._pending_comment_widgets_by_line[line_index] = mounted
    view._pending_comment_layout_widgets_by_line[line_index] = layout_widgets


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
    layout_widgets: list[Widget] = []
    for thread in threads:
        widget = _build_inline_thread_widget(thread)
        root = thread.root_comment
        side = _comment_target_side(root, thread=thread) if root is not None else "auto"
        layout_widget = mount_side_aware_widget(
            view,
            container,
            widget,
            side=side,
            before=before,
        )
        mounted.append(widget)
        layout_widgets.append(layout_widget)

    view._comment_widgets_by_line[line_index] = mounted
    view._comment_layout_widgets_by_line[line_index] = layout_widgets


def _build_side_aware_layout(
    view: DiffView,
    widget: Widget,
    *,
    side: Literal["old", "new", "auto"],
) -> Widget:
    widget.styles.width = "1fr"
    if view.split:
        return _build_split_comment_layout(view, widget, side=side)
    return _build_unified_comment_layout(view, widget)


def _build_unified_comment_layout(view: DiffView, widget: Widget) -> Horizontal:
    return Horizontal(
        _spacer(view._unified_prefix_width_for_layout(), "diff-comment-gutter"),
        widget,
        classes="diff-comment-row diff-comment-row-unified",
    )


def _build_split_comment_layout(
    view: DiffView,
    widget: Widget,
    *,
    side: Literal["old", "new", "auto"],
) -> Horizontal:
    if side == "old":
        target_side: Literal["old", "new"] = "old"
    else:
        target_side = "new"
    old_pane = _split_comment_pane(
        view,
        widget if target_side == "old" else None,
        side="old",
    )
    new_pane = _split_comment_pane(
        view,
        widget if target_side == "new" else None,
        side="new",
    )
    return Horizontal(
        old_pane,
        new_pane,
        classes=f"diff-comment-row diff-comment-row-split split-container -{target_side}-side",
    )


def _split_comment_pane(
    view: DiffView,
    widget: Widget | None,
    *,
    side: Literal["old", "new"],
) -> Horizontal:
    pane_classes = f"split-pane diff-comment-pane -{side}-side"
    if widget is None:
        return Horizontal(classes=f"{pane_classes} diff-comment-empty-pane")
    return Horizontal(
        _spacer(_split_prefix_width_for_layout(view, side), "diff-comment-gutter"),
        widget,
        classes=pane_classes,
    )


def _split_prefix_width_for_layout(
    view: DiffView,
    side: Literal["old", "new"],
) -> int:
    if not view.show_line_numbers:
        return 2
    line_width = (
        view._old_line_number_width()
        if side == "old"
        else view._new_line_number_width()
    )
    return line_width + 2


def _spacer(width: int, classes: str) -> Static:
    spacer = Static("", classes=classes)
    spacer.styles.width = max(0, width)
    return spacer


# ---------------------------------------------------------------------------
# Cursor-based visual highlight
# ---------------------------------------------------------------------------


def comment_widgets_in_order(view: DiffView, line_index: int) -> list[Widget]:
    """Return ordered (drafts first, then threads) widgets attached to a line."""
    pending_widgets = view._pending_comment_widgets_by_line.get(line_index)
    comment_widgets = view._comment_widgets_by_line.get(line_index)
    if pending_widgets is None:
        if comment_widgets is None:
            return []
        return comment_widgets
    if comment_widgets is None:
        return pending_widgets

    widgets: list[Widget] = list(pending_widgets)
    widgets.extend(comment_widgets)
    return widgets


def _iter_comment_widgets_in_order(view: DiffView, line_index: int) -> Iterator[Widget]:
    pending_widgets = view._pending_comment_widgets_by_line.get(line_index)
    if pending_widgets is not None:
        yield from pending_widgets

    comment_widgets = view._comment_widgets_by_line.get(line_index)
    if comment_widgets is not None:
        yield from comment_widgets


def total_comments_at_line(view: DiffView, line_index: int) -> int:
    pending_widgets = view._pending_comment_widgets_by_line.get(line_index)
    comment_widgets = view._comment_widgets_by_line.get(line_index)
    pending_count = len(pending_widgets) if pending_widgets is not None else 0
    comment_count = len(comment_widgets) if comment_widgets is not None else 0
    return pending_count + comment_count


def active_comment_widget(view: DiffView, line_index: int) -> Widget | None:
    """Return the comment widget currently selected via _comment_cursor_index."""
    index = view._comment_cursor_index
    if index <= 0:
        return None

    pending_widgets = view._pending_comment_widgets_by_line.get(line_index)
    pending_count = len(pending_widgets) if pending_widgets is not None else 0
    if pending_widgets is not None and index <= pending_count:
        return pending_widgets[index - 1]

    thread_widgets = view._comment_widgets_by_line.get(line_index)
    thread_index = index - pending_count - 1
    if thread_widgets is not None and 0 <= thread_index < len(thread_widgets):
        return thread_widgets[thread_index]
    return None


def active_thread(view: DiffView, line_index: int) -> ReviewThread | None:
    index = view._comment_cursor_index
    if index <= 0:
        return None
    drafts = view._pending_comment_drafts_by_line.get(line_index)
    threads = view._comment_threads_by_line.get(line_index)
    if threads is None:
        return None
    draft_count = len(drafts) if drafts is not None else 0
    thread_index = index - 1 - draft_count
    if 0 <= thread_index < len(threads):
        return threads[thread_index]
    return None


def active_pending_draft(
    view: DiffView, line_index: int
) -> PendingReviewComment | None:
    index = view._comment_cursor_index
    if index <= 0:
        return None
    drafts = view._pending_comment_drafts_by_line.get(line_index)
    if drafts is None:
        return None
    if 1 <= index <= len(drafts):
        return drafts[index - 1]
    return None


def _clear_cursor_line_class(view: DiffView, line_index: int) -> None:
    for w in _iter_comment_widgets_in_order(view, line_index):
        w.remove_class("--cursor-line")


def update_cursor_highlight(view: DiffView, old_line: int, new_line: int) -> None:
    """Refresh `--cursor-line` highlight based on current `_comment_cursor_index`.

    When the cursor enters a diff line, no comment is highlighted (index = 0).
    Pressing j/k advances the index to step through pending drafts then threads.
    """
    if old_line != new_line:
        _clear_cursor_line_class(view, old_line)
    _clear_cursor_line_class(view, new_line)

    active = active_comment_widget(view, new_line)
    if active is not None:
        active.add_class("--cursor-line")


def try_toggle_current(view: DiffView) -> bool:
    """Toggle the currently selected comment (only when one is selected)."""
    target = active_comment_widget(view, view.cursor_line)
    if target is None:
        return False
    if isinstance(target, Collapsible):
        target.collapsed = not target.collapsed
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

    lookup_pane: Literal["old", "new"]
    if target_side == "old":
        lookup_pane = "old"
    elif target_side == "new":
        lookup_pane = "new"
    elif 0 <= line_index < len(view._all_lines):
        line = view._all_lines[line_index]
        lookup_pane = "old" if line.is_deleted or line.is_modified else "new"
    else:
        lookup_pane = view.cursor_pane
    target_row = view._row_for_line_and_pane(line_index, lookup_pane)

    if target_row is not None:
        view._jump_to_row_with_anchor(
            target_row,
            pane=target_pane,
            viewport_offset=2,
            update_active_pane=target_pane is not None,
        )
    else:
        _virtual._maybe_update_virtual_window(view, line_index)
        view._move_cursor(
            line=line_index,
            pane=target_pane,
            update_active_pane=target_pane is not None,
        )


# ---------------------------------------------------------------------------
# Resolve / unresolve
# ---------------------------------------------------------------------------


async def toggle_resolve(view: DiffView) -> None:
    thread = active_thread(view, view.cursor_line)
    if thread is None:
        threads = view._comment_threads_by_line.get(view.cursor_line)
        if not threads:
            view.post_message(
                Flash("No comment thread on this line", style="warning", duration=2.0)
            )
            return
        thread = threads[0]

    if not view.store:
        return

    thread_id = thread.id
    root_id = thread.root_comment_id
    new_resolved = not thread.is_resolved

    _update_thread_widget_resolved(view, view.cursor_line, thread, new_resolved)

    try:
        if new_resolved:
            success = await view.store.resolve_thread(thread_id, root_id)
        else:
            success = await view.store.unresolve_thread(thread_id, root_id)
    except Exception as e:
        _update_thread_widget_resolved(view, view.cursor_line, thread, not new_resolved)
        view.post_message(Flash(f"Error: {e}", style="error", duration=3.0))
        return

    if success:
        verb = "Resolved" if new_resolved else "Unresolved"
        view.post_message(Flash(f"{verb} thread", style="success", duration=2.0))
        return

    _update_thread_widget_resolved(view, view.cursor_line, thread, not new_resolved)
    view.post_message(Flash("Failed to toggle resolve", style="error", duration=3.0))


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
            w.set_resolved(
                is_resolved,
                title=_inline_thread_title(
                    thread.model_copy(update={"is_resolved": is_resolved})
                ),
            )
            break

    _virtual._rebuild_virtual_layout(view)


# ---------------------------------------------------------------------------
# Build inline thread widget (shared ReviewThreadItem with cursor-line CSS)
# ---------------------------------------------------------------------------


def estimate_pending_draft_height(draft: PendingReviewComment) -> int:
    body_lines = max(1, _count_body_lines(draft.body))
    return max(PENDING_DRAFT_HEIGHT_ESTIMATE, body_lines + 2)


def _count_body_lines(body: str) -> int:
    if not body:
        return 0

    count = 1
    index = 0
    body_length = len(body)
    while index < body_length:
        char = body[index]
        if char == "\r":
            if index + 1 < body_length and body[index + 1] == "\n":
                index += 2
            else:
                index += 1
            if index < body_length:
                count += 1
        elif char == "\n":
            index += 1
            if index < body_length:
                count += 1
        else:
            index += 1
    return count


def _build_pending_draft_widget(
    draft: PendingReviewComment,
    *,
    line_index: int,
    index: int,
) -> CommentCard:
    side = "left" if draft.side == "LEFT" else "right"
    return CommentCard(
        f"{draft.path}:{draft.line} (pending)",
        draft.body,
        id=f"pending-draft-{line_index}-{side}-{index}",
        classes="pending-draft --pending-draft",
    )


def _inline_thread_title(thread: ReviewThread) -> str:
    line_info = f":{thread.anchor_line}" if thread.anchor_line else ""
    file_icon = get_file_icon(thread.path)
    if thread.is_resolved:
        return f"✓ Resolved: {file_icon} {thread.path}{line_info}"
    return f"{file_icon} {thread.path}{line_info}"


def _build_inline_thread_widget(thread: ReviewThread) -> ReviewThreadItem:
    if thread.is_resolved:
        classes = "--thread --resolved --inline"
        collapsed = True
    else:
        classes = "--thread --inline"
        collapsed = False

    line_no = thread.anchor_line

    return ReviewThreadItem(
        title=_inline_thread_title(thread),
        path=thread.path,
        line=line_no,
        comments=thread.comments,
        diff_hunk="",
        is_resolved=thread.is_resolved,
        compact=False,
        show_diff_hunk=False,
        show_path_header=False,
        collapsed=collapsed,
        classes=classes,
        id=f"inline-thread-{thread.root_comment_id}",
    )
