"""Focus-dependent cursor rendering for both diff renderers."""

from typing import Literal

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.content import Content
from textual.widgets import Input, Static, Tree

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView
from rit.ui.widgets.diff_visual import LineAnnotations, LineContent
from tests.conftest import wait_until


def _cursor_visibility(view: DiffView) -> tuple[bool, bool, bool]:
    line_highlight = False
    cursor_cell = False
    line_number = False
    for code in view.query(".code-content").results(Static):
        visual = code.visual
        if isinstance(visual, LineContent):
            line_highlight |= "on $primary 25%" in visual.line_styles
            contents = visual.code_lines
        else:
            line_highlight |= code.has_class("-cursor")
            contents = [code.content] if isinstance(code.content, Content) else []
        cursor_cell |= any(
            str(span.style) == "reverse"
            for content in contents
            if content is not None
            for span in content.spans
        )
    for prefix in view.query(".line-prefix"):
        line_number |= (
            bool(prefix._active_rows)
            if isinstance(prefix, LineAnnotations)
            else prefix.has_class("-cursor")
        )
    return line_highlight, cursor_cell, line_number


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
@pytest.mark.parametrize("block_threshold", [1, 1000])
async def test_diff_cursor_follows_focus_without_losing_position(
    mode: Literal["unified", "split"], block_threshold: int
) -> None:
    class TestApp(App):
        def compose(self) -> ComposeResult:
            with Horizontal():
                yield Tree("Files", id="file-tree")
                yield DiffView(mode=mode, id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        tree = app.query_one(Tree)
        view = app.query_one(DiffView)
        view.BLOCK_RENDER_LINE_THRESHOLD = block_threshold
        tree.focus()
        await wait_until(lambda: tree.has_focus)
        await view.show_diff(
            "test.py", parse_patch("@@ -1,3 +1,3 @@\n line1\n line2\n line3", "test.py")
        )
        await pilot.pause()
        assert _cursor_visibility(view) == (False, False, False)

        view.focus()
        await wait_until(lambda: _cursor_visibility(view) == (True, True, True))
        view._move_cursor(line=1, column=2)
        await pilot.pause()
        position = (view.cursor_line, view.cursor_column, view.cursor_pane)
        assert position[:2] == (1, 2)

        tree.focus()
        await wait_until(lambda: _cursor_visibility(view) == (False, False, False))
        assert (view.cursor_line, view.cursor_column, view.cursor_pane) == position

        view.focus()
        await wait_until(lambda: _cursor_visibility(view) == (True, True, True))
        assert (view.cursor_line, view.cursor_column, view.cursor_pane) == position

        await pilot.press("/")
        search = view.query_one("#diff-search-input", Input)
        await wait_until(lambda: search.has_focus)
        assert _cursor_visibility(view) == (True, True, True)

        tree.focus()
        await wait_until(lambda: _cursor_visibility(view) == (False, False, False))
        view._move_cursor(line=2)
        await pilot.pause()
        assert _cursor_visibility(view) == (False, False, False)

        search.focus()
        await wait_until(lambda: _cursor_visibility(view) == (True, True, True))
        assert view.cursor_line == 2
