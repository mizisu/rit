import pytest
from textual.app import App, ComposeResult
from textual.widgets import TextArea

from rit.ui.widgets.comment_editor import InlineCommentEditor


def _make_app(kind: str = "issue") -> "App":
    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: tuple[str, str, str] | None = None

        def compose(self) -> ComposeResult:
            yield InlineCommentEditor(
                kind=kind,
                title="Add comment",
                placeholder="Write a comment...",
            )

        def on_mount(self) -> None:
            self.query_one(InlineCommentEditor).open()

        def on_inline_comment_editor_submitted(
            self,
            event: InlineCommentEditor.Submitted,
        ) -> None:
            self.result = (event.kind, event.body, event.mode)

    return TestApp()


@pytest.mark.asyncio
async def test_inline_comment_editor_submits_trimmed_body_with_ctrl_s() -> None:
    app = _make_app(kind="issue")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "  hello\nworld  "

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.result == ("issue", "hello\nworld", "queue")


@pytest.mark.asyncio
async def test_inline_comment_editor_posts_with_ctrl_enter() -> None:
    """Ctrl+Enter should submit the editor in post mode for send-now."""

    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship it"

        await pilot.press("ctrl+enter")
        await pilot.pause()

        assert app.result == ("inline", "ship it", "post")


@pytest.mark.asyncio
async def test_inline_comment_editor_posts_with_ctrl_shift_s() -> None:
    """Ctrl+Shift+S is a fallback post binding for terminals lacking ctrl+enter."""

    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship it"

        await pilot.press("ctrl+shift+s")
        await pilot.pause()

        assert app.result == ("inline", "ship it", "post")
