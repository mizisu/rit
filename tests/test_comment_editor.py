from typing import cast

import pytest
from textual.app import App, ComposeResult
from textual.widgets import OptionList, Static, TextArea

from rit.ui.widgets.comment_editor import EditorKind, InlineCommentEditor


class _CommentEditorTestApp(App[None]):
    def __init__(self, kind: EditorKind = "issue") -> None:
        super().__init__()
        self._kind: EditorKind = kind
        self.result: tuple[str, str, str] | None = None

    def compose(self) -> ComposeResult:
        yield InlineCommentEditor(
            kind=self._kind,
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


def _make_app(kind: EditorKind = "issue") -> _CommentEditorTestApp:
    return _CommentEditorTestApp(kind)


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

        hint = cast(Static, app.query("InlineCommentEditor Static").last()).content

        assert "Ctrl+S pending" in str(hint)
        assert "Ctrl+Shift+S post now" in str(hint)
        assert "Ctrl+Enter" not in str(hint)


@pytest.mark.asyncio
async def test_inline_comment_editor_shows_emoji_shortcode_picker() -> None:
    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship :roc"
        textarea.move_cursor((0, len("ship :roc")))
        await pilot.pause()

        options = app.query_one("#comment-editor-emoji-options", OptionList)
        highlighted = options.highlighted_option

        assert not options.has_class("-hidden")
        assert highlighted is not None
        assert highlighted.id == "rocket"
        assert "🚀 :rocket:" in str(highlighted.prompt)


@pytest.mark.asyncio
async def test_inline_comment_editor_selects_emoji_from_shortcode_picker() -> None:
    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship :roc"
        textarea.move_cursor((0, len("ship :roc")))
        await pilot.pause()

        await pilot.press("enter")
        await pilot.pause()

        assert textarea.text == "ship 🚀"

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.result == ("inline", "ship 🚀", "queue")


@pytest.mark.asyncio
async def test_inline_comment_editor_enter_keeps_newline_without_emoji_picker() -> None:
    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship it"
        textarea.move_cursor((0, len("ship it")))

        await pilot.press("enter")
        await pilot.pause()

        assert textarea.text == "ship it\n"
