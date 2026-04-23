"""Line/hunk rendering, content building, and display for DiffView."""

from __future__ import annotations

from bisect import bisect_right
from typing import TYPE_CHECKING, Literal

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Static

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import FileViewedState
from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_highlight as _hl
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_virtual as _virtual
from rit.ui.widgets.diff_types import RenderedRow
from rit.ui.widgets.diff_visual import SyncedCodeScroll

if TYPE_CHECKING:
    from contextvars import ContextVar

    from rit.ui.widgets.diff_view import DiffView


def _get_render_request_context() -> ContextVar[int | None]:
    from rit.ui.widgets.diff_view import _RENDER_REQUEST_CONTEXT

    return _RENDER_REQUEST_CONTEXT


# ---------------------------------------------------------------------------
# Split / layout state
# ---------------------------------------------------------------------------


def _should_force_unified_for_current_file(view: DiffView) -> bool:
    if view._showing_full_file:
        return True
    if view._file is not None and view._file.status in {"added", "removed"}:
        return True
    diff = view._diff
    if diff is None:
        return False
    if diff.is_new or diff.is_deleted:
        return True
    all_lines = view._all_lines
    if not all_lines:
        return False
    return all(line.is_added for line in all_lines) or all(
        line.is_deleted for line in all_lines
    )


def _split_prefix_width_for_layout(
    view: DiffView,
    side: Literal["old", "new"],
) -> int:
    if not view.show_line_numbers:
        return 2
    line_width = (
        _old_line_number_width(view) if side == "old" else _new_line_number_width(view)
    )
    return line_width + 2


def _can_fit_auto_split_content(view: DiffView) -> bool:
    if not view._all_lines:
        return True

    old_prefix_width = _split_prefix_width_for_layout(view, "old")
    new_prefix_width = _split_prefix_width_for_layout(view, "new")
    max_old_width = max(
        (len(line.old_content) for line in view._all_lines if line.old_content),
        default=0,
    )
    max_new_width = max(
        (len(line.new_content) for line in view._all_lines if line.new_content),
        default=0,
    )
    split_gap = 2
    required_width = old_prefix_width + max_old_width + new_prefix_width + max_new_width
    required_width += split_gap
    required_width += split_gap
    return view.size.width >= required_width


def _update_split_state(view: DiffView) -> None:
    old_split = view.split

    if view.mode == "split":
        view.split = True
    elif view.mode == "unified":
        view.split = False
    else:
        view.split = (
            view.size.width >= view.LAYOUT.auto_split_min_width
            and _can_fit_auto_split_content(view)
        )

    if view.split and _should_force_unified_for_current_file(view):
        view.split = False

    if view.split:
        view.scroll_x = 0
        view._sync_split_horizontal_scroll(view._split_horizontal_scroll_x)

    if old_split != view.split and view._all_lines:
        _virtual._rebuild_virtual_layout(view)
        if view._virt.active:
            _virtual._set_virtual_window_from_viewport(view)

    if (
        old_split != view.split
        and view.is_mounted
        and view._diff is not None
        and not view._virt.render_pending
        and not view._suspend_split_state_rerender
    ):
        view.run_worker(
            view._run_render_diff_for_request(view._render_request_token),
            exclusive=True,
            name="diff-mode-rerender",
        )


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


def _row_kind_for_line(
    view: DiffView,
    line: DiffLine,
    *,
    modified_side: Literal["old", "new"] | None = None,
) -> Literal[
    "context",
    "added",
    "deleted",
    "modified-old",
    "modified-new",
]:
    if line.is_modified:
        return "modified-old" if modified_side == "old" else "modified-new"
    if line.is_added:
        return "added"
    if line.is_deleted:
        return "deleted"
    return "context"


def _rebuild_rendered_rows(view: DiffView) -> None:
    view._rows_unified = []
    view._rows_split = []
    view._row_lookup_unified = {}
    view._row_lookup_split = {}

    if view._diff is None:
        return

    for hunk_index, hunk in enumerate(view._diff.hunks):
        for line in hunk.lines:
            if line.is_modified:
                old_row = RenderedRow(
                    mode="unified",
                    row_index=len(view._rows_unified),
                    line_index=line.line_index,
                    hunk_index=hunk_index,
                    kind=_row_kind_for_line(view, line, modified_side="old"),
                    side="old",
                    anchor_id=f"line-{line.line_index}-old",
                    old_line_no=line.old_line_no,
                    new_line_no=line.new_line_no,
                )
                view._rows_unified.append(old_row)
                view._row_lookup_unified[(line.line_index, "old")] = old_row.row_index

                new_row = RenderedRow(
                    mode="unified",
                    row_index=len(view._rows_unified),
                    line_index=line.line_index,
                    hunk_index=hunk_index,
                    kind=_row_kind_for_line(view, line, modified_side="new"),
                    side="new",
                    anchor_id=f"line-{line.line_index}-new",
                    old_line_no=line.old_line_no,
                    new_line_no=line.new_line_no,
                )
                view._rows_unified.append(new_row)
                view._row_lookup_unified[(line.line_index, "new")] = new_row.row_index
            else:
                side: Literal["old", "new", "auto"]
                if line.is_deleted:
                    side = "old"
                elif line.is_added:
                    side = "new"
                else:
                    side = "auto"

                row = RenderedRow(
                    mode="unified",
                    row_index=len(view._rows_unified),
                    line_index=line.line_index,
                    hunk_index=hunk_index,
                    kind=_row_kind_for_line(view, line),
                    side=side,
                    anchor_id=f"line-{line.line_index}",
                    old_line_no=line.old_line_no,
                    new_line_no=line.new_line_no,
                )
                view._rows_unified.append(row)
                view._row_lookup_unified[(line.line_index, side)] = row.row_index

            split_row = RenderedRow(
                mode="split",
                row_index=len(view._rows_split),
                line_index=line.line_index,
                hunk_index=hunk_index,
                kind=_row_kind_for_line(view, line),
                side="auto",
                anchor_id=f"line-{line.line_index}",
                old_line_no=line.old_line_no,
                new_line_no=line.new_line_no,
            )
            view._rows_split.append(split_row)
            view._row_lookup_split[line.line_index] = split_row.row_index


# ---------------------------------------------------------------------------
# Layout metrics
# ---------------------------------------------------------------------------


def _comparison_heavy_ratio(view: DiffView) -> float:
    if not view._all_lines:
        return 0.0
    return view._modified_line_count / len(view._all_lines)


def _average_render_line_height(view: DiffView) -> float:
    if not view._all_lines or view._total_line_render_height <= 0:
        return 1.0
    return view._total_line_render_height / len(view._all_lines)


def _precompute_diff_data(view: DiffView) -> None:
    if view._diff is not None:
        _hl._highlight_diff_sync(view, view._diff)


def _invalidate_base_code_content_cache(
    view: DiffView, line_indices: set[int] | None = None
) -> None:
    if line_indices is None:
        view._base_code_content_cache.clear()
        return
    for line_idx in line_indices:
        for side in ("old", "new", "auto"):
            view._base_code_content_cache.pop((line_idx, side, ""), None)
            view._base_code_content_cache.pop((line_idx, side, " "), None)


def _render_height_for_line(view: DiffView, line: DiffLine) -> int:
    if not view.split and line.is_modified:
        return 2
    return 1


def _line_index_at_vertical_offset(view: DiffView, offset: int) -> int:
    if not view._all_lines:
        return 0
    clamped = max(0, min(offset, max(0, view._virtual_content_height - 1)))
    index = bisect_right(view._line_top_offsets, clamped) - 1
    if index < 0:
        return 0
    if clamped >= view._line_bottom_offsets[index] and index + 1 < len(view._all_lines):
        return index + 1
    return index


def _viewport_center_line(view: DiffView) -> int:
    if not view._all_lines:
        return 0
    viewport_height = max(1, view.scrollable_content_region.height)
    center_offset = int(
        view.scroll_y + view._dock_header_height() + viewport_height / 2
    )
    return _line_index_at_vertical_offset(view, center_offset)


def _get_rendered_line_bounds(view: DiffView) -> tuple[int, int]:
    if not view._all_lines:
        return 0, -1
    if view._virt.active:
        start = max(0, view._virt.rendered_start)
        end = min(len(view._all_lines) - 1, view._virt.rendered_end)
        return start, end
    return 0, len(view._all_lines) - 1


def _is_line_rendered(view: DiffView, line_idx: int) -> bool:
    if line_idx < 0 or line_idx >= len(view._all_lines):
        return False
    start, end = _get_rendered_line_bounds(view)
    return start <= line_idx <= end


def _should_render_hunk_header(
    view: DiffView,
    hunk_index: int,
    window_start: int,
    window_end: int,
) -> bool:
    if not (0 <= hunk_index < len(view._hunk_line_ranges)):
        return False
    _, hunk_start, hunk_end = view._hunk_line_ranges[hunk_index]
    if hunk_end < window_start or hunk_start > window_end:
        return False
    return window_start <= hunk_start <= window_end


# ---------------------------------------------------------------------------
# Render orchestration
# ---------------------------------------------------------------------------


def _build_header_text(view: DiffView) -> str:
    """Build the diff header text including viewed badge."""
    if not view.current_file:
        return "Select a file to view diff"

    badge = ""
    if view._file:
        state = view._file.viewer_viewed_state
        if state == FileViewedState.VIEWED:
            badge = "[green]✓ Viewed[/]  "
        elif state == FileViewedState.DISMISSED:
            badge = "[yellow]! Changed[/]  "
        else:
            badge = "[dim]○ Unviewed[/]  "

    if view._showing_full_file:
        return f"{badge}{view.current_file}  [dim italic]preview[/]"
    elif view._file:
        return (
            f"{badge}{view.current_file}  "
            f"[green]+{view._file.additions}[/] "
            f"[red]-{view._file.deletions}[/]"
        )
    return f"{badge}{view.current_file}"


async def _render_diff(view: DiffView) -> None:
    ctx = _get_render_request_context()
    request_token = ctx.get()
    if request_token is not None and not view._is_current_render_request(request_token):
        return
    header = view._header_widget
    if header is None:
        header = view.query_one("#diff-header", Static)
        view._header_widget = header
    view._update_status_line()

    new_content = view._content_widget
    if new_content is None:
        new_content = view.query_one("#diff-content", VerticalScroll)
        view._content_widget = new_content
    await new_content.remove_children()
    if request_token is not None and not view._is_current_render_request(request_token):
        return

    view._code_widgets_by_line = {}
    view._unified_blocks_by_line = {}
    view._split_blocks_by_line = {}
    view._line_widgets_by_index = {}
    view._inline_comment_editor_widget = None
    view._row_anchor_widgets = {}
    view._hunk_header_widgets = {}
    view._virt.top_buffer = None
    view._virt.bottom_buffer = None
    view._center_padding_widget = None
    view._center_padding_height = 0
    view._cursor_ui.suspend_pane_watch = False
    view._visual_selection_specs = {}

    if not view._diff or not view._diff.hunks:
        new_content.mount(Static("No changes in this file", classes="placeholder"))
    elif view._virt.active:
        _virtual._render_virtual_window(view, new_content)
    else:
        for hunk_index, hunk in enumerate(view._diff.hunks):
            _render_hunk(
                view,
                new_content,
                hunk,
                hunk_index=hunk_index,
                show_header=not view._showing_full_file,
            )

    if view._virt.active:
        view._virt.rendered_start = view._virt.window_start
        view._virt.rendered_end = view._virt.window_end
    else:
        view._virt.rendered_start = 0
        view._virt.rendered_end = len(view._all_lines) - 1

    if request_token is not None:
        view.call_after_refresh(
            lambda: view._finalize_render_state_if_current(request_token)
        )
    else:
        view.call_after_refresh(lambda: _finalize_render_state(view))


def _create_hunk_header_widget(
    view: DiffView,
    *,
    hunk_index: int,
    hunk_header: str,
) -> Widget:
    header_widget = Static(
        hunk_header,
        classes="hunk-header",
        id=f"hunk-{hunk_index}",
    )
    if not view.split:
        return header_widget
    header_widget.styles.width = max(1, len(hunk_header) + 2)
    return SyncedCodeScroll(
        header_widget,
        classes="split-hunk-header-scroll",
        on_scroll_x=view._sync_split_horizontal_scroll,
    )


def _render_hunk(
    view: DiffView,
    container: VerticalScroll,
    hunk: DiffHunk,
    *,
    hunk_index: int,
    window_start: int | None = None,
    window_end: int | None = None,
    show_header: bool = True,
) -> None:
    if window_start is not None and window_end is not None:
        lines = [
            line for line in hunk.lines if window_start <= line.line_index <= window_end
        ]
    else:
        lines = hunk.lines

    if not lines:
        return

    if show_header:
        hunk_header = (
            f"@@ -{hunk.old_start},{hunk.old_count} "
            f"+{hunk.new_start},{hunk.new_count} @@"
        )
        if hunk.header:
            hunk_header += f" {hunk.header}"
        hunk_header_widget = _create_hunk_header_widget(
            view,
            hunk_index=hunk_index,
            hunk_header=hunk_header,
        )
        container.mount(hunk_header_widget)
        view._register_hunk_header_widget(hunk_index, hunk_header_widget)

    if view.split:
        _render_hunk_split(view, container, lines)
    else:
        _render_hunk_unified(view, container, lines)


PREVIEW_PREFIX_WIDTH = 7


def _old_line_number_width(view: DiffView) -> int:
    if not view.show_line_numbers:
        return 0
    numbers = view._line_index_by_old_number
    return max(1, len(str(max(numbers)))) if numbers else 1


def _new_line_number_width(view: DiffView) -> int:
    if not view.show_line_numbers:
        return 0
    numbers = view._line_index_by_new_number
    return max(1, len(str(max(numbers)))) if numbers else 1


def _unified_prefix_width_for_layout(view: DiffView) -> int:
    if view._showing_full_file:
        return _preview_prefix_width_for_layout(view)
    if not view.show_line_numbers:
        return 2
    return _old_line_number_width(view) + _new_line_number_width(view) + 4


def _preview_prefix_width_for_layout(view: DiffView) -> int:
    if not view.show_line_numbers:
        return 2
    return _new_line_number_width(view) + 2


def _split_placeholder_content(view: DiffView) -> Content:
    placeholder_width = max(3, view.scrollable_content_region.width // 2)
    return Content.styled("╲" * placeholder_width, "$foreground 15%")


def _build_unified_prefix_content(view: DiffView, line: DiffLine) -> Content:
    if view._showing_full_file:
        return _build_preview_prefix_content(view, line)

    prefix_parts: list[Content] = []
    if view.show_line_numbers:
        old_width = _old_line_number_width(view)
        new_width = _new_line_number_width(view)
        old_no = str(line.old_line_no) if line.old_line_no else ""
        new_no = str(line.new_line_no) if line.new_line_no else ""
        prefix_parts.append(Content.styled(f"{old_no:>{old_width}} ", "$text-disabled"))
        prefix_parts.append(Content.styled(f"{new_no:>{new_width}} ", "$text-disabled"))

    prefix = " "
    if line.is_added:
        prefix = "+"
    elif line.is_deleted:
        prefix = "-"
    prefix_parts.append(Content(prefix + " "))
    return Content("").join(prefix_parts)


def _build_preview_prefix_content(view: DiffView, line: DiffLine) -> Content:
    line_no = str(line.new_line_no) if line.new_line_no else ""
    if not view.show_line_numbers:
        return Content("  ")
    line_width = _new_line_number_width(view)
    return Content.styled(f"{line_no:>{line_width}}  ", "$text-disabled")


def _unified_line_style(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> str:
    if side == "old" and line.is_modified:
        return "on $error 10%"
    if side == "new" and line.is_modified:
        return "on $success 10%"
    if line.is_added:
        return "on $success 10%"
    if line.is_deleted:
        return "on $error 10%"
    return ""


def _split_line_style(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> str:
    if side == "old" and (line.is_deleted or line.is_modified):
        return "on $error 10%"
    if side == "new" and (line.is_added or line.is_modified):
        return "on $success 10%"
    return ""


# ---------------------------------------------------------------------------
# Mount helpers
# ---------------------------------------------------------------------------


def _split_code_widths_for_layout(view: DiffView) -> tuple[int, int]:
    old_width = max(
        (
            _base_code_content(view, line, side="old", empty_fallback=" ").cell_length
            for line in view._all_lines
        ),
        default=1,
    )
    new_width = max(
        (
            _base_code_content(view, line, side="new", empty_fallback=" ").cell_length
            for line in view._all_lines
        ),
        default=1,
    )
    return old_width, new_width


def _mount_split_lines(
    view: DiffView,
    container: VerticalScroll,
    lines: list[DiffLine],
    *,
    before: Widget | None = None,
) -> None:
    old_code_width = view._split_old_code_width
    new_code_width = view._split_new_code_width

    if not _blocks._should_use_split_block_renderer(view):
        for line in lines:
            widget = _render_line_split(
                view,
                line,
                old_code_width=old_code_width,
                new_code_width=new_code_width,
            )
            if before is not None:
                container.mount(widget, before=before)
            else:
                container.mount(widget)
            view._mount_inline_comment_editor(
                container,
                line.line_index,
                before=before,
            )
            _comments.mount_pending_drafts_for_line(
                view, container, line.line_index, before=before
            )
            _comments.mount_comments_for_line(
                view, container, line.line_index, before=before
            )
        return

    chunk_limit = _blocks._block_chunk_limit(view)
    block_lines: list[DiffLine] = []
    for line in lines:
        if _blocks._can_render_in_split_block(view, line):
            block_lines.append(line)
            if chunk_limit is not None and len(block_lines) >= chunk_limit:
                _blocks._render_split_line_block(
                    view, container, block_lines, before=before
                )
                for block_line in block_lines:
                    view._mount_inline_comment_editor(
                        container,
                        block_line.line_index,
                        before=before,
                    )
                    _comments.mount_pending_drafts_for_line(
                        view,
                        container,
                        block_line.line_index,
                        before=before,
                    )
                    _comments.mount_comments_for_line(
                        view,
                        container,
                        block_line.line_index,
                        before=before,
                    )
                block_lines = []
            continue

        if block_lines:
            _blocks._render_split_line_block(
                view, container, block_lines, before=before
            )
            for block_line in block_lines:
                view._mount_inline_comment_editor(
                    container,
                    block_line.line_index,
                    before=before,
                )
                _comments.mount_pending_drafts_for_line(
                    view,
                    container,
                    block_line.line_index,
                    before=before,
                )
                _comments.mount_comments_for_line(
                    view,
                    container,
                    block_line.line_index,
                    before=before,
                )
            block_lines = []

        widget = _render_line_split(
            view,
            line,
            old_code_width=old_code_width,
            new_code_width=new_code_width,
        )
        if before is not None:
            container.mount(widget, before=before)
        else:
            container.mount(widget)
        view._mount_inline_comment_editor(
            container,
            line.line_index,
            before=before,
        )
        _comments.mount_pending_drafts_for_line(
            view, container, line.line_index, before=before
        )
        _comments.mount_comments_for_line(
            view, container, line.line_index, before=before
        )

    if block_lines:
        _blocks._render_split_line_block(view, container, block_lines, before=before)
        for block_line in block_lines:
            view._mount_inline_comment_editor(
                container,
                block_line.line_index,
                before=before,
            )
            _comments.mount_pending_drafts_for_line(
                view,
                container,
                block_line.line_index,
                before=before,
            )
            _comments.mount_comments_for_line(
                view,
                container,
                block_line.line_index,
                before=before,
            )


def _mount_unified_lines(
    view: DiffView,
    container: VerticalScroll,
    lines: list[DiffLine],
    *,
    before: Widget | None = None,
) -> None:
    if not _blocks._should_use_unified_block_renderer(view):
        for line in lines:
            widget = _render_line_unified(view, line)
            if before is not None:
                container.mount(widget, before=before)
            else:
                container.mount(widget)
            view._mount_inline_comment_editor(
                container,
                line.line_index,
                before=before,
            )
            _comments.mount_pending_drafts_for_line(
                view, container, line.line_index, before=before
            )
            _comments.mount_comments_for_line(
                view, container, line.line_index, before=before
            )
        return

    chunk_limit = _blocks._block_chunk_limit(view)
    block_lines: list[DiffLine] = []
    for line in lines:
        if _blocks._can_render_in_unified_block(view, line):
            block_lines.append(line)
            if chunk_limit is not None and len(block_lines) >= chunk_limit:
                _blocks._render_unified_line_block(
                    view, container, block_lines, before=before
                )
                for block_line in block_lines:
                    view._mount_inline_comment_editor(
                        container,
                        block_line.line_index,
                        before=before,
                    )
                    _comments.mount_pending_drafts_for_line(
                        view,
                        container,
                        block_line.line_index,
                        before=before,
                    )
                    _comments.mount_comments_for_line(
                        view,
                        container,
                        block_line.line_index,
                        before=before,
                    )
                block_lines = []
            continue

        if block_lines:
            _blocks._render_unified_line_block(
                view, container, block_lines, before=before
            )
            for block_line in block_lines:
                view._mount_inline_comment_editor(
                    container,
                    block_line.line_index,
                    before=before,
                )
                _comments.mount_pending_drafts_for_line(
                    view,
                    container,
                    block_line.line_index,
                    before=before,
                )
                _comments.mount_comments_for_line(
                    view,
                    container,
                    block_line.line_index,
                    before=before,
                )
            block_lines = []

        widget = _render_line_unified(view, line)
        if before is not None:
            container.mount(widget, before=before)
        else:
            container.mount(widget)
        view._mount_inline_comment_editor(
            container,
            line.line_index,
            before=before,
        )
        _comments.mount_pending_drafts_for_line(
            view, container, line.line_index, before=before
        )
        _comments.mount_comments_for_line(
            view, container, line.line_index, before=before
        )

    if block_lines:
        _blocks._render_unified_line_block(view, container, block_lines, before=before)
        for block_line in block_lines:
            view._mount_inline_comment_editor(
                container,
                block_line.line_index,
                before=before,
            )
            _comments.mount_pending_drafts_for_line(
                view,
                container,
                block_line.line_index,
                before=before,
            )
            _comments.mount_comments_for_line(
                view,
                container,
                block_line.line_index,
                before=before,
            )


def _render_hunk_unified(
    view: DiffView,
    container: VerticalScroll,
    lines: list[DiffLine],
) -> None:
    _mount_unified_lines(view, container, lines)


def _render_hunk_split(
    view: DiffView,
    container: VerticalScroll,
    lines: list[DiffLine],
) -> None:
    _mount_split_lines(view, container, lines)


def _get_hunk_index_for_line(view: DiffView, line_index: int) -> int | None:
    if 0 <= line_index < len(view._hunk_index_by_line):
        return view._hunk_index_by_line[line_index]
    return None


def _finalize_render_state(view: DiffView) -> None:
    if view.split:
        view._sync_split_horizontal_scroll(view._split_horizontal_scroll_x)
    _show_initial_cursor(view)
    if view.visual_mode:
        view._update_selection_highlighting({view.cursor_line})
    view._update_status_line()
    _hl._ensure_visible_highlight(view)

    pending = view._pending_comment_jump
    if pending is not None:
        view._pending_comment_jump = None
        indices = view._comment_line_indices
        if indices:
            line = indices[0] if pending == "first" else indices[-1]
            _comments._jump_to_comment_line(view, line)
        else:
            direction = 1 if pending == "first" else -1
            view.post_message(view.CrossFileComment(direction=direction))


# ---------------------------------------------------------------------------
# Line rendering — unified
# ---------------------------------------------------------------------------


def _unified_code_classes(
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> str:
    classes = "code-content"
    if side == "old" or line.is_deleted:
        if side == "old" or line.is_modified or line.is_deleted:
            classes += " -removed"
    elif side == "new" or line.is_added:
        if side == "new" or line.is_modified or line.is_added:
            classes += " -added"
    return classes


def _build_unified_code_content(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> Content:
    return _base_code_content(view, line, side=side, empty_fallback="")


def _render_line_unified(view: DiffView, line: DiffLine) -> Horizontal | Vertical:
    if line.is_modified:
        return _render_modified_line(view, line)

    prefix_content = _build_unified_prefix_content(view, line)
    side: Literal["old", "new", "auto"] = "auto"
    if line.is_added:
        side = "new"
    elif line.is_deleted:
        side = "old"
    code_content = _build_unified_code_content(view, line, side=side)
    code_classes = _unified_code_classes(line, side=side)

    prefix_widget = Static(prefix_content, classes="line-prefix")
    prefix_widget.styles.width = _unified_prefix_width_for_layout(view)
    code_widget = Static(code_content, classes=code_classes)

    container = Horizontal(
        prefix_widget,
        code_widget,
        classes="diff-line",
        id=f"line-{line.line_index}",
    )

    view._register_line_widget(line.line_index, container)
    view._register_row_anchor_widget(f"line-{line.line_index}", container)
    view._register_code_widgets(line.line_index, code_widget)
    return container


# ---------------------------------------------------------------------------
# Line rendering — split
# ---------------------------------------------------------------------------


def _build_split_prefix(
    view: DiffView,
    line_no: int | None,
    prefix: str,
    *,
    side: Literal["old", "new"],
    line_index: int,
) -> Content:
    parts: list[Content] = []
    if view.show_line_numbers:
        line_text = str(line_no) if line_no is not None else ""
        line_width = (
            _old_line_number_width(view)
            if side == "old"
            else _new_line_number_width(view)
        )
        parts.append(Content.styled(f"{line_text:>{line_width}} ", "$text-disabled"))
    parts.append(Content(prefix + " "))
    return Content("").join(parts)


def _build_split_prefix_content(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> Content:
    if side == "old":
        prefix = "-" if line.is_deleted or line.is_modified else " "
        line_no = line.old_line_no
    else:
        prefix = "+" if line.is_added or line.is_modified else " "
        line_no = line.new_line_no

    return _build_split_prefix(
        view,
        line_no,
        prefix,
        side=side,
        line_index=line.line_index,
    )


def _split_code_classes(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> str:
    classes = f"code-content -{side}-side"

    if side == "old":
        if line.is_deleted or line.is_modified:
            classes += " -removed"
        if line.is_added:
            classes += " -placeholder"
    else:
        if line.is_added or line.is_modified:
            classes += " -added"
        if line.is_deleted:
            classes += " -placeholder"

    return classes


def _build_split_code_content(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
    placeholder_when_missing: bool,
) -> Content | None:
    text = line.old_content if side == "old" else line.new_content
    if not text:
        if placeholder_when_missing:
            return _split_placeholder_content(view)
        return None

    spec = view._compute_selection_spec_for_line(line.line_index)
    has_cursor = line.line_index == view.cursor_line and view.active_pane == side
    cursor_col = view.cursor_column if has_cursor else None

    if spec is not None:
        sel_start, sel_end, _ = spec
        actual_end = sel_end if sel_end is not None else max(0, len(text) - 1)
        return view._build_code_content_with_selection(
            line,
            has_cursor,
            cursor_col,
            sel_start,
            actual_end,
            side=side,
        )

    return view._build_code_content_with_cursor(
        line,
        has_cursor,
        cursor_col,
        side=side,
    )


def _render_line_split(
    view: DiffView,
    line: DiffLine,
    *,
    old_code_width: int | None = None,
    new_code_width: int | None = None,
) -> Horizontal:
    left_prefix = _build_split_prefix_content(view, line, side="old")
    left_content = _build_split_code_content(
        view,
        line,
        side="old",
        placeholder_when_missing=True,
    )
    if left_content is None:
        left_content = Content.empty()
    left_classes = _split_code_classes(line, side="old")

    right_prefix = _build_split_prefix_content(view, line, side="new")
    right_content = _build_split_code_content(
        view,
        line,
        side="new",
        placeholder_when_missing=True,
    )
    if right_content is None:
        right_content = Content.empty()
    right_classes = _split_code_classes(line, side="new")

    left_prefix_widget = Static(left_prefix, classes="line-prefix")
    left_prefix_widget.styles.width = _split_prefix_width_for_layout(view, "old")
    left_code_widget = Static(left_content, classes=left_classes)
    if old_code_width is not None:
        left_code_widget.styles.width = old_code_width
    left_scroll = SyncedCodeScroll(
        left_code_widget,
        classes="split-code-scroll -old-side",
        on_scroll_x=view._sync_split_horizontal_scroll,
    )
    left_row = Horizontal(
        left_prefix_widget,
        left_scroll,
        classes="split-pane split-pane-left",
        id=f"line-{line.line_index}-old",
    )

    right_prefix_widget = Static(right_prefix, classes="line-prefix")
    right_prefix_widget.styles.width = _split_prefix_width_for_layout(view, "new")
    right_code_widget = Static(right_content, classes=right_classes)
    if new_code_width is not None:
        right_code_widget.styles.width = new_code_width
    right_scroll = SyncedCodeScroll(
        right_code_widget,
        classes="split-code-scroll -new-side",
        on_scroll_x=view._sync_split_horizontal_scroll,
    )
    right_row = Horizontal(
        right_prefix_widget,
        right_scroll,
        classes="split-pane split-pane-right",
        id=f"line-{line.line_index}-new",
    )

    view._register_code_widgets(line.line_index, left_code_widget, right_code_widget)
    view._register_split_scroll_widgets(line.line_index, left_scroll, right_scroll)
    container = Horizontal(
        left_row,
        right_row,
        classes="diff-line split-container",
        id=f"line-{line.line_index}",
    )
    view._register_line_widget(line.line_index, container)
    view._register_row_anchor_widget(f"line-{line.line_index}", container)
    return container


# ---------------------------------------------------------------------------
# Line rendering — modified (unified only)
# ---------------------------------------------------------------------------


def _build_unified_modified_prefix_content(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> Content:
    prefix_parts: list[Content] = []
    if view.show_line_numbers:
        old_width = _old_line_number_width(view)
        new_width = _new_line_number_width(view)
        if side == "old":
            prefix_parts.append(
                Content.styled(f"{line.old_line_no:>{old_width}} ", "$text-disabled")
            )
            prefix_parts.append(Content.styled(" " * (new_width + 1), "$text-disabled"))
        else:
            prefix_parts.append(Content.styled(" " * (old_width + 1), "$text-disabled"))
            prefix_parts.append(
                Content.styled(f"{line.new_line_no:>{new_width}} ", "$text-disabled")
            )
    prefix_parts.append(Content("- " if side == "old" else "+ "))
    return Content("").join(prefix_parts)


def _render_modified_line(view: DiffView, line: DiffLine) -> Vertical:
    old_prefix_content = _build_unified_modified_prefix_content(
        view,
        line,
        side="old",
    )

    old_code_content = _build_unified_code_content(view, line, side="old")

    old_prefix_widget = Static(old_prefix_content, classes="line-prefix")
    old_prefix_widget.styles.width = _unified_prefix_width_for_layout(view)
    old_code_widget = Static(
        old_code_content,
        classes=_unified_code_classes(line, side="old"),
    )
    old_horizontal = Horizontal(
        old_prefix_widget,
        old_code_widget,
        classes="diff-line",
        id=f"line-{line.line_index}-old",
    )

    new_prefix_content = _build_unified_modified_prefix_content(
        view,
        line,
        side="new",
    )

    new_code_content = _build_unified_code_content(view, line, side="new")

    new_prefix_widget = Static(new_prefix_content, classes="line-prefix")
    new_prefix_widget.styles.width = _unified_prefix_width_for_layout(view)
    new_code_widget = Static(
        new_code_content,
        classes=_unified_code_classes(line, side="new"),
    )
    new_horizontal = Horizontal(
        new_prefix_widget,
        new_code_widget,
        classes="diff-line",
        id=f"line-{line.line_index}-new",
    )

    view._register_code_widgets(line.line_index, old_code_widget, new_code_widget)

    container = Vertical(
        old_horizontal,
        new_horizontal,
        classes="diff-line",
        id=f"line-{line.line_index}",
    )
    view._register_line_widget(line.line_index, container)
    view._register_row_anchor_widget(f"line-{line.line_index}-old", old_horizontal)
    view._register_row_anchor_widget(f"line-{line.line_index}-new", new_horizontal)
    return container


# ---------------------------------------------------------------------------
# Code content helpers
# ---------------------------------------------------------------------------


def _compute_base_code_content(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
    empty_fallback: str = "",
) -> Content:
    if side == "old":
        if line.highlighted_old_content is not None:
            return line.highlighted_old_content
        return Content(line.old_content if line.old_content else empty_fallback)
    if side == "new":
        if line.highlighted_new_content is not None:
            return line.highlighted_new_content
        return Content(line.new_content if line.new_content else empty_fallback)
    if line.highlighted_new_content is not None:
        return line.highlighted_new_content
    if line.highlighted_old_content is not None:
        return line.highlighted_old_content

    text_content = view._get_line_text(line, side)
    return Content(text_content if text_content else empty_fallback)


def _base_code_content(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
    empty_fallback: str = "",
) -> Content:
    line_index = line.line_index
    if line_index < 0:
        return view._compute_base_code_content(
            line, side=side, empty_fallback=empty_fallback
        )
    cache_key = (line_index, side, empty_fallback)
    cached = view._base_code_content_cache.get(cache_key)
    if cached is not None:
        return cached
    cached = view._compute_base_code_content(
        line, side=side, empty_fallback=empty_fallback
    )
    view._base_code_content_cache[cache_key] = cached
    return cached


# ---------------------------------------------------------------------------
# Cursor display
# ---------------------------------------------------------------------------


def _update_line_cursor(view: DiffView, line_idx: int) -> None:
    if line_idx < 0 or line_idx >= len(view._all_lines):
        return
    if not view.is_mounted:
        return
    if _blocks._refresh_grouped_blocks_for_lines(view, {line_idx}):
        return
    code_widgets = view._get_code_widgets(line_idx)
    if not code_widgets:
        return
    line = view._all_lines[line_idx]
    has_cursor = line_idx == view.cursor_line

    for code_widget in code_widgets:
        if code_widget.has_class("-placeholder"):
            if code_widget.has_class("-cursor"):
                code_widget.remove_class("-cursor")
            continue

        side = view._get_line_side_for_widget(line, code_widget)
        show_cursor = has_cursor and view._widget_matches_cursor_side(line, code_widget)
        had_cursor = code_widget.has_class("-cursor")
        has_search = bool(view._search_query and view._search_matches)

        if not show_cursor and not had_cursor and not has_search:
            continue

        new_content = _build_code_content_with_cursor(
            view,
            line,
            show_cursor,
            view.cursor_column if show_cursor else None,
            side=side,
        )
        code_widget.update(new_content)

        if show_cursor:
            if not had_cursor:
                code_widget.add_class("-cursor")
        elif had_cursor:
            code_widget.remove_class("-cursor")


def _show_initial_cursor(view: DiffView) -> None:
    if not view._all_lines or not view.is_mounted:
        return
    _update_line_cursor(view, view.cursor_line)


def _build_code_content_with_cursor(
    view: DiffView,
    line: DiffLine,
    has_cursor: bool,
    cursor_col: int | None,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> Content:
    base_content = _base_code_content(view, line, side=side, empty_fallback=" ")
    base_content = _search.apply_search_highlights(
        view,
        base_content,
        line.line_index,
        side,
    )
    if not has_cursor or cursor_col is None:
        return base_content
    text_content = view._get_line_text(line, side)
    if not text_content:
        return Content(" ").stylize("reverse", 0, 1)
    if cursor_col >= len(text_content):
        return base_content
    return base_content.stylize("reverse", cursor_col, cursor_col + 1)


# ---------------------------------------------------------------------------
# Full-file diff builder (static)
# ---------------------------------------------------------------------------


def _build_full_file_diff(filename: str, content: str) -> FileDiff:
    raw_lines = content.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()

    diff_lines = [
        DiffLine(
            old_line_no=i + 1,
            new_line_no=i + 1,
            old_content=line,
            new_content=line,
        )
        for i, line in enumerate(raw_lines)
    ]

    hunk = DiffHunk(
        old_start=1,
        old_count=len(diff_lines),
        new_start=1,
        new_count=len(diff_lines),
        lines=diff_lines,
    )

    return FileDiff(filename=filename, hunks=[hunk])
