import pytest
from textual.app import App, ComposeResult
from textual.widgets import TextArea

from rit.core.diff import parse_patch
from rit.state.store import PRStore
from rit.ui.widgets.diff_view import DiffView


@pytest.mark.asyncio
async def test_diff_view_renders_pending_draft_below_line() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.save_pending_inline_comment(
        "hello draft",
        path="test.py",
        line=1,
        side="RIGHT",
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        await pilot.pause()

        line_widget = app.query_one("#line-1")
        draft_widget = app.query_one("#pending-draft-1-right-0")

        assert draft_widget.region.y > line_widget.region.y


@pytest.mark.asyncio
async def test_open_inline_comment_editor_prefills_existing_draft() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.save_pending_inline_comment(
        "hello draft",
        path="test.py",
        line=1,
        side="RIGHT",
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.cursor_line = 1
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        editor = app.query_one("#diff-inline-comment-editor")
        body = editor.query_one("#comment-editor-body", TextArea)

        assert body.text == "hello draft"
