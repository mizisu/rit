import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static, TextArea

from rit.core.diff import parse_patch
from rit.state.models import PRComment, ReviewThread
from rit.state.store import PRStore
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.diff_view import DiffView


def _make_review_thread(*, root_id: int, side: str) -> ReviewThread:
    root = PRComment.model_validate(
        {
            "databaseId": root_id,
            "body": "comment",
            "path": "test.py",
            "line": 2,
            "originalLine": 2,
            "side": side,
        }
    )
    return ReviewThread.model_validate(
        {
            "path": "test.py",
            "line": 2,
            "originalLine": 2,
            "comments": {"nodes": [root]},
        }
    )


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

        assert isinstance(draft_widget, CommentCard)
        assert len(app.query("CommentCard.pending-draft")) == 1
        assert draft_widget.region.y > line_widget.region.y


@pytest.mark.asyncio
async def test_unified_pending_draft_starts_after_line_number_gutter() -> None:
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
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        await pilot.pause()

        code_widget = app.query_one("#line-1 .code-content", Static)
        draft_widget = app.query_one("#pending-draft-1-right-0")

        assert draft_widget.region.x >= code_widget.region.x


@pytest.mark.asyncio
async def test_split_pending_drafts_dock_to_matching_side() -> None:
    patch = """@@ -1,3 +1,3 @@
 line1
-old alpha
+new alpha
 line2"""
    store = PRStore()
    store.save_pending_inline_comment(
        "left draft",
        path="test.py",
        line=2,
        side="LEFT",
    )
    store.save_pending_inline_comment(
        "right draft",
        path="test.py",
        line=2,
        side="RIGHT",
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(140, 16)) as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        await pilot.pause()

        old_code = app.query_one("#line-1-old .code-content", Static)
        new_code = app.query_one("#line-1-new .code-content", Static)
        left_draft = app.query_one("#pending-draft-1-left-0")
        right_draft = app.query_one("#pending-draft-1-right-1")

        assert old_code.region.x <= left_draft.region.x < new_code.region.x
        assert right_draft.region.x >= new_code.region.x


@pytest.mark.asyncio
async def test_split_inline_threads_dock_to_matching_side() -> None:
    patch = """@@ -1,3 +1,3 @@
 line1
-old alpha
+new alpha
 line2"""
    store = PRStore()
    store.state.review_threads = [
        _make_review_thread(root_id=101, side="LEFT"),
        _make_review_thread(root_id=102, side="RIGHT"),
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(140, 16)) as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        await pilot.pause()

        old_code = app.query_one("#line-1-old .code-content", Static)
        new_code = app.query_one("#line-1-new .code-content", Static)
        left_thread = app.query_one("#inline-thread-101")
        right_thread = app.query_one("#inline-thread-102")

        assert old_code.region.x <= left_thread.region.x < new_code.region.x
        assert right_thread.region.x >= new_code.region.x


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
