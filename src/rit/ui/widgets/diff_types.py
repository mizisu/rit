"""Data types and block widgets for DiffView rendering."""

from dataclasses import dataclass
from typing import Literal

from textual.containers import Horizontal
from textual.content import Content

from rit.ui.widgets.diff_visual import DiffCode, LineAnnotations, LineContent


@dataclass(frozen=True)
class RenderedRow:

    mode: Literal["unified", "split"]
    row_index: int
    line_index: int
    hunk_index: int
    kind: Literal[
        "context",
        "added",
        "deleted",
        "modified-old",
        "modified-new",
    ]
    side: Literal["old", "new", "auto"]
    anchor_id: str
    old_line_no: int | None
    new_line_no: int | None


@dataclass(frozen=True)
class DiffSearchMatch:

    row_index: int
    line_index: int
    side: Literal["old", "new", "auto"]
    column: int


@dataclass(frozen=True)
class DiffLayout:

    auto_split_min_width: int = 120
    unified_prefix_width: int = 18
    split_prefix_width: int = 8

    horizontal_scroll_edge_padding: int = 5
    horizontal_scroll_reveal_padding: int = 10


DEFAULT_DIFF_LAYOUT = DiffLayout()


class UnifiedDiffBlock(Horizontal):

    def __init__(
        self,
        line_indices: list[int],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.line_indices = tuple(line_indices)
        self._annotations = LineAnnotations([], classes="line-prefix")
        self._code = DiffCode(classes="code-content")
        super().__init__(
            self._annotations,
            self._code,
            id=id,
            classes=classes,
        )

    def update_block(
        self,
        *,
        annotations: list[Content],
        code_lines: list[Content | None],
        line_styles: list[str],
    ) -> None:
        self._annotations.numbers = annotations
        self._code.update(LineContent(code_lines, line_styles))


class SplitDiffBlock(Horizontal):

    def __init__(
        self,
        line_indices: list[int],
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.line_indices = tuple(line_indices)
        self._left_annotations = LineAnnotations([], classes="line-prefix")
        self._left_code = DiffCode(classes="code-content -old-side")
        self._right_annotations = LineAnnotations([], classes="line-prefix")
        self._right_code = DiffCode(classes="code-content -new-side")
        self._left_pane = Horizontal(
            self._left_annotations,
            self._left_code,
            classes="split-pane split-pane-left",
        )
        self._left_pane.styles.height = "auto"
        self._right_pane = Horizontal(
            self._right_annotations,
            self._right_code,
            classes="split-pane split-pane-right",
        )
        self._right_pane.styles.height = "auto"
        super().__init__(
            self._left_pane,
            self._right_pane,
            id=id,
            classes=classes,
        )

    def update_block(
        self,
        *,
        left_annotations: list[Content],
        left_code_lines: list[Content | None],
        left_styles: list[str],
        right_annotations: list[Content],
        right_code_lines: list[Content | None],
        right_styles: list[str],
    ) -> None:
        self._left_annotations.numbers = left_annotations
        self._left_code.update(LineContent(left_code_lines, left_styles))
        self._right_annotations.numbers = right_annotations
        self._right_code.update(LineContent(right_code_lines, right_styles))


@dataclass(frozen=True)
class UnifiedBlockRowStaticData:

    annotation: Content
    line_style: str
    side: Literal["old", "new", "auto"]


@dataclass(frozen=True)
class SplitBlockLineStaticData:

    left_annotation: Content
    left_style: str
    right_annotation: Content
    right_style: str
