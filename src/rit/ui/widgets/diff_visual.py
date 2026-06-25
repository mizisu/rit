from __future__ import annotations

from collections.abc import Callable, Iterable

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual.content import Content
from textual.css.styles import RulesMap
from textual.geometry import Size
from textual.reactive import reactive
from textual.selection import Selection
from textual.strip import Strip
from textual.style import Style
from textual.visual import RenderOptions, Visual
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import HorizontalScroll

__all__ = (
    "DiffCode",
    "LineAnnotations",
    "LineContent",
    "MISSING_SIDE_BACKGROUND_STYLE",
    "MISSING_SIDE_HATCH",
    "MISSING_SIDE_HATCH_STEP",
    "MISSING_SIDE_HATCH_STYLE",
    "MISSING_SIDE_STYLE",
    "SyncedCodeScroll",
    "missing_side_hatch_text",
)


MISSING_SIDE_BACKGROUND_STYLE = "on $background"
MISSING_SIDE_HATCH_STYLE = "$text-disabled 9% on $background"
MISSING_SIDE_STYLE = MISSING_SIDE_BACKGROUND_STYLE
MISSING_SIDE_HATCH = "╱"
MISSING_SIDE_HATCH_STEP = 1


def missing_side_hatch_text(width: int, *, row_index: int = 0) -> str:
    """Build a quiet diagonal marker for split-diff sides with no code."""
    if width <= 0:
        return ""

    if MISSING_SIDE_HATCH_STEP == 1:
        return MISSING_SIDE_HATCH * width

    chars = [" "] * width
    start = row_index % MISSING_SIDE_HATCH_STEP
    for index in range(start, width, MISSING_SIDE_HATCH_STEP):
        chars[index] = MISSING_SIDE_HATCH
    return "".join(chars)


class LineContent(Visual):
    def __init__(
        self,
        code_lines: list[Content | None],
        line_styles: list[str],
        width: int | None = None,
    ) -> None:
        self.code_lines = code_lines
        self.line_styles = line_styles
        self._width = width
        if width is not None:
            self._content_width: int | None = width
        elif not code_lines:
            self._content_width = None
        elif len(code_lines) == 1:
            line = code_lines[0]
            self._content_width = line.cell_length if line is not None else None
        else:
            self._content_width = max(
                (line.cell_length for line in code_lines if line is not None),
                default=None,
            )

    def render_strips(
        self, width: int, height: int | None, style: Style, options: RenderOptions
    ) -> list[Strip]:
        strips: list[Strip] = []
        selection = options.selection
        selection_style = options.selection_style or Style.null()

        for y, (line, color) in enumerate(zip(self.code_lines, self.line_styles)):
            if line is None:
                meta = {"offset": (0, y)}
                missing_style = (
                    style.rich_style
                    + options.get_style(MISSING_SIDE_HATCH_STYLE).rich_style
                    + RichStyle.from_meta(meta)
                )
                text = missing_side_hatch_text(width, row_index=y)
                strips.append(Strip([Segment(text, missing_style)], width))
                continue
            else:
                # Apply selection if present
                if selection is not None:
                    if span := selection.get_span(y):
                        start, end = span
                        if end == -1:
                            end = len(line)
                        line = line.stylize(selection_style, start, end)

            # Pad line to width if needed
            if line.cell_length < width:
                line = line.pad_right(width - line.cell_length)

            # Apply line background style and base style
            line = line.stylize_before(color).stylize_before(style)

            # Convert to segments with offset metadata
            x = 0
            meta = {"offset": (x, y)}
            segments = []
            for text, rich_style, _ in line.render_segments():
                if rich_style is not None:
                    meta["offset"] = (x, y)
                    segments.append(
                        Segment(text, rich_style + RichStyle.from_meta(meta))
                    )
                else:
                    segments.append(Segment(text, rich_style))
                x += len(text)

            strips.append(Strip(segments, line.cell_length))

        return strips

    def get_optimal_width(self, rules: RulesMap, container_width: int) -> int:
        if self._content_width is None:
            raise ValueError("max() arg is an empty sequence")
        return self._content_width

    def get_minimal_width(self, rules: RulesMap) -> int:
        return 1

    def get_height(self, rules: RulesMap, width: int) -> int:
        return len(self.line_styles)


class LineAnnotations(Widget):
    DEFAULT_CSS = """
    LineAnnotations {
        width: auto;
        height: auto;
    }
    """

    numbers: reactive[list[Content]] = reactive[list[Content]](list)
    line_styles: reactive[list[str]] = reactive[list[str]](list)

    def __init__(
        self,
        numbers: Iterable[Content],
        *,
        line_styles: Iterable[str] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ):
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)
        self._number_width = 0
        self.numbers = list(numbers)
        self.line_styles = list(line_styles or [])

    def watch_numbers(self, numbers: list[Content]) -> None:
        count = len(numbers)
        if count == 0:
            self._number_width = 0
        elif count == 1:
            self._number_width = numbers[0].cell_length
        else:
            self._number_width = max(number.cell_length for number in numbers)

    @property
    def total_width(self) -> int:
        return self.number_width

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return self.total_width

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self.numbers)

    @property
    def number_width(self) -> int:
        return self._number_width

    def render_line(self, y: int) -> Strip:
        width = self.total_width
        visual_style = self.visual_style
        try:
            line_style = self.line_styles[y]
        except IndexError:
            line_style = ""
        if line_style:
            visual_style += self._get_style(line_style)
        rich_style = visual_style.rich_style

        try:
            number = self.numbers[y]
        except IndexError:
            number = Content.empty()

        strip = Strip(
            number.render_segments(visual_style), cell_length=number.cell_length
        )
        strip = strip.adjust_cell_length(width, rich_style)
        return strip


class SyncedCodeScroll(HorizontalScroll):
    DEFAULT_CSS = """
    SyncedCodeScroll {
        width: 1fr;
        height: auto;
        overflow: scroll hidden;
        scrollbar-size: 0 0;
    }
    """

    def __init__(
        self,
        *children,
        on_scroll_x: Callable[[float, "SyncedCodeScroll | None"], None] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self._on_scroll_x = on_scroll_x

    def set_on_scroll_x(
        self,
        callback: Callable[[float, "SyncedCodeScroll | None"], None] | None,
    ) -> None:
        self._on_scroll_x = callback

    def watch_scroll_x(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_x(old_value, new_value)
        if old_value == new_value or self._on_scroll_x is None:
            return
        self._on_scroll_x(new_value, self)


class DiffCode(Static):
    DEFAULT_CSS = """
    DiffCode {
        width: auto;
        height: auto;
        min-width: 1fr;
    }
    """

    ALLOW_SELECT = True

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        visual = self._render()
        if isinstance(visual, LineContent):
            text = "\n".join(
                "" if line is None else line.plain for line in visual.code_lines
            )
        else:
            return None
        return selection.extract(text), "\n"
