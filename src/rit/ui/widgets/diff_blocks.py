"""Grouped block rendering for DiffView (unified/split)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual.containers import VerticalScroll
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Static

from rit.core.types import DiffLine
from rit.ui.widgets.diff_types import (
    SplitBlockLineStaticData,
    SplitDiffBlock,
    UnifiedBlockRowStaticData,
    UnifiedDiffBlock,
)
from rit.ui.widgets.diff_visual import DiffCode, LineAnnotations, LineContent

if TYPE_CHECKING:
    from rit.ui.widgets.diff_view import DiffView


def _invalidate_block_static_row_cache(
    view, line_indices: set[int] | None = None
) -> None:
    if line_indices is None:
        view._unified_block_static_rows_by_line.clear()
        view._split_block_static_rows_by_line.clear()
        return

    for line_idx in line_indices:
        view._unified_block_static_rows_by_line.pop(line_idx, None)
        view._split_block_static_rows_by_line.pop(line_idx, None)


def _should_use_unified_block_renderer(view) -> bool:
    return (
        (view._virt.active or len(view._all_lines) >= view.BLOCK_RENDER_LINE_THRESHOLD)
        and not view.split
        and view.mode in {"unified", "auto"}
    )


def _should_use_split_block_renderer(view) -> bool:
    return (
        (view._virt.active or len(view._all_lines) >= view.BLOCK_RENDER_LINE_THRESHOLD)
        and view.split
        and view.mode in {"split", "auto"}
    )


def _can_render_in_unified_block(view, line: DiffLine) -> bool:
    if line.line_index in view._comment_threads_by_line:
        return False
    return True


def _can_render_in_split_block(view, line: DiffLine) -> bool:
    if line.line_index in view._comment_threads_by_line:
        return False
    return True


def _compute_unified_block_static_rows(
    view,
    line: DiffLine,
) -> tuple[UnifiedBlockRowStaticData, ...]:
    if not line.is_modified:
        side: Literal["old", "new", "auto"] = "auto"
        if line.is_added:
            side = "new"
        elif line.is_deleted:
            side = "old"
        return (
            UnifiedBlockRowStaticData(
                annotation=view._build_unified_prefix_content(line),
                line_style=view._unified_line_style(line, side=side),
                side=side,
            ),
        )

    return (
        UnifiedBlockRowStaticData(
            annotation=_build_unified_modified_block_prefix_content(
                view, line, side="old"
            ),
            line_style=view._unified_line_style(line, side="old"),
            side="old",
        ),
        UnifiedBlockRowStaticData(
            annotation=_build_unified_modified_block_prefix_content(
                view, line, side="new"
            ),
            line_style=view._unified_line_style(line, side="new"),
            side="new",
        ),
    )


def _unified_block_static_rows(
    view,
    line: DiffLine,
) -> tuple[UnifiedBlockRowStaticData, ...]:
    cached = view._unified_block_static_rows_by_line.get(line.line_index)
    if cached is not None:
        return cached

    cached = _compute_unified_block_static_rows(view, line)
    view._unified_block_static_rows_by_line[line.line_index] = cached
    return cached


def _compute_split_block_static_row(
    view,
    line: DiffLine,
) -> SplitBlockLineStaticData:
    return SplitBlockLineStaticData(
        left_annotation=_build_split_block_prefix_content(view, line, side="old"),
        left_style=view._split_line_style(line, side="old"),
        right_annotation=_build_split_block_prefix_content(view, line, side="new"),
        right_style=view._split_line_style(line, side="new"),
    )


def _split_block_static_row(view, line: DiffLine) -> SplitBlockLineStaticData:
    cached = view._split_block_static_rows_by_line.get(line.line_index)
    if cached is not None:
        return cached

    cached = _compute_split_block_static_row(view, line)
    view._split_block_static_rows_by_line[line.line_index] = cached
    return cached


def _build_unified_modified_block_prefix_content(
    view,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> Content:
    prefix_parts: list[Content] = []

    if view.show_line_numbers:
        if side == "old":
            prefix_parts.append(
                Content.styled(f"{line.old_line_no:>4} ", "$text-disabled")
            )
            prefix_parts.append(Content.styled("     ", "$text-disabled"))
        else:
            prefix_parts.append(Content.styled("     ", "$text-disabled"))
            prefix_parts.append(
                Content.styled(f"{line.new_line_no:>4} ", "$text-disabled")
            )

    prefix_parts.append(Content("- " if side == "old" else "+ "))

    return Content("").join(prefix_parts)


def _build_unified_block_row_data(
    view,
    line: DiffLine,
) -> tuple[list[Content], list[Content | None], list[str]]:
    static_rows = _unified_block_static_rows(view, line)
    cursor_side = (
        view._cursor_side_for_line(line)
        if line.line_index == view.cursor_line
        else None
    )
    spec = view._compute_selection_spec_for_line(line.line_index)

    annotations: list[Content] = []
    code_lines: list[Content | None] = []
    line_styles: list[str] = []

    for row in static_rows:
        annotations.append(row.annotation)
        line_styles.append(row.line_style)

        has_cursor = cursor_side == row.side
        cursor_col = view.cursor_column if has_cursor else None
        text = view._get_line_text(line, row.side)

        if spec is not None and text:
            sel_start, sel_end, _ = spec
            actual_end = sel_end if sel_end is not None else max(0, len(text) - 1)
            code_lines.append(
                view._build_code_content_with_selection(
                    line,
                    has_cursor,
                    cursor_col,
                    sel_start,
                    actual_end,
                    side=row.side,
                )
            )
        else:
            code_lines.append(
                view._build_code_content_with_cursor(
                    line,
                    has_cursor,
                    cursor_col,
                    side=row.side,
                )
            )

    return annotations, code_lines, line_styles


def _build_split_block_prefix_content(
    view,
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

    return view._build_split_prefix(
        line_no,
        prefix,
        line_index=line.line_index,
    )


def _build_split_block_code_content(
    view,
    line: DiffLine,
    *,
    side: Literal["old", "new"],
) -> Content:
    text = line.old_content if side == "old" else line.new_content
    if not text:
        return Content(" ")

    spec = view._compute_selection_spec_for_line(line.line_index)
    has_cursor = line.line_index == view.cursor_line and view.active_pane == side

    if spec is not None:
        sel_start, sel_end, _ = spec
        actual_end = sel_end if sel_end is not None else max(0, len(text) - 1)
        return view._build_code_content_with_selection(
            line,
            has_cursor,
            view.cursor_column if has_cursor else None,
            sel_start,
            actual_end,
            side=side,
        )

    return view._build_code_content_with_cursor(
        line,
        has_cursor,
        view.cursor_column if has_cursor else None,
        side=side,
    )


def _build_split_block_row_data(
    view,
    line: DiffLine,
) -> tuple[Content, Content, str, Content, Content, str]:
    static_row = _split_block_static_row(view, line)
    return (
        static_row.left_annotation,
        _build_split_block_code_content(view, line, side="old"),
        static_row.left_style,
        static_row.right_annotation,
        _build_split_block_code_content(view, line, side="new"),
        static_row.right_style,
    )


def _register_unified_block(
    view,
    block: UnifiedDiffBlock,
    lines: list[DiffLine],
) -> None:
    for line in lines:
        view._unified_blocks_by_line[line.line_index] = block
        view._register_line_widget(line.line_index, block)


def _refresh_unified_blocks_for_lines(view, line_indices: set[int]) -> bool:
    blocks: list[UnifiedDiffBlock] = []
    seen: set[int] = set()

    for line_idx in line_indices:
        block = view._unified_blocks_by_line.get(line_idx)
        if block is None:
            continue
        block_id = id(block)
        if block_id in seen:
            continue
        seen.add(block_id)
        blocks.append(block)

    if not blocks:
        return False

    for block in blocks:
        block_lines = [view._all_lines[idx] for idx in block.line_indices]
        annotations: list[Content] = []
        code_lines: list[Content | None] = []
        line_styles: list[str] = []
        for line in block_lines:
            row_annotations, row_code_lines, row_styles = _build_unified_block_row_data(
                view, line
            )
            annotations.extend(row_annotations)
            code_lines.extend(row_code_lines)
            line_styles.extend(row_styles)

        block.update_block(
            annotations=annotations,
            code_lines=code_lines,
            line_styles=line_styles,
        )

    return True


def _register_split_block(
    view,
    block: SplitDiffBlock,
    lines: list[DiffLine],
) -> None:
    for line in lines:
        view._split_blocks_by_line[line.line_index] = block
        view._register_line_widget(line.line_index, block)


def _refresh_split_blocks_for_lines(view, line_indices: set[int]) -> bool:
    blocks: list[SplitDiffBlock] = []
    seen: set[int] = set()

    for line_idx in line_indices:
        block = view._split_blocks_by_line.get(line_idx)
        if block is None:
            continue
        block_id = id(block)
        if block_id in seen:
            continue
        seen.add(block_id)
        blocks.append(block)

    if not blocks:
        return False

    for block in blocks:
        block_lines = [view._all_lines[idx] for idx in block.line_indices]
        left_annotations: list[Content] = []
        left_code_lines: list[Content | None] = []
        left_styles: list[str] = []
        right_annotations: list[Content] = []
        right_code_lines: list[Content | None] = []
        right_styles: list[str] = []

        for line in block_lines:
            (
                left_annotation,
                left_code,
                left_style,
                right_annotation,
                right_code,
                right_style,
            ) = _build_split_block_row_data(view, line)
            left_annotations.append(left_annotation)
            left_code_lines.append(left_code)
            left_styles.append(left_style)
            right_annotations.append(right_annotation)
            right_code_lines.append(right_code)
            right_styles.append(right_style)

        block.update_block(
            left_annotations=left_annotations,
            left_code_lines=left_code_lines,
            left_styles=left_styles,
            right_annotations=right_annotations,
            right_code_lines=right_code_lines,
            right_styles=right_styles,
        )

    return True


def _refresh_grouped_blocks_for_lines(view, line_indices: set[int]) -> bool:
    refreshed = _refresh_unified_blocks_for_lines(view, line_indices)
    refreshed = _refresh_split_blocks_for_lines(view, line_indices) or refreshed
    return refreshed


def _block_chunk_limit(view) -> int | None:
    if view._showing_full_file:
        return view.UNIFIED_BLOCK_CHUNK_SIZE
    if (
        not view._virt.active
        and len(view._all_lines) >= view.BLOCK_RENDER_LINE_THRESHOLD
    ):
        return None
    return view.UNIFIED_BLOCK_CHUNK_SIZE


def _render_split_line_block(
    view,
    container: VerticalScroll,
    lines: list[DiffLine],
    *,
    before: Widget | None = None,
) -> None:
    block = SplitDiffBlock(
        [line.line_index for line in lines],
        classes="diff-block split-block split-container",
    )
    left_annotations: list[Content] = []
    left_code_lines: list[Content | None] = []
    left_styles: list[str] = []
    right_annotations: list[Content] = []
    right_code_lines: list[Content | None] = []
    right_styles: list[str] = []

    for line in lines:
        (
            left_annotation,
            left_code,
            left_style,
            right_annotation,
            right_code,
            right_style,
        ) = _build_split_block_row_data(view, line)
        left_annotations.append(left_annotation)
        left_code_lines.append(left_code)
        left_styles.append(left_style)
        right_annotations.append(right_annotation)
        right_code_lines.append(right_code)
        right_styles.append(right_style)

    block.update_block(
        left_annotations=left_annotations,
        left_code_lines=left_code_lines,
        left_styles=left_styles,
        right_annotations=right_annotations,
        right_code_lines=right_code_lines,
        right_styles=right_styles,
    )
    if before is not None:
        container.mount(block, before=before)
    else:
        container.mount(block)
    _register_split_block(view, block, lines)


def _render_unified_line_block(
    view,
    container: VerticalScroll,
    lines: list[DiffLine],
    *,
    before: Widget | None = None,
) -> None:
    annotations: list[Content] = []
    code_lines: list[Content | None] = []
    line_styles: list[str] = []
    for line in lines:
        row_annotations, row_code_lines, row_styles = _build_unified_block_row_data(
            view, line
        )
        annotations.extend(row_annotations)
        code_lines.extend(row_code_lines)
        line_styles.extend(row_styles)

    block = UnifiedDiffBlock(
        [line.line_index for line in lines],
        classes="diff-block",
    )
    if view._showing_full_file:
        block._annotations.styles.width = view.PREVIEW_PREFIX_WIDTH
    block.update_block(
        annotations=annotations,
        code_lines=code_lines,
        line_styles=line_styles,
    )
    if before is not None:
        container.mount(block, before=before)
    else:
        container.mount(block)
    _register_unified_block(view, block, lines)


def _refresh_non_block_line_content(view, line_idx: int) -> None:
    code_widgets = view._get_code_widgets(line_idx)
    if not code_widgets:
        return

    line = view._all_lines[line_idx]
    selection_spec = view._compute_selection_spec_for_line(line_idx)
    has_cursor_line = line_idx == view.cursor_line

    for code_widget in code_widgets:
        if code_widget.has_class("-placeholder"):
            if code_widget.has_class("-cursor"):
                code_widget.remove_class("-cursor")
            continue

        side = view._get_line_side_for_widget(line, code_widget)
        show_cursor = has_cursor_line and view._widget_matches_cursor_side(
            line, code_widget
        )

        if selection_spec is not None:
            sel_start, sel_end, _ = selection_spec
            text = view._get_line_text(line, side)
            actual_end = sel_end if sel_end is not None else max(0, len(text) - 1)
            new_content = view._build_code_content_with_selection(
                line,
                show_cursor,
                view.cursor_column if show_cursor else None,
                sel_start,
                actual_end,
                side=side,
            )
        elif show_cursor:
            new_content = view._build_code_content_with_cursor(
                line,
                True,
                view.cursor_column,
                side=side,
            )
        else:
            new_content = view._base_code_content(
                line,
                side=side,
                empty_fallback=" ",
            )
        code_widget.update(new_content)

        if show_cursor:
            code_widget.add_class("-cursor")
        else:
            code_widget.remove_class("-cursor")
