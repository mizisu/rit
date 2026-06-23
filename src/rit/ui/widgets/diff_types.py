"""Data types and block widgets for DiffView rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from textual.widgets import Static

    from rit.core.types import FileDiff

from textual.containers import Horizontal
from textual.content import Content

from rit.ui.widgets.diff_visual import (
    DiffCode,
    LineAnnotations,
    LineContent,
    SyncedCodeScroll,
)

__all__ = (
    "CursorUIState",
    "DEFAULT_DIFF_LAYOUT",
    "DiffLayout",
    "DiffSearchMatch",
    "HighlightState",
    "RenderedRow",
    "SplitBlockLineStaticData",
    "SplitDiffBlock",
    "UnifiedBlockRowStaticData",
    "UnifiedDiffBlock",
    "VirtualState",
)


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

    vertical_scrolloff: int = 2
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
        width: int | None = None,
    ) -> None:
        self._annotations.numbers = annotations
        self._code.update(LineContent(code_lines, line_styles, width=width))


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
        self._left_scroll = SyncedCodeScroll(
            self._left_code,
            classes="split-code-scroll -old-side",
        )
        self._right_annotations = LineAnnotations([], classes="line-prefix")
        self._right_code = DiffCode(classes="code-content -new-side")
        self._right_scroll = SyncedCodeScroll(
            self._right_code,
            classes="split-code-scroll -new-side",
        )
        self._left_pane = Horizontal(
            self._left_annotations,
            self._left_scroll,
            classes="split-pane split-pane-left",
        )
        self._left_pane.styles.height = "auto"
        self._right_pane = Horizontal(
            self._right_annotations,
            self._right_scroll,
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
        left_annotation_styles: list[str],
        left_code_lines: list[Content | None],
        left_styles: list[str],
        right_annotations: list[Content],
        right_annotation_styles: list[str],
        right_code_lines: list[Content | None],
        right_styles: list[str],
        left_width: int | None = None,
        right_width: int | None = None,
    ) -> None:
        self._left_annotations.numbers = left_annotations
        self._left_annotations.line_styles = left_annotation_styles
        self._left_code.update(
            LineContent(left_code_lines, left_styles, width=left_width)
        )
        self._right_annotations.numbers = right_annotations
        self._right_annotations.line_styles = right_annotation_styles
        self._right_code.update(
            LineContent(right_code_lines, right_styles, width=right_width)
        )


@dataclass(frozen=True)
class UnifiedBlockRowStaticData:
    annotation: Content
    line_style: str
    side: Literal["old", "new", "auto"]


@dataclass(frozen=True)
class SplitBlockLineStaticData:
    left_annotation: Content
    left_annotation_style: str
    left_style: str
    right_annotation: Content
    right_annotation_style: str
    right_style: str


# ---------------------------------------------------------------------------
# Grouped state containers for DiffView
# ---------------------------------------------------------------------------


@dataclass
class HighlightState:
    """Syntax highlight cache and async worker coordination."""

    cache: set[tuple[int, bool, bool]] = field(default_factory=set)
    request_token: int = 0
    window_inflight: tuple[int, int, bool] | None = None
    queued_window: tuple[str, FileDiff, int, int, bool, int] | None = None
    window_worker_active: bool = False
    queued_full: tuple[str, FileDiff, bool, int] | None = None
    full_worker_active: bool = False


@dataclass
class VirtualState:
    """Virtual scrolling window state."""

    active: bool = False
    window_start: int = 0
    window_end: int = -1
    rendered_start: int = 0
    rendered_end: int = -1
    render_pending: bool = False
    cursor_shift_pending: bool = False
    coalesced_center: int | None = None
    suppress_next_viewport_shift: bool = False
    top_buffer: Static | None = None
    bottom_buffer: Static | None = None


@dataclass
class CursorUIState:
    """Batched cursor update and suspension flags."""

    flush_pending: bool = False
    dirty_lines: set[int] = field(default_factory=set)
    selection_dirty: set[int] = field(default_factory=set)
    selection_full_refresh: bool = False
    sync_search: bool = False
    update_status: bool = False
    suppress_scroll: bool = False
    pending_count: str = ""
    desired_column: int | None = None
    suspend_line_watch: bool = False
    suspend_column_watch: bool = False
    suspend_pane_watch: bool = False
