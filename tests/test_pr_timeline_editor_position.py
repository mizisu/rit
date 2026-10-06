import pytest
from textual import on
from textual.app import App, ComposeResult
from textual.widgets import Button, TextArea

from rit.state.store import PRStore
from rit.ui.components.pr_timeline import PRTimeline
from rit.ui.screens.comment_submit import CommentSubmitScreen
from rit.ui.widgets.comment_editor import InlineCommentEditor
from tests.conftest import wait_until


@pytest.mark.asyncio
async def test_pr_comment_overlay_submits_and_cancels_without_changing_timeline() -> (
    None
):
    store = PRStore()
    submitted: list[str] = []

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Button("Add comment", id="add-comment")
            yield PRTimeline(store)

        @on(Button.Pressed, "#add-comment")
        def add_comment(self) -> None:
            self.query_one(PRTimeline).start_issue_comment()

        @on(InlineCommentEditor.Submitted)
        def submit(self, event: InlineCommentEditor.Submitted) -> None:
            assert event.kind == "issue"
            submitted.append(event.body)
            self.query_one(PRTimeline).close_issue_comment()

        @on(InlineCommentEditor.Cancelled)
        def cancel(self, event: InlineCommentEditor.Cancelled) -> None:
            assert event.kind == "issue"
            self.query_one(PRTimeline).close_issue_comment()

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        children = list(timeline.children)
        main_screen = app.screen
        button = app.query_one("#add-comment", Button)
        button.focus()
        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, CommentSubmitScreen))
        body = app.screen.query_one(TextArea)
        await wait_until(lambda: body.has_focus)

        assert list(timeline.children) == children
        assert not timeline.query(InlineCommentEditor)
        timeline.start_issue_comment()
        assert len(app.screen_stack) == 2
        body.text = "   "
        await pilot.press("ctrl+s")
        assert isinstance(app.screen, CommentSubmitScreen)
        assert submitted == []

        body.text = "ship :roc"
        body.move_cursor((0, len(body.text)))
        await pilot.press("enter")
        assert body.text == "ship 🚀"
        await pilot.press("ctrl+enter")
        await wait_until(lambda: app.screen is main_screen and button.has_focus)
        assert submitted == ["ship 🚀"]

        await pilot.press("enter")
        await wait_until(lambda: isinstance(app.screen, CommentSubmitScreen))
        body = app.screen.query_one(TextArea)
        await wait_until(lambda: body.has_focus)
        assert body.text == ""
        body.text = "discard this"
        await pilot.press("escape")
        await wait_until(lambda: app.screen is main_screen and button.has_focus)
        assert submitted == ["ship 🚀"]
        assert list(timeline.children) == children
