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
async def test_inline_comment_editor_queues_with_ctrl_s() -> None:
    """Ctrl+S should save inline comments as pending review drafts."""

    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship it"

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.result == ("inline", "ship it", "queue")


@pytest.mark.asyncio
async def test_inline_comment_editor_posts_with_ctrl_shift_s() -> None:
    """Ctrl+Shift+S should submit inline comments immediately."""

    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship it"

        await pilot.press("ctrl+shift+s")
        await pilot.pause()

        assert app.result == ("inline", "ship it", "post")


@pytest.mark.asyncio
async def test_inline_comment_editor_hint_uses_terminal_safe_shortcuts() -> None:
    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        hint = app.query("InlineCommentEditor Static").last().content

        assert "Ctrl+S pending" in str(hint)
        assert "Ctrl+Shift+S post now" in str(hint)
        assert "Ctrl+Enter" not in str(hint)
