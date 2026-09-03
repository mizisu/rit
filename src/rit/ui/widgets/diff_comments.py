"""Inline comment thread display and interaction for DiffView.

Follows the same module-function pattern as diff_search, diff_virtual, etc.
All public functions accept a DiffView instance as the first argument.

Comment display is cursor-based: DiffView keeps focus while comment widgets
receive a visual highlight (``--cursor-line`` class) when the cursor sits on
their parent diff line.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Iterator, Sequence
from collections.abc import Set as AbstractSet
from typing import TYPE_CHECKING, Literal

from textual.containers import Horizontal, VerticalScroll
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine
from rit.state.models import PendingReviewComment, PRComment, PRReview, ReviewThread
from rit.state.pending_review_visibility import (
    pending_review_hidden_ids,
    review_thread_is_pending_draft,
)
from rit.ui.icons import get_file_icon
from rit.ui.messages import Flash
from rit.ui.widgets import diff_layout as _layout
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.review_thread_card import ReviewThreadItem

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


__all__ = (
    "COLLAPSED_PENDING_DRAFT_HEIGHT",
    "COLLAPSED_THREAD_HEIGHT",
    "COMMENT_HEIGHT_ESTIMATE",
    "PENDING_DRAFT_HEIGHT_ESTIMATE",
    "active_comment_widget",
    "active_file_comment_widget",
    "active_file_pending_draft",
    "active_file_review_comment",
    "active_pending_draft",
    "active_review_comment",
    "active_thread",
    "build_comment_map",
    "clear_state",
    "comment_widgets_in_order",
    "estimate_pending_draft_height",
    "estimate_thread_height",
    "mount_comments_for_line",
    "mount_file_comments_for_hunk",
    "mount_pending_drafts_for_line",
    "mount_side_aware_widget",
    "next_comment",
    "pending_draft_is_collapsed",
    "prev_comment",
    "toggle_resolve",
    "total_comments_at_file_header",
    "total_comments_at_line",
    "try_toggle_current",
    "update_cursor_highlight",
    "update_file_comment_cursor_highlight",
)


# ---------------------------------------------------------------------------
# Height estimation for virtual layout
# ---------------------------------------------------------------------------

COLLAPSED_PENDING_DRAFT_HEIGHT = 1
COLLAPSED_THREAD_HEIGHT = 1
COMMENT_HEIGHT_ESTIMATE = 3  # header + ~2 body lines
PENDING_DRAFT_HEIGHT_ESTIMATE = 5
INLINE_COMMENT_MAX_WIDTH = 96


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
    view._pending_file_comment_drafts_by_path = {}
    view._file_comment_threads_by_path = {}


def build_comment_map(view: DiffView) -> None:
    clear_state(view)
    _prune_collapsed_pending_drafts(view)

    if not view.store or not view.current_file:
        return

    file_paths = _visible_comment_file_paths(view)
    if not file_paths:
        return

    for draft in _pending_comments_for_current_diff(view, file_paths):
        if draft.is_file_level:
            view._pending_file_comment_drafts_by_path.setdefault(draft.path, []).append(
                draft
            )
            continue
        line_index = _resolve_pending_line_index(view, draft)
        if line_index is not None:
            view._pending_comment_drafts_by_line.setdefault(line_index, []).append(
                draft
            )

    threads = _visible_review_threads_for_current_diff(view, file_paths)
    if not threads:
        view._comment_line_indices = _comment_line_indices_for_keys(
            view._pending_comment_drafts_by_line.keys()
        )
        return

    for thread in threads:
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
    if _thread_is_file_level(thread):
        view._file_comment_threads_by_path.setdefault(thread.path, []).append(thread)
        return

    line_index = _resolve_line_index(view, root, thread=thread)
    if line_index is None:
        return

    view._comment_threads_by_line.setdefault(line_index, []).append(thread)
    existing_side = view._comment_side_by_line.get(line_index)
    root_side = _comment_target_side(root, thread=thread)
    if existing_side is None or existing_side == "auto":
        view._comment_side_by_line[line_index] = root_side
    elif root_side != "auto" and existing_side != root_side:
        view._comment_side_by_line[line_index] = "auto"


def _visible_review_threads_for_current_diff(
    view: DiffView,
    file_paths: AbstractSet[str],
) -> list[ReviewThread]:
    selector = getattr(view.store, "visible_review_threads_for_paths", None)
    if callable(selector):
        threads = selector(file_paths)
        if isinstance(threads, list):
            return threads

    state = getattr(view.store, "state", None)
    raw_threads = getattr(state, "review_threads", [])
    if not isinstance(raw_threads, list):
        return []

    return [
        thread
        for thread in raw_threads
        if isinstance(thread, ReviewThread)
        and thread.path in file_paths
        and not _pending_review_thread_is_draft(view, thread)
    ]


def _pending_review_thread_is_draft(view: DiffView, thread: ReviewThread) -> bool:
    state = getattr(view.store, "state", None)
    raw_reviews = getattr(state, "reviews", [])
    if not isinstance(raw_reviews, list):
        raw_reviews = []
    reviews = [review for review in raw_reviews if isinstance(review, PRReview)]

    raw_drafts = getattr(state, "pending_review_comments", [])
    if not isinstance(raw_drafts, list):
        raw_drafts = []
    drafts = [draft for draft in raw_drafts if isinstance(draft, PendingReviewComment)]
    obsolete_ids = getattr(state, "obsolete_pending_review_ids", ())
    if not drafts and not reviews and not obsolete_ids:
        return False

    return review_thread_is_pending_draft(
        thread,
        drafts=drafts,
        hidden_review_ids=pending_review_hidden_ids(
            pending_review_id=getattr(state, "pending_review_id", None),
            reviews=reviews,
            obsolete_pending_review_ids=obsolete_ids,
        ),
        reviews=reviews,
    )


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


def _visible_comment_file_paths(view: DiffView) -> AbstractSet[str]:
    file_paths = _file_paths_for_current_diff(view)
    folded_paths = getattr(view, "_folded_file_paths", frozenset())
    if not folded_paths:
        return file_paths
    return file_paths - folded_paths


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
    if comment.original_line is not None:
        return comment.original_line
    return thread.original_line if thread is not None else None


def _new_anchor_line(
    comment: PRComment,
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if comment.line is not None:
        return comment.line
    return thread.line if thread is not None else None


def _start_line_for_side(
    comment: PRComment,
    target_side: Literal["old", "new", "auto"],
    *,
    thread: ReviewThread | None = None,
) -> int | None:
    if target_side == "old":
        if comment.original_start_line is not None:
            return comment.original_start_line
        if comment.start_line is not None:
            return comment.start_line
        if thread is None:
            return None
        return thread.original_start_line or thread.start_line
    if target_side == "new":
        if comment.start_line is not None:
            return comment.start_line
        if comment.original_start_line is not None:
            return comment.original_start_line
        if thread is None:
            return None
        return thread.start_line or thread.original_start_line
    comment_start = comment.start_line or comment.original_start_line
    if comment_start is not None:
        return comment_start
    return (
        thread.start_line or thread.original_start_line if thread is not None else None
    )


def _start_side_for_side(
    comment: PRComment,
    target_side: Literal["old", "new", "auto"],
    *,
    thread: ReviewThread | None = None,
) -> Literal["LEFT", "RIGHT"] | None:
    if thread is not None:
        if thread.start_diff_side == "LEFT":
            return "LEFT"
        if thread.start_diff_side == "RIGHT":
            return "RIGHT"
    if comment.start_side == "LEFT":
        return "LEFT"
    if comment.start_side == "RIGHT":
        return "RIGHT"
    if target_side == "old":
        return "LEFT"
    if target_side == "new":
        return "RIGHT"
    return None


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
    line_index: int | None = None,
    before: Widget | None = None,
) -> Widget:
    layout_widget = _build_side_aware_layout(
        view,
        widget,
        side=side,
        line_index=line_index,
    )
    if before is not None:
        container.mount(layout_widget, before=before)
    else:
        container.mount(layout_widget)
    return layout_widget


def mount_file_comments_for_hunk(
    view: DiffView,
    container: VerticalScroll,
    hunk_index: int,
    *,
    split: bool | None = None,
    before: Widget | None = None,
) -> None:
    if hunk_index in view._file_comment_annotation_widgets_by_hunk:
        return

    path = view._file_path_for_hunk(hunk_index)
    if path is None:
        return

    line_index = None
    if view._diff is not None and 0 <= hunk_index < len(view._diff.hunks):
        lines = view._diff.hunks[hunk_index].lines
        if lines:
            line_index = lines[0].line_index

    pending_widgets: list[Widget] = []
    comment_widgets: list[Widget] = []
    layout_widgets: list[Widget] = []
    previous_split = view._comment_layout_split_override
    if split is not None:
        view._comment_layout_split_override = split
    try:
        for index, draft in enumerate(
            view._pending_file_comment_drafts_by_path.get(path, ())
        ):
            widget, item = _build_pending_draft_item(
                draft,
                line_index=line_index or 0,
                index=index,
                view=view,
                widget_id=f"pending-file-draft-{hunk_index}-{index}",
            )
            layout_widget = mount_side_aware_widget(
                view,
                container,
                item,
                side="new",
                line_index=line_index,
                before=before,
            )
            pending_widgets.append(widget)
            layout_widgets.append(layout_widget)

        for thread in view._file_comment_threads_by_path.get(path, ()):
            item = _build_inline_thread_widget(thread)
            layout_widget = mount_side_aware_widget(
                view,
                container,
                item,
                side="new",
                line_index=line_index,
                before=before,
            )
            comment_widgets.append(item)
            layout_widgets.append(layout_widget)
    finally:
        view._comment_layout_split_override = previous_split

    if pending_widgets:
        view._pending_file_comment_widgets_by_hunk[hunk_index] = pending_widgets
    if comment_widgets:
        view._file_comment_widgets_by_hunk[hunk_index] = comment_widgets
    if layout_widgets:
        view._file_comment_annotation_widgets_by_hunk[hunk_index] = layout_widgets


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
        widget, item = _build_pending_draft_item(
            draft,
            line_index=line_index,
            index=index,
            view=view,
        )
        layout_widget = mount_side_aware_widget(
            view,
            container,
            item,
            side=draft.anchor_side,
            line_index=line_index,
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
            line_index=line_index,
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
    line_index: int | None,
) -> Widget:
    widget.styles.width = "1fr"
    widget.styles.max_width = INLINE_COMMENT_MAX_WIDTH
    use_split = view._comment_layout_split_override
    if use_split is None:
        use_split = view.split
    if use_split:
        return _build_split_comment_layout(view, widget, side=side)
    return _build_unified_comment_layout(view, widget, line_index=line_index)


def _build_unified_comment_layout(
    view: DiffView,
    widget: Widget,
    *,
    line_index: int | None,
) -> Horizontal:
    line = (
        view._all_lines[line_index]
        if line_index is not None and 0 <= line_index < len(view._all_lines)
        else None
    )
    return Horizontal(
        _spacer(view._unified_prefix_width_for_layout(line), "diff-comment-gutter"),
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
    line_width = (
        view._old_line_number_width()
        if side == "old"
        else view._new_line_number_width()
    )
    return _layout.split_prefix_width_for_layout(
        show_line_numbers=view.show_line_numbers,
        line_number_width=line_width,
    )


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
    if comment_widgets is None:
        return
    for widget in comment_widgets:
        yield widget
        if not isinstance(widget, ReviewThreadItem):
            continue
        for index in range(widget.comment_count):
            card = widget.comment_card_at(index)
            if card is not None:
                yield card


def _thread_entry_count(widget: Widget) -> int:
    if not isinstance(widget, ReviewThreadItem):
        return 1
    if widget.collapsed or widget.comment_count == 0:
        return 1
    return widget.comment_count


def _comment_target_index(
    pending_widgets: Sequence[Widget] | None,
    thread_widgets: Sequence[Widget] | None,
    target: Widget,
) -> int | None:
    offset = 0
    if pending_widgets is not None:
        for widget in pending_widgets:
            offset += 1
            if widget is target:
                return offset

    if thread_widgets is None:
        return None
    for widget in thread_widgets:
        if widget is target:
            return offset + 1
        if (
            isinstance(widget, ReviewThreadItem)
            and not widget.collapsed
            and widget.comment_count
        ):
            for comment_index in range(widget.comment_count):
                offset += 1
                if widget.comment_card_at(comment_index) is target:
                    return offset
        else:
            offset += 1
    return None


def select_comment_widget(view: DiffView, target: Widget) -> bool:
    line_indices = (
        view._pending_comment_widgets_by_line.keys()
        | view._comment_widgets_by_line.keys()
    )
    for line_index in line_indices:
        index = _comment_target_index(
            view._pending_comment_widgets_by_line.get(line_index),
            view._comment_widgets_by_line.get(line_index),
            target,
        )
        if index is None:
            continue
        side = view._comment_side_by_line.get(line_index, "auto")
        view._comment_cursor_index = 0
        view._move_cursor(
            line=line_index,
            pane=None if side == "auto" else side,
            update_active_pane=side != "auto",
        )
        view._comment_cursor_index = index
        update_cursor_highlight(view, view.cursor_line, view.cursor_line)
        view._update_line_cursor(line_index)
        return True

    hunk_indices = (
        view._pending_file_comment_widgets_by_hunk.keys()
        | view._file_comment_widgets_by_hunk.keys()
    )
    for hunk_index in hunk_indices:
        index = _comment_target_index(
            view._pending_file_comment_widgets_by_hunk.get(hunk_index),
            view._file_comment_widgets_by_hunk.get(hunk_index),
            target,
        )
        if index is None:
            continue
        view._set_file_header_selection(hunk_index)
        view._comment_cursor_index = index
        update_file_comment_cursor_highlight(view, hunk_index)
        return True
    return False


def total_comments_at_line(view: DiffView, line_index: int) -> int:
    pending_widgets = view._pending_comment_widgets_by_line.get(line_index)
    comment_widgets = view._comment_widgets_by_line.get(line_index)
    pending_count = len(pending_widgets) if pending_widgets is not None else 0
    if comment_widgets is None:
        return pending_count
    return pending_count + sum(
        _thread_entry_count(widget) for widget in comment_widgets
    )


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
    if thread_widgets is None:
        return None
    submitted_index = index - pending_count - 1
    for thread_index in range(len(thread_widgets)):
        widget = thread_widgets[thread_index]
        entry_count = _thread_entry_count(widget)
        if submitted_index >= entry_count:
            submitted_index -= entry_count
            continue
        if not isinstance(widget, ReviewThreadItem) or widget.collapsed:
            return widget
        return widget.comment_card_at(submitted_index) or widget
    return None


def _active_thread_position(
    view: DiffView,
    line_index: int,
) -> tuple[int, int | None, int] | None:
    index = view._comment_cursor_index
    if index <= 0:
        return None
    drafts = view._pending_comment_drafts_by_line.get(line_index)
    threads = view._comment_threads_by_line.get(line_index)
    if threads is None:
        return None
    draft_count = len(drafts) if drafts is not None else 0
    submitted_index = index - draft_count - 1
    if submitted_index < 0:
        return None

    thread_widgets = view._comment_widgets_by_line.get(line_index)
    entry_start = 0
    for thread_index, _thread in enumerate(threads):
        widget = (
            thread_widgets[thread_index]
            if thread_widgets is not None and thread_index < len(thread_widgets)
            else None
        )
        entry_count = _thread_entry_count(widget) if widget is not None else 1
        if submitted_index < entry_count:
            comment_index = (
                submitted_index
                if isinstance(widget, ReviewThreadItem) and not widget.collapsed
                else None
            )
            return thread_index, comment_index, entry_start
        submitted_index -= entry_count
        entry_start += entry_count
    return None


def active_thread(view: DiffView, line_index: int) -> ReviewThread | None:
    position = _active_thread_position(view, line_index)
    if position is None:
        return None
    thread_index, _, _ = position
    threads = view._comment_threads_by_line.get(line_index)
    if threads is None or thread_index >= len(threads):
        return None
    return threads[thread_index]


def active_review_comment(view: DiffView, line_index: int) -> PRComment | None:
    """Return the individually selected submitted review comment."""
    position = _active_thread_position(view, line_index)
    if position is None:
        return None
    thread_index, comment_index, _ = position
    if comment_index is None:
        return None

    thread_widgets = view._comment_widgets_by_line.get(line_index)
    if thread_widgets is not None and thread_index < len(thread_widgets):
        widget = thread_widgets[thread_index]
        if isinstance(widget, ReviewThreadItem):
            return widget.comment_at(comment_index)

    threads = view._comment_threads_by_line.get(line_index)
    if threads is None or thread_index >= len(threads):
        return None
    comments = threads[thread_index].comments
    if not 0 <= comment_index < len(comments):
        return None
    return comments[comment_index]


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


def _file_comment_path(view: DiffView, hunk_index: int) -> str | None:
    return view._file_path_for_hunk(hunk_index)


def total_comments_at_file_header(view: DiffView, hunk_index: int) -> int:
    pending_widgets = view._pending_file_comment_widgets_by_hunk.get(hunk_index)
    comment_widgets = view._file_comment_widgets_by_hunk.get(hunk_index)
    pending_count = len(pending_widgets) if pending_widgets is not None else 0
    if comment_widgets is None:
        return pending_count
    return pending_count + sum(
        _thread_entry_count(widget) for widget in comment_widgets
    )


def active_file_comment_widget(view: DiffView, hunk_index: int) -> Widget | None:
    index = view._comment_cursor_index
    if index <= 0:
        return None

    pending_widgets = view._pending_file_comment_widgets_by_hunk.get(hunk_index)
    pending_count = len(pending_widgets) if pending_widgets is not None else 0
    if pending_widgets is not None and index <= pending_count:
        return pending_widgets[index - 1]

    thread_widgets = view._file_comment_widgets_by_hunk.get(hunk_index)
    if thread_widgets is None:
        return None
    submitted_index = index - pending_count - 1
    for widget in thread_widgets:
        entry_count = _thread_entry_count(widget)
        if submitted_index >= entry_count:
            submitted_index -= entry_count
            continue
        if not isinstance(widget, ReviewThreadItem) or widget.collapsed:
            return widget
        return widget.comment_card_at(submitted_index) or widget
    return None


def active_file_pending_draft(
    view: DiffView,
    hunk_index: int,
) -> PendingReviewComment | None:
    index = view._comment_cursor_index
    path = _file_comment_path(view, hunk_index)
    if index <= 0 or path is None:
        return None
    drafts = view._pending_file_comment_drafts_by_path.get(path)
    if drafts is None or index > len(drafts):
        return None
    return drafts[index - 1]


def _active_file_thread_position(
    view: DiffView,
    hunk_index: int,
) -> tuple[int, int | None, int] | None:
    index = view._comment_cursor_index
    path = _file_comment_path(view, hunk_index)
    if index <= 0 or path is None:
        return None

    drafts = view._pending_file_comment_drafts_by_path.get(path)
    threads = view._file_comment_threads_by_path.get(path)
    if threads is None:
        return None
    draft_count = len(drafts) if drafts is not None else 0
    submitted_index = index - draft_count - 1
    if submitted_index < 0:
        return None

    thread_widgets = view._file_comment_widgets_by_hunk.get(hunk_index)
    entry_start = 0
    for thread_index, _thread in enumerate(threads):
        widget = (
            thread_widgets[thread_index]
            if thread_widgets is not None and thread_index < len(thread_widgets)
            else None
        )
        entry_count = _thread_entry_count(widget) if widget is not None else 1
        if submitted_index < entry_count:
            comment_index = (
                submitted_index
                if isinstance(widget, ReviewThreadItem) and not widget.collapsed
                else None
            )
            return thread_index, comment_index, entry_start
        submitted_index -= entry_count
        entry_start += entry_count
    return None


def active_file_review_comment(
    view: DiffView,
    hunk_index: int,
) -> PRComment | None:
    position = _active_file_thread_position(view, hunk_index)
    if position is None:
        return None
    thread_index, comment_index, _ = position
    if comment_index is None:
        return None

    thread_widgets = view._file_comment_widgets_by_hunk.get(hunk_index)
    if thread_widgets is not None and thread_index < len(thread_widgets):
        widget = thread_widgets[thread_index]
        if isinstance(widget, ReviewThreadItem):
            return widget.comment_at(comment_index)

    path = _file_comment_path(view, hunk_index)
    if path is None:
        return None
    threads = view._file_comment_threads_by_path.get(path)
    if threads is None or thread_index >= len(threads):
        return None
    comments = threads[thread_index].comments
    if not 0 <= comment_index < len(comments):
        return None
    return comments[comment_index]


def _thread_item_for_widget(widget: Widget) -> ReviewThreadItem | None:
    if isinstance(widget, ReviewThreadItem):
        return widget
    return next(
        (
            ancestor
            for ancestor in widget.ancestors
            if isinstance(ancestor, ReviewThreadItem)
        ),
        None,
    )


def _clear_cursor_line_class(view: DiffView, line_index: int) -> None:
    for widget in _iter_comment_widgets_in_order(view, line_index):
        widget.remove_class("--cursor-line")
        thread_item = _thread_item_for_widget(widget)
        if thread_item is not None:
            thread_item.remove_class("--cursor-line")


def _add_active_cursor_line_class(view: DiffView, line_index: int) -> None:
    active = active_comment_widget(view, line_index)
    if active is None:
        return
    active.add_class("--cursor-line")

    thread_item = _thread_item_for_widget(active)
    if thread_item is not None:
        thread_item.add_class("--cursor-line")
        return

    position = _active_thread_position(view, line_index)
    if position is None:
        return
    thread_index, _, _ = position
    thread_widgets = view._comment_widgets_by_line.get(line_index)
    if thread_widgets is None or thread_index >= len(thread_widgets):
        return
    thread_widget = thread_widgets[thread_index]
    if isinstance(thread_widget, ReviewThreadItem):
        thread_widget.add_class("--cursor-line")


def update_cursor_highlight(view: DiffView, old_line: int, new_line: int) -> None:
    """Refresh `--cursor-line` highlight based on current `_comment_cursor_index`.

    When the cursor enters a diff line, no comment is highlighted (index = 0).
    Pressing j/k advances the index to step through pending drafts then threads.
    """
    if old_line != new_line:
        _clear_cursor_line_class(view, old_line)
    _clear_cursor_line_class(view, new_line)

    _add_active_cursor_line_class(view, new_line)


def _iter_file_comment_widgets_in_order(
    view: DiffView,
    hunk_index: int,
) -> Iterator[Widget]:
    pending_widgets = view._pending_file_comment_widgets_by_hunk.get(hunk_index)
    if pending_widgets is not None:
        yield from pending_widgets

    comment_widgets = view._file_comment_widgets_by_hunk.get(hunk_index)
    if comment_widgets is None:
        return
    for widget in comment_widgets:
        yield widget
        if not isinstance(widget, ReviewThreadItem):
            continue
        for index in range(widget.comment_count):
            card = widget.comment_card_at(index)
            if card is not None:
                yield card


def _clear_file_comment_cursor_highlight(view: DiffView, hunk_index: int) -> None:
    for widget in _iter_file_comment_widgets_in_order(view, hunk_index):
        widget.remove_class("--cursor-line")
        thread_item = _thread_item_for_widget(widget)
        if thread_item is not None:
            thread_item.remove_class("--cursor-line")


def _add_active_file_comment_cursor_highlight(
    view: DiffView,
    hunk_index: int,
) -> None:
    active = active_file_comment_widget(view, hunk_index)
    if active is None:
        return
    active.add_class("--cursor-line")
    thread_item = _thread_item_for_widget(active)
    if thread_item is not None:
        thread_item.add_class("--cursor-line")


def update_file_comment_cursor_highlight(view: DiffView, hunk_index: int) -> None:
    """Refresh the selected file-level comment under a file header."""
    _clear_file_comment_cursor_highlight(view, hunk_index)
    _add_active_file_comment_cursor_highlight(view, hunk_index)


def _try_toggle_file_current(view: DiffView, hunk_index: int) -> bool:
    target = active_file_comment_widget(view, hunk_index)
    if target is None:
        return False

    draft = active_file_pending_draft(view, hunk_index)
    if isinstance(target, CommentCard) and draft is not None:
        thread_item = _thread_item_for_widget(target)
        if thread_item is None:
            target.toggle_collapsed()
            collapsed = target.collapsed
        else:
            collapsed = not thread_item.collapsed
            thread_item.collapsed = collapsed
            target.set_class(collapsed, "-collapsed")
        _set_pending_draft_collapsed(view, draft, collapsed=collapsed)
        from rit.ui.widgets import diff_virtual as _virtual

        _virtual._rebuild_virtual_layout(view)
        return True

    position = _active_file_thread_position(view, hunk_index)
    if position is not None:
        thread_index, comment_index, entry_start = position
        widgets = view._file_comment_widgets_by_hunk.get(hunk_index)
        if widgets is not None and thread_index < len(widgets):
            thread_widget = widgets[thread_index]
            if isinstance(thread_widget, ReviewThreadItem):
                thread_widget.collapsed = comment_index is not None
                if comment_index is not None:
                    path = _file_comment_path(view, hunk_index)
                    drafts = (
                        view._pending_file_comment_drafts_by_path.get(path)
                        if path is not None
                        else None
                    )
                    draft_count = len(drafts) if drafts is not None else 0
                    view._comment_cursor_index = draft_count + entry_start + 1
                update_file_comment_cursor_highlight(view, hunk_index)
                from rit.ui.widgets import diff_virtual as _virtual

                _virtual._rebuild_virtual_layout(view)
                return True

    if isinstance(target, Collapsible):
        target.collapsed = not target.collapsed
        return True
    if isinstance(target, CommentCard):
        target.toggle_collapsed()
        return True
    return False


def try_toggle_current(view: DiffView) -> bool:
    """Toggle the currently selected comment (only when one is selected)."""
    hunk_index = view._selected_file_header_hunk
    if hunk_index is not None:
        return _try_toggle_file_current(view, hunk_index)

    target = active_comment_widget(view, view.cursor_line)
    if target is None:
        return False

    draft = active_pending_draft(view, view.cursor_line)
    if isinstance(target, CommentCard) and draft is not None:
        thread_item = _thread_item_for_widget(target)
        if thread_item is None:
            target.toggle_collapsed()
            collapsed = target.collapsed
        else:
            collapsed = not thread_item.collapsed
            thread_item.collapsed = collapsed
            target.set_class(collapsed, "-collapsed")
        _set_pending_draft_collapsed(view, draft, collapsed=collapsed)
        from rit.ui.widgets import diff_virtual as _virtual

        _virtual._rebuild_virtual_layout(view)
        return True

    position = _active_thread_position(view, view.cursor_line)
    if position is not None:
        thread_index, comment_index, entry_start = position
        widgets = view._comment_widgets_by_line.get(view.cursor_line)
        if widgets is not None and thread_index < len(widgets):
            thread_widget = widgets[thread_index]
            if isinstance(thread_widget, ReviewThreadItem):
                thread_widget.collapsed = comment_index is not None
                if comment_index is not None:
                    drafts = view._pending_comment_drafts_by_line.get(view.cursor_line)
                    draft_count = len(drafts) if drafts is not None else 0
                    view._comment_cursor_index = draft_count + entry_start + 1
                _clear_cursor_line_class(view, view.cursor_line)
                _add_active_cursor_line_class(view, view.cursor_line)
                from rit.ui.widgets import diff_virtual as _virtual

                _virtual._rebuild_virtual_layout(view)
                return True

    if isinstance(target, Collapsible):
        target.collapsed = not target.collapsed
        return True
    if isinstance(target, CommentCard):
        target.toggle_collapsed()
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
    return max(PENDING_DRAFT_HEIGHT_ESTIMATE, body_lines + 3)


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


def _pending_draft_title(draft: PendingReviewComment) -> str:
    file_icon = get_file_icon(draft.path)
    location = (
        f"{draft.path} • entire file"
        if draft.is_file_level
        else f"{draft.path}:{_pending_draft_line_label(draft)}"
    )
    return f"{file_icon} {location} [#eed49f](pending)[/]"


def _build_pending_draft_item(
    draft: PendingReviewComment,
    *,
    line_index: int,
    index: int,
    view: DiffView | None = None,
    widget_id: str | None = None,
) -> tuple[CommentCard, ReviewThreadItem]:
    if widget_id is None:
        side = "left" if draft.side == "LEFT" else "right"
        widget_id = f"pending-draft-{line_index}-{side}-{index}"
    collapsed = view is not None and pending_draft_is_collapsed(view, draft)
    comment = PRComment(
        body=draft.body,
        path=draft.path,
        line=(
            draft.line if not draft.is_file_level and draft.side == "RIGHT" else None
        ),
        original_line=(
            draft.line if not draft.is_file_level and draft.side == "LEFT" else None
        ),
        start_line=draft.start_line if draft.side == "RIGHT" else None,
        original_start_line=draft.start_line if draft.side == "LEFT" else None,
        side="" if draft.is_file_level else draft.side,
        start_side="" if draft.is_file_level else draft.start_side or "",
        subject_type=draft.subject_type,
    )
    item = ReviewThreadItem(
        title=_pending_draft_title(draft),
        path=draft.path,
        line=None if draft.is_file_level else draft.line,
        comments=[comment],
        compact=False,
        show_diff_hunk=False,
        show_path_header=False,
        collapsed=collapsed,
        classes="--thread --inline pending-draft-thread",
        id=f"{widget_id}-thread",
    )
    widget = item.comment_card_at(0)
    if widget is None:
        raise RuntimeError("Pending review item did not create a comment card")
    widget.id = widget_id
    widget.add_class("pending-draft", "--pending-draft")
    widget.set_content("", draft.body)
    widget.set_class(collapsed, "-collapsed")
    return widget, item


def _build_pending_draft_widget(
    draft: PendingReviewComment,
    *,
    line_index: int,
    index: int,
    view: DiffView | None = None,
) -> CommentCard:
    widget, _ = _build_pending_draft_item(
        draft,
        line_index=line_index,
        index=index,
        view=view,
    )
    return widget


def pending_draft_is_collapsed(
    view: DiffView,
    draft: PendingReviewComment,
) -> bool:
    collapsed_drafts = getattr(view, "_collapsed_pending_drafts", None)
    return collapsed_drafts is not None and collapsed_drafts.get(id(draft)) is draft


def _set_pending_draft_collapsed(
    view: DiffView,
    draft: PendingReviewComment,
    *,
    collapsed: bool,
) -> None:
    collapsed_drafts = getattr(view, "_collapsed_pending_drafts", None)
    if collapsed_drafts is None:
        return

    draft_id = id(draft)
    if collapsed:
        collapsed_drafts[draft_id] = draft
    else:
        collapsed_drafts.pop(draft_id, None)


def _prune_collapsed_pending_drafts(view: DiffView) -> None:
    collapsed_drafts = getattr(view, "_collapsed_pending_drafts", None)
    if collapsed_drafts is None:
        return
    if not view.store:
        collapsed_drafts.clear()
        return

    pending_comments = list(getattr(view.store.state, "pending_review_comments", ()))
    available = {id(draft): draft for draft in pending_comments}
    retained: dict[int, PendingReviewComment] = {}
    unmatched: list[PendingReviewComment] = []

    for draft_id, draft in collapsed_drafts.items():
        if available.get(draft_id) is draft:
            retained[draft_id] = draft
            available.pop(draft_id)
        else:
            unmatched.append(draft)

    for previous in unmatched:
        replacement = _matching_pending_draft(previous, available.values())
        if replacement is None:
            continue
        replacement_id = id(replacement)
        retained[replacement_id] = replacement
        available.pop(replacement_id)

    view._collapsed_pending_drafts = retained


def _matching_pending_draft(
    previous: PendingReviewComment,
    candidates: Iterable[PendingReviewComment],
) -> PendingReviewComment | None:
    if previous.review_comment_id:
        for candidate in candidates:
            if candidate.review_comment_id == previous.review_comment_id:
                return candidate

    previous_key = _pending_draft_content_key(previous)
    for candidate in candidates:
        if _pending_draft_content_key(candidate) == previous_key:
            return candidate
    return None


def _pending_draft_content_key(
    draft: PendingReviewComment,
) -> tuple[str, int, str, int | None, str | None, str]:
    return (
        draft.path,
        draft.line,
        draft.side,
        draft.start_line,
        draft.start_side,
        draft.body,
    )


def _pending_draft_line_label(draft: PendingReviewComment) -> str:
    if draft.start_line is None:
        return str(draft.line)
    return f"{draft.start_line}-{draft.line}"


def _thread_is_file_level(thread: ReviewThread) -> bool:
    root = thread.root_comment
    return thread.is_file_level or (root is not None and root.is_file_level)


def _inline_thread_title(thread: ReviewThread) -> str:
    location = (
        f"{thread.path} • entire file"
        if _thread_is_file_level(thread)
        else f"{thread.path}:{thread.anchor_line}"
        if thread.anchor_line
        else thread.path
    )
    file_icon = get_file_icon(thread.path)
    if thread.is_resolved:
        return f"✓ Resolved: {file_icon} {location}"
    return f"{file_icon} {location}"


def _build_inline_thread_widget(thread: ReviewThread) -> ReviewThreadItem:
    if thread.is_resolved:
        classes = "--thread --resolved --inline"
        collapsed = True
    else:
        classes = "--thread --inline"
        collapsed = False

    line_no = None if _thread_is_file_level(thread) else thread.anchor_line

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
