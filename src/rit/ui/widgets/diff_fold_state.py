"""Logical anchors and editor drafts retained across projection commits."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from textual.widgets import TextArea

from rit.core.types import DiffLine
from rit.ui.widgets import diff_cursor as _cursor

if TYPE_CHECKING:
    from rit.ui.widgets.comment_editor import InlineCommentEditor
    from rit.ui.widgets.diff_view import DiffView


@dataclass(frozen=True)
class LineAnchor:
    line: DiffLine | None
    filename: str | None
    header: bool = False
    side: Literal["old", "new", "auto"] = "auto"

    @classmethod
    def at(
        cls,
        view: DiffView,
        index: int | None,
        *,
        side: Literal["old", "new", "auto"] = "auto",
    ) -> LineAnchor:
        line = (
            view._all_lines[index]
            if index is not None and 0 <= index < len(view._all_lines)
            else None
        )
        return cls(line, line.file_path if line else None, side=side)

    def resolve(self, view: DiffView) -> int | None:
        if self.line is not None and not self.header:
            index = self.line.line_index
            if (
                0 <= index < len(view._all_lines)
                and view._all_lines[index] is self.line
            ):
                return index
            if self.filename:
                sides: tuple[Literal["LEFT", "RIGHT"], ...] = (
                    ("LEFT", "RIGHT") if self.side == "old" else ("RIGHT", "LEFT")
                )
                for side in sides:
                    number = (
                        self.line.old_line_no
                        if side == "LEFT"
                        else self.line.new_line_no
                    )
                    if number is not None:
                        index = view.line_index_for_location(
                            self.filename, number, side
                        )
                        if index is not None:
                            return index
        return view._first_line_index_for_file(self.filename) if self.filename else None

    def is_header(self, view: DiffView) -> bool:
        index = self.resolve(view)
        return self.header or (
            index is not None and view._all_lines[index].is_folded_file_placeholder
        )

    def top(self, view: DiffView, *, mounted: bool) -> int | None:
        index = self.resolve(view)
        if index is None:
            return None
        if self.is_header(view) and self.filename:
            hunk = view._file_header_hunk_index(self.filename)
            if hunk is None:
                return None
            widget = view._get_file_header_widget(hunk) if mounted else None
            if widget is not None and widget.region.height:
                return (
                    int(view.scroll_y)
                    + widget.region.y
                    - view.scrollable_content_region.y
                )
            return view._hunk_header_top_offsets[hunk]
        row = view._row_for_line_and_pane(index, "old" if self.side == "old" else "new")
        if row is None:
            return None
        if mounted:
            widget = _cursor._target_widget_for_row(view, row)
            if widget is not None and widget.region.height:
                return (
                    int(view.scroll_y)
                    + widget.region.y
                    - view.scrollable_content_region.y
                )
            bounds = _cursor._mounted_block_row_vertical_bounds(view, row)
            if bounds is not None:
                return bounds[0]
        bounds = _cursor._row_vertical_bounds(view, row)
        return bounds[0] if bounds else None


@dataclass(frozen=True)
class FoldState:
    cursor: LineAnchor
    column: int
    cursor_pane: Literal["old", "new"]
    active_pane: Literal["old", "new"]
    visual_anchor: LineAnchor
    visual_column: int | None
    visual_mode: bool
    visual_type: Literal["char", "line"]
    comment_index: int
    viewport: LineAnchor
    viewport_offset: int
    scroll_x: float
    split_scroll_x: float

    @classmethod
    def capture(cls, view: DiffView) -> FoldState:
        cursor = LineAnchor.at(view, view.cursor_line, side=view.cursor_pane)
        header = view.selected_file_header_path()
        if header:
            cursor = LineAnchor(cursor.line, header, header=True)
        index = (
            view._line_index_at_vertical_offset(int(view.scroll_y))
            if view._all_lines
            else None
        )
        viewport = LineAnchor.at(view, index)
        hunk_index = bisect_right(view._hunk_header_top_offsets, int(view.scroll_y)) - 1
        if hunk_index >= 0 and view._diff is not None:
            hunk = view._diff.hunks[hunk_index]
            first = view._hunk_start_line_indices[hunk_index]
            if (
                hunk.starts_file
                and first < len(view._line_top_offsets)
                and view.scroll_y < view._line_top_offsets[first]
            ):
                viewport = LineAnchor(
                    None, view._file_path_for_hunk(hunk_index), header=True
                )
        if viewport.line is not None and viewport.line.is_modified and not view.split:
            top = view._line_top_offsets[viewport.line.line_index]
            viewport = LineAnchor(
                viewport.line,
                viewport.filename,
                side="new" if view.scroll_y > top else "old",
            )
        top = viewport.top(view, mounted=True)
        return cls(
            cursor,
            view.cursor_column,
            view.cursor_pane,
            view.active_pane,
            LineAnchor.at(view, view.visual_anchor_line, side=view.cursor_pane),
            view.visual_anchor_column,
            view.visual_mode,
            view.visual_type,
            view._comment_cursor_index,
            viewport,
            (top - int(view.scroll_y)) if top is not None else 0,
            (view._content_widget or view).scroll_x,
            view._split_horizontal_scroll_x,
        )

    def restore_model(self, view: DiffView) -> None:
        cursor_index = self.cursor.resolve(view)
        anchor_index = self.visual_anchor.resolve(view)
        view._cursor_ui.suspend_line_watch = True
        view._cursor_ui.suspend_pane_watch = True
        try:
            view.cursor_line = cursor_index or 0
            view.cursor_column = self.column
            view.cursor_pane = self.cursor_pane
            view.active_pane = self.active_pane
            view._selected_file_header_hunk = (
                view._file_header_hunk_index(self.cursor.filename)
                if self.cursor.filename and self.cursor.is_header(view)
                else None
            )
            view.current_hunk_index = (
                view._get_hunk_index_for_line(view.cursor_line) or 0
            )
            # Hidden endpoints cannot represent the original text selection.
            view.visual_mode = (
                self.visual_mode
                and cursor_index is not None
                and anchor_index is not None
                and not (
                    self.cursor.is_header(view) or self.visual_anchor.is_header(view)
                )
            )
            view.visual_type = self.visual_type
            view.visual_anchor_line = anchor_index if view.visual_mode else None
            view.visual_anchor_column = self.visual_column if view.visual_mode else None
            view._comment_cursor_index = (
                self.comment_index if not self.cursor.is_header(view) else 0
            )
            view._split_horizontal_scroll_x = self.split_scroll_x
        finally:
            view._cursor_ui.suspend_line_watch = False
            view._cursor_ui.suspend_pane_watch = False

    def restore_scroll(self, view: DiffView, *, mounted: bool = False) -> None:
        top = self.viewport.top(view, mounted=mounted)
        if top is not None:
            target = max(0, top - self.viewport_offset)
            view.scroll_to(y=target, animate=False, force=True, immediate=True)
            if not mounted:
                # Layout still has the old projection's scroll limits until commit.
                view.set_scroll(None, target)
        if view._content_widget is not None:
            view._content_widget.scroll_to(
                x=self.scroll_x, animate=False, force=True, immediate=True
            )
        if view.split:
            view._sync_split_horizontal_scroll(self.split_scroll_x)


@dataclass
class EditorState:
    body: TextArea
    is_open: bool
    focus_id: str | None
    navigation_revision: int

    @classmethod
    def capture(
        cls,
        view: DiffView,
        editor: InlineCommentEditor,
        previous: EditorState | None = None,
    ) -> EditorState:
        focused = view.screen.focused
        body = editor.query_one("#comment-editor-body", TextArea)
        focus_id = (
            focused.id
            if focused is not None and focused in editor.walk_children()
            else None
        )
        if (
            focus_id is None
            and previous is not None
            and previous.body is body
            and view.has_focus
            and previous.navigation_revision == view._file_navigation_revision
        ):
            focus_id = previous.focus_id
        return cls(body, editor.is_open, focus_id, view._file_navigation_revision)

    def restore(self, view: DiffView, editor: InlineCommentEditor) -> None:
        body = editor.query_one("#comment-editor-body", TextArea)
        if body is not self.body:
            body.text = self.body.text
            body.history = self.body.history
            body.selection = self.body.selection
        editor.set_class(not self.is_open, "-hidden")
        if (
            self.focus_id
            and self.is_open
            and view._file_navigation_revision == self.navigation_revision
            and (view.screen.focused is None or view.has_focus)
        ):
            view.screen.set_focus(
                editor.query_one(f"#{self.focus_id}"), scroll_visible=False
            )
        self.focus_id = None
