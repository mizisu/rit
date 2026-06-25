"""Visual selection text extraction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rit.ui.widgets.diff_selection_range import SelectionKind, visual_selection_bounds

__all__ = (
    "VisualYank",
    "normal_yank_for_line",
    "selected_text_for_visual_range",
    "visual_yank_for_range",
)


@dataclass(frozen=True)
class VisualYank:
    """Text and success message for a yank."""

    text: str
    success_message: str


def normal_yank_for_line(line_text: str) -> VisualYank:
    """Return copied text and message for a normal-mode line yank."""
    return VisualYank(text=f"{line_text}\n", success_message="Copied 1 line")


def selected_text_for_visual_range(
    line_texts: Sequence[str],
    *,
    visual_anchor_line: int,
    visual_anchor_column: int | None,
    cursor_line: int,
    cursor_column: int,
    visual_type: SelectionKind,
) -> str:
    """Return the text selected by a visual range."""
    bounds = visual_selection_bounds(
        visual_anchor_line=visual_anchor_line,
        visual_anchor_column=visual_anchor_column,
        cursor_line=cursor_line,
        cursor_column=cursor_column,
    )

    if visual_type == "line":
        text_to_copy = "\n".join(
            line_texts[line_idx]
            for line_idx in range(bounds.start_line, bounds.end_line + 1)
        )
        return f"{text_to_copy}\n" if text_to_copy else ""

    selected_lines: list[str] = []

    if bounds.start_line == bounds.end_line:
        text = line_texts[bounds.start_line]
        selected_lines.append(text[bounds.first_line_col : bounds.last_line_col + 1])
    else:
        for line_idx in range(bounds.start_line, bounds.end_line + 1):
            text = line_texts[line_idx]

            if line_idx == bounds.start_line:
                selected_lines.append(text[bounds.first_line_col:])
            elif line_idx == bounds.end_line:
                selected_lines.append(text[: bounds.last_line_col + 1])
            else:
                selected_lines.append(text)

    return "\n".join(selected_lines)


def visual_yank_for_range(
    line_texts: Sequence[str],
    *,
    visual_anchor_line: int,
    visual_anchor_column: int | None,
    cursor_line: int,
    cursor_column: int,
    visual_type: SelectionKind,
) -> VisualYank:
    """Return copied text and message for a visual yank."""
    text = selected_text_for_visual_range(
        line_texts,
        visual_anchor_line=visual_anchor_line,
        visual_anchor_column=visual_anchor_column,
        cursor_line=cursor_line,
        cursor_column=cursor_column,
        visual_type=visual_type,
    )

    if visual_type == "line":
        bounds = visual_selection_bounds(
            visual_anchor_line=visual_anchor_line,
            visual_anchor_column=visual_anchor_column,
            cursor_line=cursor_line,
            cursor_column=cursor_column,
        )
        line_count = bounds.end_line - bounds.start_line + 1
        suffix = "" if line_count == 1 else "s"
        return VisualYank(text=text, success_message=f"Copied {line_count} line{suffix}")

    char_count = len(text)
    suffix = "" if char_count == 1 else "s"
    return VisualYank(
        text=text,
        success_message=f"Copied {char_count} character{suffix}",
    )
