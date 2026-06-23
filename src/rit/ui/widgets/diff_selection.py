"""Visual mode, selection highlighting, and yank/copy for DiffView."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual.content import Content

from rit.ui.messages import Flash
from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_search as _search
from rit.ui.widgets import diff_selection_content as _selection_content
from rit.ui.widgets import diff_selection_range as _selection_range
from rit.ui.widgets import diff_selection_text as _selection_text
from rit.ui.widgets import diff_visual_mode as _visual_mode

if TYPE_CHECKING:
    from rit.core.types import DiffLine
    from rit.ui.widgets.diff_view import DiffView


__all__ = ()


def _apply_visual_mode_state(
    view: DiffView,
    state: _visual_mode.VisualModeState,
) -> None:
    view.visual_type = state.visual_type
    view.visual_mode = state.visual_mode
    view.visual_anchor_line = state.visual_anchor_line
    view.visual_anchor_column = state.visual_anchor_column


def _enter_visual_mode(view: DiffView, visual_type: Literal["char", "line"]) -> None:
    state = _visual_mode.enter_visual_mode(
        visual_type=visual_type,
        current_visual_mode=view.visual_mode,
        current_visual_anchor_line=view.visual_anchor_line,
        current_visual_anchor_column=view.visual_anchor_column,
        cursor_line=view.cursor_line,
        cursor_column=view.cursor_column,
    )
    _apply_visual_mode_state(view, state)


def _exit_visual_mode(view: DiffView) -> None:
    state = _visual_mode.exit_visual_mode(current_visual_type=view.visual_type)
    _apply_visual_mode_state(view, state)


def _toggle_visual(view: DiffView) -> None:
    state = _visual_mode.toggle_visual_mode(
        requested_visual_type="char",
        current_visual_mode=view.visual_mode,
        current_visual_type=view.visual_type,
        current_visual_anchor_line=view.visual_anchor_line,
        current_visual_anchor_column=view.visual_anchor_column,
        cursor_line=view.cursor_line,
        cursor_column=view.cursor_column,
    )
    _apply_visual_mode_state(view, state)


def _toggle_visual_line(view: DiffView) -> None:
    state = _visual_mode.toggle_visual_mode(
        requested_visual_type="line",
        current_visual_mode=view.visual_mode,
        current_visual_type=view.visual_type,
        current_visual_anchor_line=view.visual_anchor_line,
        current_visual_anchor_column=view.visual_anchor_column,
        cursor_line=view.cursor_line,
        cursor_column=view.cursor_column,
    )
    _apply_visual_mode_state(view, state)


def _copy_yank_to_clipboard(view: DiffView, yank: _selection_text.VisualYank) -> None:
    try:
        view._copy_to_clipboard(yank.text)
    except Exception as e:
        view.post_message(
            Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
        )
        return

    view.post_message(Flash(yank.success_message, style="success", duration=2.0))


def _yank(view: DiffView) -> None:
    if not view._all_lines:
        return

    if not view.visual_mode:
        if not (0 <= view.cursor_line < len(view._all_lines)):
            return

        yank = _selection_text.normal_yank_for_line(view._get_cursor_text())
        _copy_yank_to_clipboard(view, yank)
        return

    if view.visual_anchor_line is None:
        return

    line_texts = [view._get_line_text(line) for line in view._all_lines]
    yank = _selection_text.visual_yank_for_range(
        line_texts,
        visual_anchor_line=view.visual_anchor_line,
        visual_anchor_column=view.visual_anchor_column,
        cursor_line=view.cursor_line,
        cursor_column=view.cursor_column,
        visual_type=view.visual_type,
    )

    _copy_yank_to_clipboard(view, yank)
    _exit_visual_mode(view)


def _exit_visual(view: DiffView) -> None:
    if view.visual_mode:
        _exit_visual_mode(view)
    elif view._search_query:
        _search.clear_state(view)
        _search._refresh_search_display(view)


# ---------------------------------------------------------------------------
# Selection spec computation
# ---------------------------------------------------------------------------


def _compute_selection_spec_for_line(
    view: DiffView,
    line_idx: int,
) -> tuple[int, int | None, Literal["char", "line"]] | None:
    return _selection_range.visual_selection_spec_for_line(
        line_idx,
        visual_mode=view.visual_mode,
        visual_anchor_line=view.visual_anchor_line,
        visual_anchor_column=view.visual_anchor_column,
        cursor_line=view.cursor_line,
        cursor_column=view.cursor_column,
        visual_type=view.visual_type,
        has_lines=bool(view._all_lines),
        line_is_rendered=view._is_line_rendered(line_idx),
    )


def _compute_visible_selection_specs(
    view: DiffView,
) -> dict[int, tuple[int, int | None, Literal["char", "line"]]]:
    rendered_start, rendered_end = view._get_rendered_line_bounds()
    return _selection_range.visual_selection_specs_for_visible_lines(
        visual_mode=view.visual_mode,
        visual_anchor_line=view.visual_anchor_line,
        visual_anchor_column=view.visual_anchor_column,
        cursor_line=view.cursor_line,
        cursor_column=view.cursor_column,
        visual_type=view.visual_type,
        has_lines=bool(view._all_lines),
        rendered_start=rendered_start,
        rendered_end=rendered_end,
        line_is_rendered=view._is_line_rendered,
    )


# ---------------------------------------------------------------------------
# Selection highlighting
# ---------------------------------------------------------------------------


def _update_selection_highlighting(
    view: DiffView, dirty_lines: set[int] | None = None
) -> None:
    if not view.visual_mode or view.visual_anchor_line is None:
        return

    if not view._all_lines or not view.is_mounted:
        return

    old_specs = view._visual_selection_specs

    incremental = bool(dirty_lines) and bool(old_specs)
    if incremental:
        dirty_specs = {
            line_idx: _compute_selection_spec_for_line(view, line_idx)
            for line_idx in set(dirty_lines or ())
        }
        new_specs = _selection_range.visual_selection_specs_with_dirty_lines(
            old_specs,
            dirty_specs,
        )
    else:
        new_specs = _compute_visible_selection_specs(view)

    delta = _selection_range.visual_selection_delta(
        old_specs,
        new_specs,
        dirty_lines=dirty_lines,
    )

    for line_idx in sorted(delta.lines_to_clear):
        _clear_line_selection(view, line_idx)

    for line_idx in sorted(delta.lines_to_apply):
        sel_start, sel_end, _ = new_specs[line_idx]
        _apply_line_selection(view, line_idx, sel_start, sel_end)

    view._visual_selection_specs = new_specs


def _clear_line_selection(view: DiffView, line_idx: int) -> None:
    if line_idx < 0 or line_idx >= len(view._all_lines):
        return

    if _blocks._refresh_grouped_blocks_for_lines(view, {line_idx}):
        return

    code_widgets = view._get_code_widgets(line_idx)
    if not code_widgets:
        return

    for widget in code_widgets:
        if widget.has_class("-placeholder"):
            continue

        widget.remove_class("-selected")
        widget.remove_class("-anchor")

        line = view._all_lines[line_idx]
        side = view._get_line_side_for_widget(line, widget)
        has_cursor = view._diff_line_cursor_active(
            line_idx
        ) and view._widget_matches_cursor_side(line, widget)
        if has_cursor:
            new_content = view._build_code_content_with_cursor(
                line,
                True,
                view.cursor_column,
                side=side,
            )
            widget.add_class("-cursor")
        else:
            new_content = view._base_code_content(line, side=side)
            widget.remove_class("-cursor")
        widget.update(new_content)


def _apply_line_selection(
    view: DiffView, line_idx: int, start_col: int, end_col: int | None
) -> None:
    if line_idx < 0 or line_idx >= len(view._all_lines):
        return

    if _blocks._refresh_grouped_blocks_for_lines(view, {line_idx}):
        return

    line = view._all_lines[line_idx]
    text = view._get_line_text(line)

    code_widgets = view._get_code_widgets(line_idx)
    if not code_widgets:
        return

    for widget in code_widgets:
        if widget.has_class("-placeholder"):
            continue

        actual_end = end_col if end_col is not None else len(text) - 1
        side = view._get_line_side_for_widget(line, widget)
        has_cursor = view._diff_line_cursor_active(
            line_idx
        ) and view._widget_matches_cursor_side(line, widget)
        cursor_col = view.cursor_column if has_cursor else None
        content = _build_code_content_with_selection(
            view,
            line,
            has_cursor,
            cursor_col,
            start_col,
            actual_end,
            side=side,
        )
        widget.update(content)
        if has_cursor:
            widget.add_class("-cursor")
        else:
            widget.remove_class("-cursor")

        line_role = _visual_mode.visual_line_selection_role(
            line_index=line_idx,
            visual_type=view.visual_type,
            visual_anchor_line=view.visual_anchor_line,
        )
        if line_role != "none":
            widget.add_class("-selected")
            if line_role == "anchor":
                widget.add_class("-anchor")
            else:
                widget.remove_class("-anchor")
        else:
            widget.remove_class("-selected")
            widget.remove_class("-anchor")


def _build_code_content_with_selection(
    view: DiffView,
    line: DiffLine,
    has_cursor: bool,
    cursor_col: int | None,
    sel_start: int,
    sel_end: int,
    *,
    side: Literal["old", "new", "auto"] = "auto",
) -> Content:
    base_content = view._base_code_content(line, side=side)
    base_content = _search.apply_search_highlights(
        view,
        base_content,
        line.line_index,
        side,
    )
    text_content = view._get_line_text(line, side)
    return _selection_content.apply_selection_to_code_content(
        base_content,
        line_text=text_content,
        selection_start=sel_start,
        selection_end=sel_end,
        has_cursor=has_cursor,
        cursor_col=cursor_col,
    )
