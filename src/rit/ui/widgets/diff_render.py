"""Line/hunk rendering, content building, and display for DiffView."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from rich.cells import cell_len
from rich.markup import escape
from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Static

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import FileViewedState, PRFile
from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_geometry as _geometry
from rit.ui.widgets import diff_highlight as _hl
from rit.ui.widgets import diff_plan as _plan
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_virtual as _virtual
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


def _has_only_added_deleted_changes(lines: list[DiffLine]) -> bool:
    has_change = False

    for line in lines:
        if line.is_modified:
            return False
        if line.is_added or line.is_deleted:
            has_change = True

    return has_change


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
    return diff.is_fully_refined and _has_only_added_deleted_changes(view._all_lines)


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


def _rebuild_rendered_rows(view: DiffView) -> None:
    rows = _plan.build_rendered_rows(view._diff)
    view._rows_unified = rows.rows_unified
    view._rows_split = rows.rows_split
    view._row_lookup_unified = rows.row_lookup_unified
    view._row_lookup_split = rows.row_lookup_split


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
    return _geometry.render_height_for_line(line, split=view.split)


def _line_index_at_vertical_offset(view: DiffView, offset: int) -> int:
    return _geometry.line_index_at_vertical_offset(
        line_top_offsets=view._line_top_offsets,
        line_bottom_offsets=view._line_bottom_offsets,
        virtual_content_height=view._virtual_content_height,
        offset=offset,
    )


def _viewport_center_line(view: DiffView) -> int:
    return _geometry.viewport_center_line(
        line_top_offsets=view._line_top_offsets,
        line_bottom_offsets=view._line_bottom_offsets,
        virtual_content_height=view._virtual_content_height,
        scroll_y=int(view.scroll_y),
        dock_header_height=view._dock_header_height(),
        viewport_height=view.scrollable_content_region.height,
    )


def _get_rendered_line_bounds(view: DiffView) -> tuple[int, int]:
    return _geometry.rendered_line_bounds(
        total_lines=len(view._all_lines),
        virtual_active=view._virt.active,
        rendered_start=view._virt.rendered_start,
        rendered_end=view._virt.rendered_end,
    )


def _is_line_rendered(view: DiffView, line_idx: int) -> bool:
    return _geometry.is_line_rendered(
        line_idx,
        total_lines=len(view._all_lines),
        virtual_active=view._virt.active,
        rendered_start=view._virt.rendered_start,
        rendered_end=view._virt.rendered_end,
    )


def _should_render_hunk_header(
    view: DiffView,
    hunk_index: int,
    window_start: int,
    window_end: int,
) -> bool:
    return _geometry.should_render_hunk_header(
        hunk_line_ranges=view._hunk_line_ranges,
        hunk_index=hunk_index,
        window_start=window_start,
        window_end=window_end,
    )


# ---------------------------------------------------------------------------
# Render orchestration
# ---------------------------------------------------------------------------


def _build_header_text(view: DiffView) -> str:
    """Build the diff header text including viewed badge."""
    if not view.current_file:
        return "Select a file to view diff"

    path = escape(view.current_file)
    status_parts: list[str] = []
    if view._file:
        state_badge, state_style = _viewed_state_badge(view._file)
        status_parts.extend(
            [
                f"[{state_style}]{state_badge}[/]",
                _change_stats_markup(view._file.additions, view._file.deletions),
            ]
        )

    if view._showing_full_file:
        location = _full_preview_location_label(view)
        status_parts.append("[dim italic]preview[/]")
        if location:
            status_parts.append(f"[dim]{escape(location)}[/]")

    if not status_parts:
        return f"[bold #cad3f5]{path}[/]"

    return f"[bold #cad3f5]{path}[/]  " + "  [dim]|[/]  ".join(status_parts)


def _full_preview_location_label(view: DiffView) -> str:
    line = view._current_line()
    if line is None:
        return ""

    line_no = line.new_line_no or line.old_line_no or line.line_index + 1
    total_lines = len(view._all_lines)
    label = f"line {line_no}/{total_lines}"

    diff = view._diff
    if diff is None or not diff.hunks:
        return label

    hunk_index = view._get_hunk_index_for_line(line.line_index)
    if hunk_index is None or not (0 <= hunk_index < len(diff.hunks)):
        return label

    hunk = diff.hunks[hunk_index]
    section = hunk.header.strip()
    if not section:
        section = f"section {hunk_index + 1}/{len(diff.hunks)}"
    return f"{label}  {section}"


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
    view._comment_widgets_by_line = {}
    view._comment_layout_widgets_by_line = {}
    view._pending_comment_widgets_by_line = {}
    view._pending_comment_layout_widgets_by_line = {}
    view._inline_comment_editor_widget = None
    view._inline_comment_editor_layout_widget = None
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
                show_header=True,
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


def _file_for_header(view: DiffView, path: str) -> PRFile | None:
    if view.store is not None:
        file = next(
            (
                candidate
                for candidate in view.store.state.files
                if candidate.filename == path
            ),
            None,
        )
        if file is not None:
            return file

        file = view.store.state.files_by_filename.get(path)
        if file is not None:
            return file

    if view._file is not None and view._file.filename == path:
        return view._file

    return None


def _viewed_state_badge(file: PRFile | None) -> tuple[str, str]:
    state = file.viewer_viewed_state if file is not None else FileViewedState.UNVIEWED
    if state == FileViewedState.VIEWED:
        return "✓ Viewed", "bold #a6da95"
    if state == FileViewedState.DISMISSED:
        return "! Changed", "bold #eed49f"
    return "● Unviewed", "#6e738d"


def _change_stats_markup(additions: int, deletions: int) -> str:
    parts: list[str] = []
    if deletions:
        parts.append(f"[bold #ed8796]-{deletions}[/]")
    if additions:
        parts.append(f"[bold #a6da95]+{additions}[/]")
    if not parts:
        return "[dim]no textual changes[/]"
    return " ".join(parts)


def _change_stats_plain(additions: int, deletions: int) -> str:
    parts: list[str] = []
    if deletions:
        parts.append(f"-{deletions}")
    if additions:
        parts.append(f"+{additions}")
    if not parts:
        return "no textual changes"
    return " ".join(parts)


def _append_change_stats(text: Text, additions: int, deletions: int) -> None:
    if deletions:
        text.append(f"-{deletions}", style="bold #ed8796")
        if additions:
            text.append(" ")
    if additions:
        text.append(f"+{additions}", style="bold #a6da95")
    if not additions and not deletions:
        text.append("no textual changes", style="dim")


def _file_header_width_for_layout(view: DiffView, fallback_width: int) -> int:
    viewport_width = view.scrollable_content_region.width
    if viewport_width > 0:
        return max(fallback_width, viewport_width - 8)
    if not view.split:
        return max(fallback_width, _unified_content_width_for_layout(view))
    split_width = (
        _split_prefix_width_for_layout(view, "old")
        + view._split_old_code_width
        + _split_prefix_width_for_layout(view, "new")
        + view._split_new_code_width
        + 4
    )
    return max(fallback_width, split_width)


def _truncate_middle(value: str, max_width: int) -> str:
    if cell_len(value) <= max_width:
        return value
    if max_width <= 3:
        return value[:max(0, max_width)]

    head_width = max(1, (max_width - 3) // 2)
    tail_width = max(1, max_width - 3 - head_width)
    return f"{value[:head_width]}...{value[-tail_width:]}"


def _aggregate_file_change_stats(view: DiffView, path: str) -> tuple[int, int]:
    diff = view._diff
    if diff is None:
        return 0, 0

    additions = 0
    deletions = 0
    active_path = diff.filename
    for hunk in diff.hunks:
        hunk_path = hunk.file_path or active_path
        for line in hunk.lines:
            line_path = line.file_path or hunk_path
            if line_path != path:
                continue
            if line.is_added or line.is_modified:
                additions += 1
            if line.is_deleted or line.is_modified:
                deletions += 1
    return additions, deletions


def _create_file_header_widget(
    view: DiffView,
    *,
    hunk_index: int,
    hunk: DiffHunk,
) -> Widget:
    path = hunk.file_path or "unknown"
    old_path = hunk.file_old_path
    file = _file_for_header(view, path)
    additions = file.additions if file is not None else hunk.file_additions
    deletions = file.deletions if file is not None else hunk.file_deletions
    if additions == 0 or deletions == 0:
        aggregate_additions, aggregate_deletions = _aggregate_file_change_stats(
            view, path
        )
        additions = max(additions, aggregate_additions)
        deletions = max(deletions, aggregate_deletions)

    stats_plain = _change_stats_plain(additions, deletions)
    width = _file_header_width_for_layout(
        view,
        cell_len(path) + cell_len(stats_plain) + 8,
    )
    prefix_plain = "▾ "
    path_budget = max(
        4,
        width - cell_len(prefix_plain) - cell_len(stats_plain) - 2,
    )
    display_path = f"{old_path} -> {path}" if old_path and old_path != path else path
    display_path = _truncate_middle(display_path, path_budget)

    text = Text()
    text.append("▾", style="#6e738d")
    text.append(" ")
    _append_change_stats(text, additions, deletions)
    text.append(" ")
    if old_path and old_path != path and display_path == f"{old_path} -> {path}":
        text.append(old_path, style="dim")
        text.append(" -> ", style="dim")
        text.append(path, style="bold #cad3f5")
    else:
        text.append(display_path, style="bold #cad3f5")

    header_widget = Static(
        text,
        classes=f"file-diff-header -{hunk.file_status}",
        id=f"file-header-{hunk_index}",
    )
    header_widget.styles.height = _geometry.FILE_DIFF_HEADER_HEIGHT
    header_widget.styles.min_height = _geometry.FILE_DIFF_HEADER_HEIGHT
    header_widget.styles.width = max(1, width)
    if not view.split:
        return header_widget
    header_scroll = SyncedCodeScroll(
        header_widget,
        classes="split-file-diff-header-scroll",
        on_scroll_x=view._sync_split_horizontal_scroll,
    )
    header_scroll.styles.height = _geometry.FILE_DIFF_HEADER_HEIGHT
    header_scroll.styles.min_height = _geometry.FILE_DIFF_HEADER_HEIGHT
    return header_scroll


def _create_hunk_header_widget(
    view: DiffView,
    *,
    hunk_index: int,
    hunk_header: str,
) -> Widget:
    classes = "hunk-header"
    if view._showing_full_file:
        classes += " preview-hunk-boundary"
    header_widget = Static(
        hunk_header,
        classes=classes,
        id=f"hunk-{hunk_index}",
    )
    if not view.split:
        header_widget.styles.width = max(
            1,
            len(hunk_header) + 2,
            _unified_content_width_for_layout(view),
        )
        return header_widget
    header_widget.styles.width = max(1, len(hunk_header) + 2)
    return SyncedCodeScroll(
        header_widget,
        classes="split-hunk-header-scroll",
        on_scroll_x=view._sync_split_horizontal_scroll,
    )


def _hunk_lines_for_window(
    hunk: DiffHunk,
    window_start: int | None,
    window_end: int | None,
) -> list[DiffLine]:
    if window_start is None or window_end is None:
        return hunk.lines
    if not hunk.lines or window_start > window_end:
        return []

    hunk_start = hunk.lines[0].line_index
    hunk_end = hunk.lines[-1].line_index
    if hunk_end < window_start or hunk_start > window_end:
        return []

    start_index = max(0, window_start - hunk_start)
    end_index = min(len(hunk.lines) - 1, window_end - hunk_start)
    return hunk.lines[start_index : end_index + 1]


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
    lines = _hunk_lines_for_window(hunk, window_start, window_end)
    if not lines:
        return

    if show_header and hunk.starts_file:
        container.mount(
            _create_file_header_widget(view, hunk_index=hunk_index, hunk=hunk)
        )

    if show_header and (view._diff is None or view._diff.show_hunk_headers):
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
        return 3
    return _new_line_number_width(view) + 4


def _split_placeholder_content(view: DiffView) -> Content:
    return Content(" ")


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
    delete_marker = (
        Content.styled("▸", "$error")
        if line.preview_deleted_before
        else Content(" ")
    )
    change_marker = _preview_change_marker_content(line)
    if not view.show_line_numbers:
        return Content("").join([delete_marker, change_marker, Content(" ")])
    line_width = _new_line_number_width(view)
    return Content("").join(
        [
            Content.styled(f"{line_no:>{line_width}} ", "$text-disabled"),
            delete_marker,
            change_marker,
            Content(" "),
        ]
    )


def _preview_change_marker_content(line: DiffLine) -> Content:
    if line.preview_change == "added":
        return Content.styled("┃", "$success")
    if line.preview_change == "modified":
        return Content.styled("┃", "$warning")
    return Content(" ")


def _unified_line_style(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> str:
    if view._showing_full_file:
        return ""
    if side == "old" and line.is_modified:
        return "on $error 6%"
    if side == "new" and line.is_modified:
        return "on $success 6%"
    if line.is_added:
        return "on $success 6%"
    if line.is_deleted:
        return "on $error 6%"
    return ""


def _split_line_style(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> str:
    if side == "old" and (line.is_deleted or line.is_modified):
        return "on $error 6%"
    if side == "new" and (line.is_added or line.is_modified):
        return "on $success 6%"
    return ""


# ---------------------------------------------------------------------------
# Mount helpers
# ---------------------------------------------------------------------------


def _line_code_width(line: DiffLine, side: Literal["old", "new"]) -> int:
    text = line.old_content if side == "old" else line.new_content
    return max(1, cell_len(text)) if text else 1


def _code_widths_for_layout(view: DiffView) -> tuple[int, int, int]:
    old_width = 1
    new_width = 1
    for line in view._all_lines:
        old_width = max(old_width, _line_code_width(line, "old"))
        new_width = max(new_width, _line_code_width(line, "new"))
    return max(old_width, new_width), old_width, new_width


def _unified_code_width_for_layout(view: DiffView) -> int:
    unified_width, _, _ = _code_widths_for_layout(view)
    return unified_width


def _unified_content_width_for_layout(view: DiffView) -> int:
    return _unified_prefix_width_for_layout(view) + view._unified_code_width


def _split_code_widths_for_layout(view: DiffView) -> tuple[int, int]:
    _, old_width, new_width = _code_widths_for_layout(view)
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
    code_widget.styles.width = view._unified_code_width

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
    has_side = line.has_old_side if side == "old" else line.has_new_side
    if not has_side:
        if placeholder_when_missing:
            return _split_placeholder_content(view)
        return None

    text = line.old_content if side == "old" else line.new_content
    spec = view._compute_selection_spec_for_line(line.line_index)
    has_cursor = (
        view._diff_line_cursor_active(line.line_index)
        and view._cursor_side_for_line(line) == side
    )
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
    old_code_widget.styles.width = view._unified_code_width
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
    new_code_widget.styles.width = view._unified_code_width
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
        if line.has_old_side:
            return Content(line.old_content)
        return Content(empty_fallback)
    if side == "new":
        if line.highlighted_new_content is not None:
            return line.highlighted_new_content
        if line.has_new_side:
            return Content(line.new_content)
        return Content(empty_fallback)
    if line.has_new_side and line.highlighted_new_content is not None:
        return line.highlighted_new_content
    if line.has_old_side and line.highlighted_old_content is not None:
        return line.highlighted_old_content

    if line.has_new_side or line.has_old_side:
        return Content(view._get_line_text(line, side))
    return Content(empty_fallback)


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
    has_cursor = view._diff_line_cursor_active(line_idx)

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


def _build_full_file_diff(
    filename: str,
    content: str,
    *,
    source_diff: FileDiff | None = None,
) -> FileDiff:
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

    if source_diff is not None:
        _apply_full_preview_change_markers(diff_lines, source_diff)

    hunks = _build_full_file_preview_hunks(
        filename,
        diff_lines,
        source_diff=source_diff,
    )

    return FileDiff(filename=filename, hunks=hunks, show_hunk_headers=False)


def _apply_full_preview_change_markers(
    diff_lines: list[DiffLine],
    source_diff: FileDiff,
) -> None:
    if not diff_lines:
        return

    lines_by_new_number = {
        line.new_line_no: line for line in diff_lines if line.new_line_no is not None
    }
    total_lines = len(diff_lines)

    for hunk in source_diff.hunks:
        pending_deletion = False
        last_new_line_no: int | None = None

        for source_line in hunk.lines:
            if source_line.is_deleted:
                pending_deletion = True
                continue

            new_line_no = source_line.new_line_no
            if new_line_no is None:
                continue

            if pending_deletion:
                _mark_full_preview_deleted_before(lines_by_new_number, new_line_no)
                pending_deletion = False

            target_line = lines_by_new_number.get(new_line_no)
            if target_line is not None:
                if source_line.is_modified:
                    target_line.preview_change = "modified"
                elif source_line.is_added:
                    target_line.preview_change = "added"

            last_new_line_no = new_line_no

        if pending_deletion:
            anchor_line_no = _full_preview_deleted_anchor_line(
                hunk,
                total_lines,
                last_new_line_no,
            )
            if anchor_line_no is not None:
                _mark_full_preview_deleted_before(
                    lines_by_new_number,
                    anchor_line_no,
                )


def _mark_full_preview_deleted_before(
    lines_by_new_number: dict[int, DiffLine],
    line_no: int,
) -> None:
    line = lines_by_new_number.get(line_no)
    if line is not None:
        line.preview_deleted_before = True


def _full_preview_deleted_anchor_line(
    hunk: DiffHunk,
    total_lines: int,
    last_new_line_no: int | None,
) -> int | None:
    if total_lines <= 0:
        return None
    if last_new_line_no is not None:
        return min(total_lines, max(1, last_new_line_no + 1))
    return min(total_lines, max(1, hunk.new_start))


def _build_full_file_preview_hunks(
    filename: str,
    diff_lines: list[DiffLine],
    *,
    source_diff: FileDiff | None,
) -> list[DiffHunk]:
    if not diff_lines:
        return [
            DiffHunk(
                old_start=0,
                old_count=0,
                new_start=0,
                new_count=0,
                header="empty file",
                lines=[],
                starts_file=True,
                file_path=filename,
            )
        ]

    if source_diff is None or not source_diff.hunks:
        return [
            DiffHunk(
                old_start=1,
                old_count=len(diff_lines),
                new_start=1,
                new_count=len(diff_lines),
                header="full file",
                lines=diff_lines,
                starts_file=True,
                file_path=filename,
                file_additions=source_diff.total_additions if source_diff else 0,
                file_deletions=source_diff.total_deletions if source_diff else 0,
            )
        ]

    change_ranges = _full_preview_change_ranges(source_diff, len(diff_lines))
    if not change_ranges:
        return [
            DiffHunk(
                old_start=1,
                old_count=len(diff_lines),
                new_start=1,
                new_count=len(diff_lines),
                header="full file",
                lines=diff_lines,
                starts_file=True,
                file_path=filename,
                file_additions=source_diff.total_additions,
                file_deletions=source_diff.total_deletions,
            )
        ]

    hunks: list[DiffHunk] = []
    cursor = 1
    total_changes = len(change_ranges)

    for range_index, (start, end, source_hunk) in enumerate(change_ranges, start=1):
        if cursor < start:
            hunks.append(
                _make_full_preview_hunk(
                    filename,
                    diff_lines,
                    start=cursor,
                    end=start - 1,
                    header=_full_preview_context_label(range_index, total_changes),
                    starts_file=not hunks,
                    source_diff=source_diff,
                )
            )

        hunks.append(
            _make_full_preview_hunk(
                filename,
                diff_lines,
                start=start,
                end=end,
                header=_full_preview_change_label(
                    range_index,
                    total_changes,
                    source_hunk.header,
                ),
                starts_file=not hunks,
                source_diff=source_diff,
                old_start=source_hunk.old_start,
                old_count=source_hunk.old_count,
                new_start=source_hunk.new_start,
                new_count=source_hunk.new_count,
            )
        )
        cursor = end + 1

    if cursor <= len(diff_lines):
        hunks.append(
            _make_full_preview_hunk(
                filename,
                diff_lines,
                start=cursor,
                end=len(diff_lines),
                header=f"context after hunk {total_changes}",
                starts_file=not hunks,
                source_diff=source_diff,
            )
        )

    return hunks


def _full_preview_change_ranges(
    source_diff: FileDiff,
    total_lines: int,
) -> list[tuple[int, int, DiffHunk]]:
    ranges: list[tuple[int, int, DiffHunk]] = []
    next_start = 1

    for hunk in source_diff.hunks:
        if hunk.new_count <= 0:
            start = max(1, min(hunk.new_start, total_lines))
            end = start
        else:
            start = max(1, min(hunk.new_start, total_lines))
            end = max(start, min(hunk.new_start + hunk.new_count - 1, total_lines))

        if start < next_start:
            start = next_start
        if start > total_lines:
            break
        if end < start:
            end = start

        ranges.append((start, end, hunk))
        next_start = end + 1

    return ranges


def _full_preview_context_label(range_index: int, total_changes: int) -> str:
    if range_index == 1:
        return "context before hunk 1"
    return f"context between hunks {range_index - 1}-{range_index}"


def _full_preview_change_label(
    range_index: int,
    total_changes: int,
    header: str,
) -> str:
    label = f"change hunk {range_index}/{total_changes}"
    header = header.strip()
    if header:
        label += f"  {header}"
    return label


def _make_full_preview_hunk(
    filename: str,
    diff_lines: list[DiffLine],
    *,
    start: int,
    end: int,
    header: str,
    starts_file: bool,
    source_diff: FileDiff,
    old_start: int | None = None,
    old_count: int | None = None,
    new_start: int | None = None,
    new_count: int | None = None,
) -> DiffHunk:
    count = max(0, end - start + 1)
    return DiffHunk(
        old_start=old_start if old_start is not None else start,
        old_count=old_count if old_count is not None else count,
        new_start=new_start if new_start is not None else start,
        new_count=new_count if new_count is not None else count,
        header=header,
        lines=diff_lines[start - 1 : end],
        starts_file=starts_file,
        file_path=filename if starts_file else None,
        file_additions=source_diff.total_additions,
        file_deletions=source_diff.total_deletions,
    )
