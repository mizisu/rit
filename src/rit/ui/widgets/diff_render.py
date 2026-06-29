"""Line/hunk rendering, content building, and display for DiffView."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Literal

from rich.cells import cell_len
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Static

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile
from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets import diff_cursor_content as _cursor_content
from rit.ui.widgets import diff_full_file_preview as _full_preview
from rit.ui.widgets import diff_geometry as _geometry
from rit.ui.widgets import diff_header as _header
from rit.ui.widgets import diff_highlight as _hl
from rit.ui.widgets import diff_layout as _layout
from rit.ui.widgets import diff_location as _location
from rit.ui.widgets import diff_plan as _plan
from rit.ui.widgets import diff_prefix as _prefix
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_styles as _styles
from rit.ui.widgets import diff_virtual as _virtual
from rit.ui.widgets.diff_visual import (
    MISSING_SIDE_HATCH_STYLE,
    SyncedCodeScroll,
    missing_side_hatch_text,
)

if TYPE_CHECKING:
    from contextvars import ContextVar

    from rit.ui.widgets.diff_view import DiffView


__all__ = ("PREVIEW_PREFIX_WIDTH",)


def _get_render_request_context() -> ContextVar[int | None]:
    from rit.ui.widgets.diff_view import _RENDER_REQUEST_CONTEXT

    return _RENDER_REQUEST_CONTEXT


# ---------------------------------------------------------------------------
# Split / layout state
# ---------------------------------------------------------------------------


def _should_force_unified_for_current_file(view: DiffView) -> bool:
    file = _file_for_header(view, view.current_file) if view.current_file else None
    return _layout.should_force_unified_for_file(
        showing_full_file=view._showing_full_file,
        file=file,
        diff=view._diff,
        lines=view._all_lines,
    )


def _should_force_unified_for_hunk(hunk: DiffHunk) -> bool:
    return _layout.should_force_unified_for_hunk(hunk)


def _split_prefix_width_for_layout(
    view: DiffView,
    side: Literal["old", "new"],
) -> int:
    line_width = (
        _old_line_number_width(view) if side == "old" else _new_line_number_width(view)
    )
    return _layout.split_prefix_width_for_layout(
        show_line_numbers=view.show_line_numbers,
        line_number_width=line_width,
    )


def _can_fit_auto_split_content(view: DiffView) -> bool:
    if not view._all_lines:
        return True

    split_gap = 2
    required_width = (
        _split_prefix_width_for_layout(view, "old")
        + view._split_old_code_width
        + _split_prefix_width_for_layout(view, "new")
        + view._split_new_code_width
        + split_gap
        + split_gap
    )
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

    if view._all_lines:
        _ensure_rendered_rows_for_mode(view, split=view.split)

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
    rows = _rendered_rows_for_mode(view)
    view._rows_unified = rows.rows_unified
    view._rows_split = rows.rows_split
    view._row_lookup_unified = rows.row_lookup_unified
    view._row_lookup_split = rows.row_lookup_split
    view._rows_unified_ready = True
    view._rows_split_ready = True


def _ensure_rendered_rows_for_mode(view: DiffView, *, split: bool) -> None:
    if split:
        if view._rows_split_ready:
            return
        rows = _rendered_rows_for_mode(view, split=True)
        view._rows_split = rows.rows_split
        view._row_lookup_split = rows.row_lookup_split
        view._rows_split_ready = True
        return

    if view._rows_unified_ready:
        return
    rows = _rendered_rows_for_mode(view, split=False)
    view._rows_unified = rows.rows_unified
    view._row_lookup_unified = rows.row_lookup_unified
    view._rows_unified_ready = True


def _rendered_rows_for_mode(
    view: DiffView,
    *,
    split: bool | None = None,
) -> _plan.RenderedRowsPlan:
    if view._all_lines and len(view._hunk_index_by_line) == len(view._all_lines):
        return _plan.build_rendered_rows_from_lines(
            view._all_lines,
            view._hunk_index_by_line,
            split=split,
        )
    return _plan.build_rendered_rows(view._diff, split=split)


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
    view: DiffView, line_indices: Collection[int] | None = None
) -> None:
    if line_indices is None:
        view._base_code_content_cache.clear()
        view._base_code_content_cache_keys_by_line.clear()
        return
    for line_idx in line_indices:
        cache_keys = view._base_code_content_cache_keys_by_line.pop(line_idx, ())
        for cache_key in cache_keys:
            view._base_code_content_cache.pop(cache_key, None)


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
    """Build the diff header text for the current view state."""
    showing_full_file = bool(view.current_file and view._showing_full_file)
    file = _file_for_header(view, view.current_file) if view.current_file else None
    return _header.build_diff_header_text(
        current_file=view.current_file,
        file=file,
        showing_full_file=showing_full_file,
        preview_location=_full_preview_location_label(view)
        if showing_full_file
        else "",
    )


def _full_preview_location_label(view: DiffView) -> str:
    line = view._current_line()
    return _location.full_preview_location_label(
        line=line,
        total_lines=len(view._all_lines),
        diff=view._diff,
        hunk_index=view._get_hunk_index_for_line(line.line_index)
        if line is not None
        else None,
    )


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
    with view.app.batch_update():
        await new_content.remove_children()
        if request_token is not None and not view._is_current_render_request(
            request_token
        ):
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
    file = getattr(view, "_file", None)
    if isinstance(file, PRFile) and file.filename == path:
        return file

    store = getattr(view, "store", None)
    state = getattr(store, "state", None)
    files = getattr(state, "files", None)
    if isinstance(files, Sequence) and not isinstance(files, (str, bytes)):
        file = next(
            (
                candidate
                for candidate in files
                if isinstance(candidate, PRFile) and candidate.filename == path
            ),
            None,
        )
        if file is not None:
            return file

    files_by_filename = getattr(state, "files_by_filename", None)
    if isinstance(files_by_filename, Mapping):
        file = files_by_filename.get(path)
        if isinstance(file, PRFile):
            return file

    return None


def _change_stats_plain(additions: int, deletions: int) -> str:
    return _header.change_stats_plain(additions, deletions)


def _file_header_width_for_layout(view: DiffView, fallback_width: int) -> int:
    return _layout.file_header_width_for_layout(
        fallback_width=fallback_width,
        viewport_width=view.scrollable_content_region.width,
        split=view.split,
        unified_content_width=_unified_content_width_for_layout(view),
        old_split_prefix_width=_split_prefix_width_for_layout(view, "old"),
        old_split_code_width=view._split_old_code_width,
        new_split_prefix_width=_split_prefix_width_for_layout(view, "new"),
        new_split_code_width=view._split_new_code_width,
    )


def _aggregate_file_change_stats(view: DiffView, path: str) -> tuple[int, int]:
    planned_stats = getattr(view, "_file_change_stats", {})
    if path in planned_stats:
        return planned_stats[path]
    return _header.aggregate_file_change_stats(view._diff, path)


def _create_file_header_widget(
    view: DiffView,
    *,
    hunk_index: int,
    hunk: DiffHunk,
    split: bool | None = None,
) -> Widget:
    use_split = view.split if split is None else split
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
        _header.file_header_min_width(
            path=path,
            old_path=old_path,
            stats_plain=stats_plain,
        ),
    )
    prefix_plain = "▾ "
    path_budget = max(
        4,
        width - cell_len(prefix_plain) - cell_len(stats_plain) - 2,
    )
    text = _header.build_file_header_text(
        path=path,
        old_path=old_path,
        additions=additions,
        deletions=deletions,
        path_budget=path_budget,
    )

    header_widget = Static(
        text,
        classes=f"file-diff-header -{hunk.file_status}",
        id=f"file-header-{hunk_index}",
    )
    header_widget.styles.height = _geometry.FILE_DIFF_HEADER_HEIGHT
    header_widget.styles.min_height = _geometry.FILE_DIFF_HEADER_HEIGHT
    header_widget.styles.width = max(1, width)
    if not use_split:
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
    split: bool | None = None,
) -> Widget:
    use_split = view.split if split is None else split
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
    if not use_split:
        header_widget.styles.width = max(
            1,
            len(hunk_header) + 2,
            _unified_content_width_for_layout(view),
        )
        return header_widget
    return SyncedCodeScroll(
        header_widget,
        classes="split-hunk-header-scroll",
        on_scroll_x=view._sync_split_horizontal_scroll,
    )


def _hunk_lines_for_window(
    hunk: DiffHunk,
    window_start: int | None,
    window_end: int | None,
) -> Sequence[DiffLine]:
    return _geometry.hunk_lines_for_window(hunk, window_start, window_end)


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

    render_split = view.split and not _should_force_unified_for_hunk(hunk)

    if show_header and hunk.starts_file:
        container.mount(
            _create_file_header_widget(
                view,
                hunk_index=hunk_index,
                hunk=hunk,
                split=render_split,
            )
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
            split=render_split,
        )
        container.mount(hunk_header_widget)
        view._register_hunk_header_widget(hunk_index, hunk_header_widget)

    previous_comment_layout = view._comment_layout_split_override
    view._comment_layout_split_override = render_split
    try:
        if render_split:
            _render_hunk_split(view, container, lines)
        else:
            _render_hunk_unified(view, container, lines)
    finally:
        view._comment_layout_split_override = previous_comment_layout


PREVIEW_PREFIX_WIDTH = 7


def _old_line_number_width(view: DiffView) -> int:
    if not view.show_line_numbers:
        return 0
    planned_width = getattr(view, "_old_line_number_width_value", None)
    if isinstance(planned_width, int):
        return planned_width
    return _layout.line_number_width_for_layout(
        show_line_numbers=True,
        numbers=view._line_index_by_old_number.keys(),
    )


def _new_line_number_width(view: DiffView) -> int:
    if not view.show_line_numbers:
        return 0
    planned_width = getattr(view, "_new_line_number_width_value", None)
    if isinstance(planned_width, int):
        return planned_width
    return _layout.line_number_width_for_layout(
        show_line_numbers=True,
        numbers=view._line_index_by_new_number.keys(),
    )


def _unified_prefix_width_for_layout(view: DiffView) -> int:
    if view._showing_full_file:
        return _preview_prefix_width_for_layout(view)
    return _layout.unified_prefix_width_for_layout(
        show_line_numbers=view.show_line_numbers,
        old_line_number_width=_old_line_number_width(view),
        new_line_number_width=_new_line_number_width(view),
    )


def _preview_prefix_width_for_layout(view: DiffView) -> int:
    return _layout.preview_prefix_width_for_layout(
        show_line_numbers=view.show_line_numbers,
        new_line_number_width=_new_line_number_width(view),
    )


def _split_placeholder_content(
    view: DiffView,
    *,
    side: Literal["old", "new"],
) -> Content:
    side_width = (
        view._split_old_code_width if side == "old" else view._split_new_code_width
    )
    width = _layout.split_placeholder_width_for_layout(
        side_code_width=side_width,
        viewport_width=view.size.width,
    )
    return Content.styled(
        missing_side_hatch_text(width),
        MISSING_SIDE_HATCH_STYLE,
    )


def _build_unified_prefix_content(view: DiffView, line: DiffLine) -> Content:
    if view._showing_full_file:
        return _build_preview_prefix_content(view, line)
    return _prefix.build_unified_prefix_content(
        line,
        show_line_numbers=view.show_line_numbers,
        old_line_number_width=_old_line_number_width(view),
        new_line_number_width=_new_line_number_width(view),
    )


def _build_preview_prefix_content(view: DiffView, line: DiffLine) -> Content:
    return _prefix.build_preview_prefix_content(
        line,
        show_line_numbers=view.show_line_numbers,
        new_line_number_width=_new_line_number_width(view),
    )


def _unified_line_style(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> str:
    return _styles.unified_line_style(
        line,
        side=side,
        showing_full_file=view._showing_full_file,
    )


def _split_line_style(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> str:
    return _styles.split_line_style(
        line,
        side=side,
        word_diff_enabled=getattr(view, "word_diff_enabled", True),
    )


# ---------------------------------------------------------------------------
# Mount helpers
# ---------------------------------------------------------------------------


def _code_widths_for_layout(view: DiffView) -> tuple[int, int, int]:
    return _layout.code_widths_for_layout(view._all_lines)


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
    lines: Sequence[DiffLine],
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
    lines: Sequence[DiffLine],
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
    lines: Sequence[DiffLine],
) -> None:
    _mount_unified_lines(view, container, lines)


def _render_hunk_split(
    view: DiffView,
    container: VerticalScroll,
    lines: Sequence[DiffLine],
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
    return _styles.unified_code_classes(line, side=side)


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
    line_width = (
        _old_line_number_width(view) if side == "old" else _new_line_number_width(view)
    )
    return _prefix.build_split_prefix(
        line_no,
        prefix,
        show_line_numbers=view.show_line_numbers,
        line_number_width=line_width,
    )


def _build_split_prefix_content(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> Content:
    line_width = (
        _old_line_number_width(view) if side == "old" else _new_line_number_width(view)
    )
    return _prefix.build_split_prefix_content(
        line,
        side=side,
        show_line_numbers=view.show_line_numbers,
        line_number_width=line_width,
    )


def _split_side_missing(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> bool:
    return _styles.split_side_missing(line, side=side)


def _split_prefix_classes(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> str:
    return _styles.split_prefix_classes(line, side=side)


def _split_annotation_style(
    view: DiffView,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> str:
    return _styles.split_annotation_style(line, side=side)


def _split_code_classes(
    line: DiffLine,
    *,
    side: Literal["old", "new"],
    word_diff_enabled: bool,
) -> str:
    return _styles.split_code_classes(
        line,
        side=side,
        word_diff_enabled=word_diff_enabled,
    )


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
            return _split_placeholder_content(view, side=side)
        return None

    spec = _selection_spec_for_rendered_line(view, line.line_index)
    has_cursor = (
        view._diff_line_cursor_active(line.line_index)
        and view._cursor_side_for_line(line) == side
    )
    cursor_col = view.cursor_column if has_cursor else None

    if spec is not None:
        text = line.old_content if side == "old" else line.new_content
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


def _selection_spec_for_rendered_line(view: DiffView, line_index: int):
    if not getattr(view, "visual_mode", False):
        return None
    if getattr(view, "visual_anchor_line", None) is None:
        return None
    return view._compute_selection_spec_for_line(line_index)


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
    left_classes = _split_code_classes(
        line,
        side="old",
        word_diff_enabled=view.word_diff_enabled,
    )

    right_prefix = _build_split_prefix_content(view, line, side="new")
    right_content = _build_split_code_content(
        view,
        line,
        side="new",
        placeholder_when_missing=True,
    )
    if right_content is None:
        right_content = Content.empty()
    right_classes = _split_code_classes(
        line,
        side="new",
        word_diff_enabled=view.word_diff_enabled,
    )

    left_prefix_widget = Static(
        left_prefix,
        classes=_split_prefix_classes(line, side="old"),
    )
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

    right_prefix_widget = Static(
        right_prefix,
        classes=_split_prefix_classes(line, side="new"),
    )
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
    return _prefix.build_unified_modified_prefix_content(
        line,
        side=side,
        show_line_numbers=view.show_line_numbers,
        old_line_number_width=_old_line_number_width(view),
        new_line_number_width=_new_line_number_width(view),
    )


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
    view._base_code_content_cache_keys_by_line.setdefault(
        line_index,
        set(),
    ).add(cache_key)
    return cached


# ---------------------------------------------------------------------------
# Cursor display
# ---------------------------------------------------------------------------


def _update_line_cursor(view: DiffView, line_idx: int) -> None:
    if line_idx < 0 or line_idx >= len(view._all_lines):
        return
    if not view.is_mounted:
        return
    if _blocks._refresh_grouped_blocks_for_lines(view, (line_idx,)):
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
    return _cursor_content.apply_cursor_to_code_content(
        base_content,
        line_text=view._get_line_text(line, side),
        has_cursor=has_cursor,
        cursor_col=cursor_col,
    )


# ---------------------------------------------------------------------------
# Full-file diff builder (static)
# ---------------------------------------------------------------------------


def _build_full_file_diff(
    filename: str,
    content: str,
    *,
    source_diff: FileDiff | None = None,
) -> FileDiff:
    return _full_preview.build_full_file_diff(
        filename,
        content,
        source_diff=source_diff,
    )
