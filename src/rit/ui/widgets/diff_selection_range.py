"""Visual selection range policy."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Literal

SelectionKind = Literal["char", "line"]
SelectionSpec = tuple[int, int | None, SelectionKind]

__all__ = (
    "SelectionKind",
    "SelectionSpec",
    "VisualSelectionBounds",
    "VisualSelectionDelta",
    "visible_selection_line_range",
    "visual_selection_bounds",
    "visual_selection_delta",
    "visual_selection_spec_for_line",
    "visual_selection_specs_for_visible_lines",
    "visual_selection_specs_with_dirty_lines",
)


@dataclass(frozen=True)
class VisualSelectionBounds:
    """Line and column bounds for a visual selection."""

    start_line: int
    end_line: int
    first_line_col: int
    last_line_col: int


@dataclass(frozen=True)
class VisualSelectionDelta:
    """Selection highlight lines that need UI updates."""

    lines_to_clear: frozenset[int]
    lines_to_apply: frozenset[int]


def visual_selection_bounds(
    *,
    visual_anchor_line: int,
    visual_anchor_column: int | None,
    cursor_line: int,
    cursor_column: int,
) -> VisualSelectionBounds:
    """Return normalized visual selection line and column bounds."""
    start_line = min(visual_anchor_line, cursor_line)
    end_line = max(visual_anchor_line, cursor_line)
    start_col = visual_anchor_column if visual_anchor_column is not None else 0
    end_col = cursor_column

    if visual_anchor_line < cursor_line:
        first_line_col = start_col
        last_line_col = end_col
    elif visual_anchor_line > cursor_line:
        first_line_col = end_col
        last_line_col = start_col
    else:
        first_line_col = min(start_col, end_col)
        last_line_col = max(start_col, end_col)

    return VisualSelectionBounds(
        start_line=start_line,
        end_line=end_line,
        first_line_col=first_line_col,
        last_line_col=last_line_col,
    )


def visible_selection_line_range(
    *,
    visual_mode: bool,
    visual_anchor_line: int | None,
    cursor_line: int,
    has_lines: bool,
    rendered_start: int,
    rendered_end: int,
) -> tuple[int, int] | None:
    """Return the visible line range for the current visual selection."""
    if not visual_mode or visual_anchor_line is None:
        return None
    if not has_lines:
        return None

    start_line = min(visual_anchor_line, cursor_line)
    end_line = max(visual_anchor_line, cursor_line)
    visible_start = max(start_line, rendered_start)
    visible_end = min(end_line, rendered_end)

    if visible_start > visible_end:
        return None
    return visible_start, visible_end


def visual_selection_delta(
    old_specs: Mapping[int, SelectionSpec],
    new_specs: Mapping[int, SelectionSpec],
    *,
    dirty_lines: Collection[int] | None = None,
) -> VisualSelectionDelta:
    """Return highlight clear/apply work for a visual selection update."""
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

    return VisualSelectionDelta(
        lines_to_clear=frozenset(lines_to_clear),
        lines_to_apply=frozenset(lines_to_apply),
    )


def visual_selection_specs_for_visible_lines(
    *,
    visual_mode: bool,
    visual_anchor_line: int | None,
    visual_anchor_column: int | None,
    cursor_line: int,
    cursor_column: int,
    visual_type: SelectionKind,
    has_lines: bool,
    rendered_start: int,
    rendered_end: int,
    line_is_rendered: Callable[[int], bool],
) -> dict[int, SelectionSpec]:
    """Return selection specs for visible rendered lines."""
    visible_range = visible_selection_line_range(
        visual_mode=visual_mode,
        visual_anchor_line=visual_anchor_line,
        cursor_line=cursor_line,
        has_lines=has_lines,
        rendered_start=rendered_start,
        rendered_end=rendered_end,
    )
    if visible_range is None:
        return {}

    visible_start, visible_end = visible_range
    specs: dict[int, SelectionSpec] = {}
    for line_idx in range(visible_start, visible_end + 1):
        spec = visual_selection_spec_for_line(
            line_idx,
            visual_mode=visual_mode,
            visual_anchor_line=visual_anchor_line,
            visual_anchor_column=visual_anchor_column,
            cursor_line=cursor_line,
            cursor_column=cursor_column,
            visual_type=visual_type,
            has_lines=has_lines,
            line_is_rendered=line_is_rendered(line_idx),
        )
        if spec is not None:
            specs[line_idx] = spec
    return specs


def visual_selection_specs_with_dirty_lines(
    old_specs: Mapping[int, SelectionSpec],
    dirty_specs: Mapping[int, SelectionSpec | None],
) -> dict[int, SelectionSpec]:
    """Return selection specs after recomputing dirty lines."""
    updated_specs = dict(old_specs)
    for line_idx, spec in dirty_specs.items():
        if spec is None:
            updated_specs.pop(line_idx, None)
        else:
            updated_specs[line_idx] = spec
    return updated_specs


def visual_selection_spec_for_line(
    line_index: int,
    *,
    visual_mode: bool,
    visual_anchor_line: int | None,
    visual_anchor_column: int | None,
    cursor_line: int,
    cursor_column: int,
    visual_type: SelectionKind,
    has_lines: bool,
    line_is_rendered: bool,
) -> SelectionSpec | None:
    """Return the visual selection spec for one rendered line."""
    if not visual_mode or visual_anchor_line is None:
        return None
    if not has_lines or not line_is_rendered:
        return None

    bounds = visual_selection_bounds(
        visual_anchor_line=visual_anchor_line,
        visual_anchor_column=visual_anchor_column,
        cursor_line=cursor_line,
        cursor_column=cursor_column,
    )
    if not (bounds.start_line <= line_index <= bounds.end_line):
        return None

    if visual_type == "line":
        return (0, None, "line")

    if bounds.start_line == bounds.end_line:
        return (bounds.first_line_col, bounds.last_line_col, "char")
    if line_index == bounds.start_line:
        return (bounds.first_line_col, None, "char")
    if line_index == bounds.end_line:
        return (0, bounds.last_line_col, "char")
    return (0, None, "char")
