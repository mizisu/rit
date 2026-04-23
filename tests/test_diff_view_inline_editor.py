import pytest
from textual.app import App, ComposeResult

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView


@pytest.mark.asyncio
async def test_open_inline_comment_editor_mounts_below_current_line() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        line_widget = app.query_one("#line-0")
        editor = app.query_one("#diff-inline-comment-editor")

        assert editor.region.y > line_widget.region.y
        assert diff_view.inline_comment_target() == ("test.py", 1, "LEFT")


@pytest.mark.asyncio
async def test_open_inline_comment_editor_uses_left_side_for_deleted_line() -> None:
    patch = "@@ -5,1 +5,0 @@\n-old"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.active_pane = "old"
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        assert diff_view.inline_comment_target() == ("test.py", 5, "LEFT")
