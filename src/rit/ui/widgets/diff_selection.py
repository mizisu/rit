"""Visual mode, selection highlighting, and yank/copy for DiffView."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from textual.content import Content

from rit.ui.messages import Flash
from rit.ui.widgets import diff_blocks as _blocks
from rit.ui.widgets import diff_search as _search

if TYPE_CHECKING:
    from rit.core.types import DiffLine
    from rit.ui.widgets.diff_view import DiffView


def _enter_visual_mode(view: DiffView, visual_type: Literal["char", "line"]) -> None:
    view.visual_type = visual_type

    if not view.visual_mode:
        view.visual_mode = True
        view.visual_anchor_line = view.cursor_line
        view.visual_anchor_column = view.cursor_column
        return

    if view.visual_anchor_line is None:
        view.visual_anchor_line = view.cursor_line
    if view.visual_anchor_column is None:
        view.visual_anchor_column = view.cursor_column


def _exit_visual_mode(view: DiffView) -> None:
    view.visual_mode = False
    view.visual_anchor_line = None
    view.visual_anchor_column = None


def _toggle_visual(view: DiffView) -> None:
    if view.visual_mode and view.visual_type == "char":
        _exit_visual_mode(view)
        return
    _enter_visual_mode(view, "char")


def _toggle_visual_line(view: DiffView) -> None:
    if view.visual_mode and view.visual_type == "line":
        _exit_visual_mode(view)
        return
    _enter_visual_mode(view, "line")


def _yank(view: DiffView) -> None:
    if not view._all_lines:
        return

    if not view.visual_mode:
        if not (0 <= view.cursor_line < len(view._all_lines)):
            return

        text_to_copy = view._get_cursor_text() + "\n"
        try:
            view._copy_to_clipboard(text_to_copy)
            view.post_message(Flash("Copied 1 line", style="success", duration=2.0))
        except Exception as e:
            view.post_message(
                Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
            )
        return

    if view.visual_anchor_line is None:
        return

    start_line = min(view.visual_anchor_line, view.cursor_line)
    end_line = max(view.visual_anchor_line, view.cursor_line)

    if view.visual_type == "line":
        selected_lines = [
            view._get_line_text(view._all_lines[line_idx])
            for line_idx in range(start_line, end_line + 1)
        ]
        text_to_copy = "\n".join(selected_lines)
        if text_to_copy:
            text_to_copy += "\n"

        try:
            view._copy_to_clipboard(text_to_copy)
            line_count = end_line - start_line + 1
            view.post_message(
                Flash(
                    f"Copied {line_count} line{'s' if line_count != 1 else ''}",
                    style="success",
                    duration=2.0,
                )
            )
        except Exception as e:
            view.post_message(
                Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
            )

        _exit_visual_mode(view)
        return

    start_col = (
        view.visual_anchor_column if view.visual_anchor_column is not None else 0
    )
    end_col = view.cursor_column

    if view.visual_anchor_line < view.cursor_line:
        first_line_col = start_col
        last_line_col = end_col
    elif view.visual_anchor_line > view.cursor_line:
        first_line_col = end_col
        last_line_col = start_col
    else:
        first_line_col = min(start_col, end_col)
        last_line_col = max(start_col, end_col)

    selected_lines: list[str] = []

    if start_line == end_line:
        line = view._all_lines[start_line]
        text = view._get_line_text(line)
        actual_start = min(start_col, end_col)
        actual_end = max(start_col, end_col)
        selected_lines.append(text[actual_start : actual_end + 1])
    else:
        for line_idx in range(start_line, end_line + 1):
            line = view._all_lines[line_idx]
            text = view._get_line_text(line)

            if line_idx == start_line:
                selected_lines.append(text[first_line_col:])
            elif line_idx == end_line:
                selected_lines.append(text[: last_line_col + 1])
            else:
                selected_lines.append(text)

    text_to_copy = "\n".join(selected_lines)

    try:
        view._copy_to_clipboard(text_to_copy)
        char_count = len(text_to_copy)
        view.post_message(
            Flash(
                f"Copied {char_count} character{'s' if char_count != 1 else ''}",
                style="success",
                duration=2.0,
            )
        )
    except Exception as e:
        view.post_message(
            Flash(f"Failed to copy: {str(e)}", style="error", duration=3.0)
        )

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
    if not view.visual_mode or view.visual_anchor_line is None:
        return None
    if not view._all_lines or not view._is_line_rendered(line_idx):
        return None

    start_line = min(view.visual_anchor_line, view.cursor_line)
    end_line = max(view.visual_anchor_line, view.cursor_line)
    if not (start_line <= line_idx <= end_line):
        return None

    if view.visual_type == "line":
        return (0, None, "line")

    start_col = (
        view.visual_anchor_column if view.visual_anchor_column is not None else 0
    )
    end_col = view.cursor_column

    if view.visual_anchor_line < view.cursor_line:
        first_line_col = start_col
        last_line_col = end_col
    elif view.visual_anchor_line > view.cursor_line:
        first_line_col = end_col
        last_line_col = start_col
    else:
        first_line_col = min(start_col, end_col)
        last_line_col = max(start_col, end_col)

    if start_line == end_line:
        return (first_line_col, last_line_col, "char")
    if line_idx == start_line:
        return (first_line_col, None, "char")
    if line_idx == end_line:
        return (0, last_line_col, "char")
    return (0, None, "char")


def _compute_visible_selection_specs(
    view: DiffView,
) -> dict[int, tuple[int, int | None, Literal["char", "line"]]]:
    if not view.visual_mode or view.visual_anchor_line is None:
        return {}

    if not view._all_lines:
        return {}

    start_line = min(view.visual_anchor_line, view.cursor_line)
    end_line = max(view.visual_anchor_line, view.cursor_line)

    rendered_start, rendered_end = view._get_rendered_line_bounds()
    visible_start = max(start_line, rendered_start)
    visible_end = min(end_line, rendered_end)

    if visible_start > visible_end:
        return {}

    specs: dict[int, tuple[int, int | None, Literal["char", "line"]]] = {}
    for line_idx in range(visible_start, visible_end + 1):
        spec = _compute_selection_spec_for_line(view, line_idx)
        if spec is not None:
            specs[line_idx] = spec

    return specs


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
        new_specs = dict(old_specs)
        candidate_lines = set(dirty_lines or ())
        for line_idx in candidate_lines:
            spec = _compute_selection_spec_for_line(view, line_idx)
            if spec is None:
                new_specs.pop(line_idx, None)
            else:
                new_specs[line_idx] = spec
    else:
        new_specs = _compute_visible_selection_specs(view)

    lines_to_clear = set(old_specs) - set(new_specs)
    lines_to_apply = {
        line_idx
        for line_idx, spec in new_specs.items()
        if old_specs.get(line_idx) != spec
    }

    if dirty_lines:
        for line_idx in dirty_lines:
            if line_idx in new_specs:
                lines_to_apply.add(line_idx)
            elif line_idx in old_specs:
                lines_to_clear.add(line_idx)

    for line_idx in sorted(lines_to_clear):
        _clear_line_selection(view, line_idx)

    for line_idx in sorted(lines_to_apply):
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

        if view.visual_type == "line":
            widget.add_class("-selected")
            if line_idx == view.visual_anchor_line:
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
    if not text_content:
        return base_content

    sel_start = max(0, min(sel_start, len(text_content) - 1))
    sel_end = max(0, min(sel_end, len(text_content) - 1))

    if sel_start > sel_end:
        sel_start, sel_end = sel_end, sel_start

    result = base_content.stylize("reverse dim", sel_start, sel_end + 1)

    if has_cursor and cursor_col is not None and cursor_col < len(text_content):
        result = result.stylize("reverse bold", cursor_col, cursor_col + 1)

    return result
