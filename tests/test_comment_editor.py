import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Footer, OptionList, TextArea

from rit.app import RitApp
from rit.core.diff import parse_patch
from rit.state.store import PRStore
from rit.ui.screens.main import MainScreen
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
async def test_inline_comment_editor_grows_with_content_up_to_max_height() -> None:
    app = _make_app(kind="inline")
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        initial_height = textarea.region.height

        textarea.text = "long comment text " * 40
        await pilot.pause()

        assert textarea.region.height > initial_height
        assert textarea.region.height <= 12

        textarea.text = "\n".join(f"line {line}" for line in range(20))
        await pilot.pause()

        assert textarea.region.height == 12


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
async def test_inline_comment_editor_posts_with_post_now_button() -> None:
    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "ship it"

        await pilot.press("tab")
        assert app.screen.focused is app.query_one("#comment-editor-queue", Button)

        await pilot.press("tab")
        assert app.screen.focused is app.query_one("#comment-editor-post", Button)

        await pilot.press("enter")
        await pilot.pause()

        assert app.result == ("inline", "ship it", "post")


@pytest.mark.asyncio
async def test_main_screen_omits_shortcut_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load_all(_store: PRStore) -> None:
        return None

    monkeypatch.setattr(PRStore, "load_all", fake_load_all)
    app = RitApp(owner="test", repo="repo", pr_number=123)

    async with app.run_test():
        assert len(app.screen.query(Footer)) == 0


@pytest.mark.asyncio
async def test_comment_editor_owns_tab_while_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load_all(_store: PRStore) -> None:
        return None

    monkeypatch.setattr(PRStore, "load_all", fake_load_all)
    app = RitApp(owner="test", repo="repo", pr_number=123)
    source_diff = parse_patch("@@ -1 +1 @@\n-old\n+new", "preview.py")

    async with app.run_test() as pilot:
        assert isinstance(app.screen, MainScreen)
        screen = app.screen
        assert screen.current_tab == 0
        await pilot.press("tab")
        assert screen.current_tab == 1

        diff_view = screen.file_changes.diff_view
        await diff_view.show_diff("preview.py", source_diff)
        await pilot.pause()
        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()

        focus_targets = (
            diff_view.query_one("#comment-editor-queue", Button),
            diff_view.query_one("#comment-editor-post", Button),
            diff_view.query_one("#comment-editor-cancel", Button),
            diff_view.query_one("#comment-editor-body", TextArea),
        )
        for target in focus_targets:
            await pilot.press("tab")
            assert screen.current_tab == 1
            assert app.screen.focused is target

        await pilot.press("shift+tab")
        assert screen.current_tab == 1
        assert app.screen.focused is focus_targets[-2]


def test_inline_comment_editor_has_no_post_now_shortcut() -> None:
    assert all(
        binding.action != "submit('post')" for binding in InlineCommentEditor.BINDINGS
    )


@pytest.mark.asyncio
async def test_inline_comment_editor_shows_visible_action_buttons() -> None:
    app = _make_app(kind="inline")
    async with app.run_test() as pilot:
        await pilot.pause()

        buttons = [
            app.query_one("#comment-editor-queue", Button),
            app.query_one("#comment-editor-post", Button),
            app.query_one("#comment-editor-cancel", Button),
        ]

        assert [button.id for button in buttons] == [
            "comment-editor-queue",
            "comment-editor-post",
            "comment-editor-cancel",
        ]
        assert [str(button.label) for button in buttons] == [
            "Add to review",
            "Post now",
            "Cancel",
        ]
        assert buttons[0].variant == "primary"


@pytest.mark.asyncio
async def test_file_comment_editor_offers_pending_and_post_now_buttons() -> None:
    app = _make_app(kind="file")
    async with app.run_test() as pilot:
        await pilot.pause()

        textarea = app.query_one("#comment-editor-body", TextArea)
        textarea.text = "whole file"
        buttons = [
            app.query_one("#comment-editor-queue", Button),
            app.query_one("#comment-editor-post", Button),
            app.query_one("#comment-editor-cancel", Button),
        ]

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert [button.id for button in buttons] == [
            "comment-editor-queue",
            "comment-editor-post",
            "comment-editor-cancel",
        ]
        assert app.result == ("file", "whole file", "queue")


@pytest.mark.asyncio
async def test_existing_comment_editor_shows_update_button() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield InlineCommentEditor(
                kind="inline",
                title="Edit inline comment",
                placeholder="Edit comment...",
                update_existing=True,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        buttons = [
            app.query_one("#comment-editor-submit", Button),
            app.query_one("#comment-editor-cancel", Button),
        ]

        assert [button.id for button in buttons] == [
            "comment-editor-submit",
            "comment-editor-cancel",
        ]
        assert [str(button.label) for button in buttons] == ["Update", "Cancel"]
        assert buttons[0].variant == "primary"


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
