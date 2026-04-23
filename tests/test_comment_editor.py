import pytest
from textual.app import App, ComposeResult
from textual.widgets import TextArea

from rit.ui.widgets.comment_editor import InlineCommentEditor


@pytest.mark.asyncio
async def test_inline_comment_editor_submits_trimmed_body_with_ctrl_s() -> None:
    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: tuple[str, str] | None = None

        def compose(self) -> ComposeResult:
            yield InlineCommentEditor(
                kind="issue",
                title="Add comment",
                placeholder="Write a comment...",
            )

        def on_mount(self) -> None:
            self.query_one(InlineCommentEditor).open()

        def on_inline_comment_editor_submitted(
            self,
            event: InlineCommentEditor.Submitted,
        ) -> None:
            self.result = (event.kind, event.body)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "  hello\nworld  "

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.result == ("issue", "hello\nworld")
