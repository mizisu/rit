import asyncio
from typing import Literal

import pytest
from textual import on
from textual.app import App, ComposeResult
from textual.widgets import Button, Static, TextArea

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRComment, PRFile, PRUser, ReviewThread
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges
from rit.ui.widgets.comment_editor import InlineCommentEditor
from rit.ui.widgets.diff_view import DiffView
from rit.ui.widgets.review_thread_card import ReviewThreadItem
from tests.conftest import wait_until


def _combined_two_file_diff() -> FileDiff:
    return FileDiff(
        filename="All files",
        show_hunk_headers=False,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(1, 1, "one", "one")],
                starts_file=True,
                file_path="one.py",
            ),
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(1, 1, "two", "two")],
                starts_file=True,
                file_path="two.py",
            ),
        ],
    )


@pytest.mark.asyncio
async def test_editors_follow_the_file_layout_and_selected_pending_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.save_pending_file_comment("first file draft", path="one.py")
    store.save_pending_file_comment("second file draft", path="one.py")
    store.save_pending_inline_comment("left draft", path="two.py", line=1, side="LEFT")
    store.save_pending_inline_comment(
        "right draft", path="two.py", line=1, side="RIGHT"
    )
    source = _combined_two_file_diff()
    source.hunks[0].file_status = "added"
    source.hunks[0].old_count = 0
    source.hunks[0].lines = [DiffLine(None, 1, new_content="one", is_added=True)]

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="split")

    app = TestApp()
    async with app.run_test(size=(140, 36)) as pilot:
        view = app.query_one(DiffView)
        monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", 0)
        await view.show_diff("All files", source)
        assert view.split and not view._split_for_line(0)
        view._set_file_header_selection(0)
        assert await view.open_file_comment_editor()
        await pilot.pause()
        editor = view.query_one(InlineCommentEditor)
        card = view._pending_file_comment_widgets_by_hunk[0][0]
        thread = next(
            parent for parent in card.ancestors if isinstance(parent, ReviewThreadItem)
        )
        assert editor.region.x == thread.region.x
        assert editor.region.width == thread.region.width
        await view.close_file_comment_editor()

        for kind in ("file", "inline"):
            for cursor in (1, 2):
                if kind == "file":
                    view._set_file_header_selection(0)
                else:
                    view.jump_to_line_index(1, side="LEFT" if cursor == 1 else "RIGHT")
                view._comment_cursor_index = cursor
                open_editor = (
                    view.open_file_comment_editor
                    if kind == "file"
                    else view.open_inline_comment_editor
                )
                assert await open_editor(edit=True)
                await pilot.pause()
                editor = view.query_one(InlineCommentEditor)
                cards = (
                    view._pending_file_comment_widgets_by_hunk[0]
                    if kind == "file"
                    else view._pending_comment_widgets_by_line[1]
                )
                card = cards[cursor - 1]
                thread = next(
                    parent
                    for parent in card.ancestors
                    if isinstance(parent, ReviewThreadItem)
                )
                assert thread in editor.ancestors
                assert thread.content_region.contains_region(editor.region)
                assert editor.region.y >= card.region.bottom
                assert editor.styles.background == thread.styles.background
                assert str(editor.query_one("#comment-editor-queue", Button).label) == (
                    "Save changes  Ctrl+S"
                )
                body = editor.query_one(TextArea)
                assert body.styles.background == thread.styles.background
                assert not body.styles.border_top[0]
                body.text = "unsaved edit"
                await view.show_diff("All files", source)
                await pilot.pause()
                assert view.query_one(TextArea).text == "unsaved edit"
                close_editor = (
                    view.close_file_comment_editor
                    if kind == "file"
                    else view.close_inline_comment_editor
                )
                await close_editor()
        assert [draft.body for draft in store.state.pending_review.comments] == [
            "first file draft",
            "second file draft",
            "left draft",
            "right draft",
        ]


@pytest.mark.asyncio
async def test_file_headers_are_cursor_targets_for_file_comments() -> None:
    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("All files", _combined_two_file_diff())
        await pilot.pause()
        diff_view.focus()

        await pilot.press("k")
        await pilot.pause()

        first_header = app.query_one("#file-header-0", Static)
        assert diff_view.selected_file_header_path() == "one.py"
        assert first_header.has_class("-selected")
        assert all(
            "-cursor" not in widget.classes for widget in diff_view._get_code_widgets(0)
        )

        assert await diff_view.open_file_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        editor = diff_view.query_one("#diff-file-comment-editor")
        context = editor.query_one(".comment-editor-context", Static)
        assert editor in diff_view.walk_children()
        assert editor.region.y > first_header.region.y
        assert str(context.content) == "Entire file: one.py"
        assert diff_view.file_comment_target() == "one.py"

        await diff_view.close_file_comment_editor()
        await pilot.pause()
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()
        assert app.screen.focused is diff_view
        await pilot.press("j")
        await pilot.pause()
        assert diff_view.selected_file_header_path() is None
        assert all(
            "-cursor" in widget.classes for widget in diff_view._get_code_widgets(0)
        )

        await pilot.press("j")
        await pilot.pause()
        second_header = app.query_one("#file-header-1", Static)
        assert diff_view.selected_file_header_path() == "two.py"
        assert second_header.has_class("-selected")
        assert diff_view.cursor_line == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_file_editor_matches_inline_editor_layout(
    mode: Literal["unified", "split"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode=mode)

    app = TestApp()
    async with app.run_test(size=(160, 60)) as pilot:
        view = app.query_one(DiffView)
        monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", 0)
        await view.show_diff("All files", _combined_two_file_diff())
        for width in (160, 80, 44):
            await pilot.resize_terminal(width, 30)
            sizes = []
            for kind in ("inline", "file"):
                if kind == "inline":
                    assert await view.open_inline_comment_editor()
                else:
                    view._set_file_header_selection(0)
                    assert view.selected_file_header_path() == "one.py"
                    assert await view.open_file_comment_editor()
                assert len(app.screen_stack) == 1
                editor = view.query_one(InlineCommentEditor)
                body = editor.query_one(TextArea)
                await wait_until(
                    lambda body=body, editor=editor: (
                        body.has_focus and editor.region.width > 0
                    )
                )
                sizes.append(editor.region.width)
                assert view.region.contains_region(editor.region)
                assert editor.region.width <= 96
                assert editor.region.x >= view.region.x
                assert not await view.open_inline_comment_editor()
                assert not await view.open_file_comment_editor()

                close_editor = (
                    view.close_inline_comment_editor
                    if kind == "inline"
                    else view.close_file_comment_editor
                )
                await asyncio.gather(close_editor(), close_editor())
                assert len(app.screen_stack) == 1
                assert not editor.is_attached
                await wait_until(lambda: view.has_focus)
            assert sizes[0] == sizes[1]


@pytest.mark.asyncio
async def test_file_editor_tracks_selected_draft_reply_and_new_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.save_pending_inline_comment("other file", path="a.py", line=1, side="RIGHT")
    store.save_pending_file_comment("first draft", path="one.py")
    store.save_pending_file_comment("second draft", path="one.py")
    root = PRComment(
        id=501, node_id="PRRC_501", body="root", path="one.py", subject_type="file"
    )
    reply = PRComment(
        id=502,
        node_id="PRRC_502",
        body="reply",
        path="one.py",
        subject_type="file",
        in_reply_to_id=501,
    )
    store.state.review_threads = [
        ReviewThread.model_validate(
            {
                "id": "thread-501",
                "path": "one.py",
                "subjectType": "FILE",
                "comments": {"nodes": [root, reply]},
            }
        )
    ]

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        view = app.query_one(DiffView)
        monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", 0)
        await view.show_diff("All files", _combined_two_file_diff())
        await pilot.pause()
        assert view._set_file_header_selection(0)

        thread_widget = view._file_comment_widgets_by_hunk[0][0]
        assert isinstance(thread_widget, ReviewThreadItem)
        thread_widget.collapsed = False
        await pilot.pause()
        for cursor, text, draft_index, edit_target in (
            (2, "second draft", 2, None),
            (4, "reply", None, reply),
            (0, "", None, None),
        ):
            if edit_target is not None:
                thread_widget = view._file_comment_widgets_by_hunk[0][0]
                assert isinstance(thread_widget, ReviewThreadItem)
                thread_widget.collapsed = False
                await pilot.pause()
            view._comment_cursor_index = cursor
            assert await view.open_file_comment_editor(edit=cursor != 0)
            editor = app.screen.query_one(
                "#diff-file-comment-editor", InlineCommentEditor
            )
            body = editor.query_one(TextArea)
            await wait_until(lambda body=body: body.has_focus)
            assert body.text == text
            assert body.cursor_location == body.document.end
            assert view.file_comment_draft_index() == draft_index
            assert view.file_comment_edit_target() == edit_target
            assert bool(editor.query("#comment-editor-post")) == (edit_target is None)
            body.text = "unsaved edit"

            await view.close_file_comment_editor()
            assert view.file_comment_draft_index() is None
            assert view.file_comment_edit_target() is None
            await wait_until(lambda: view.has_focus)

        thread_widget = view._file_comment_widgets_by_hunk[0][0]
        assert isinstance(thread_widget, ReviewThreadItem)
        thread_widget.collapsed = False
        await pilot.pause()
        view._comment_cursor_index = 4
        assert view.selected_reply_comment() == reply
        assert await view.open_reply_comment_editor()
        assert view.reply_comment_target() == reply
        assert len(app.screen_stack) == 1
        reply_editor = view.query_one("#diff-reply-comment-editor", InlineCommentEditor)
        assert reply_editor.query_one(TextArea).text == ""
        assert any(
            isinstance(parent, ReviewThreadItem) for parent in reply_editor.ancestors
        )
        assert not await view.open_inline_comment_editor()
        assert not await view.open_file_comment_editor()
        assert not await view.open_reply_comment_editor()
        await view.close_reply_comment_editor()
        assert view.reply_comment_target() is None

        thread_widget = view._file_comment_widgets_by_hunk[0][0]
        assert isinstance(thread_widget, ReviewThreadItem)
        thread_widget.collapsed = True
        view._comment_cursor_index = 3
        assert view.active_review_comment() is None
        assert view.selected_reply_comment() == root
        assert not await view.open_file_comment_editor(edit=True)

        assert store.state.pending_review.comments[2].body == "second draft"
        assert reply.body == "reply"


@pytest.mark.asyncio
async def test_editor_buttons_keep_keys_from_diff_and_file_navigation() -> None:
    store = PRStore()
    store.state.files = [PRFile(filename="one.py"), PRFile(filename="two.py")]
    submitted: list[InlineCommentEditor.Submitted] = []
    cancelled: list[InlineCommentEditor.Cancelled] = []

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

        @on(InlineCommentEditor.Submitted)
        def on_submitted(self, event: InlineCommentEditor.Submitted) -> None:
            submitted.append(event)

        @on(InlineCommentEditor.Cancelled)
        def on_cancelled(self, event: InlineCommentEditor.Cancelled) -> None:
            cancelled.append(event)

    app = TestApp()
    async with app.run_test() as pilot:
        files = app.query_one(FileChanges)
        view = files.diff_view
        view.mode = "unified"
        await view.show_diff("All files", _combined_two_file_diff())

        for kind in ("inline", "file"):
            if kind == "inline":
                assert await view.open_inline_comment_editor()
            else:
                view._set_file_header_selection(0)
                assert await view.open_file_comment_editor()
            editor = app.screen.query_one(
                f"#diff-{kind}-comment-editor", InlineCommentEditor
            )
            body = editor.query_one(TextArea)
            await wait_until(lambda body=body: body.has_focus)
            body.text = "keep this draft"
            await pilot.press("tab")
            button = editor.query_one("#comment-editor-queue", Button)
            assert button.has_focus

            for key in ("2", "j", "}", "{", "[", "]", "<", ">", "p", "|", "/"):
                await pilot.press(key)
                assert button.has_focus
                assert view.current_file == "All files"
                assert view.cursor_line == 0
                assert view.mode == "unified"
                assert files.sidebar_width == 35
                assert view._cursor_ui.pending_count == ""
                assert body.text == "keep this draft"

            await pilot.press("enter")
            await wait_until(
                lambda kind=kind: len(submitted) == (1 if kind == "inline" else 3)
            )
            assert submitted[-1].body == "keep this draft"
            assert submitted[-1].kind == kind
            assert submitted[-1].mode == "queue"

            await pilot.press("tab", "enter")
            await wait_until(
                lambda kind=kind: len(submitted) == (2 if kind == "inline" else 4)
            )
            assert submitted[-1].mode == "post"
            await pilot.press("tab", "enter")
            await wait_until(
                lambda kind=kind: len(cancelled) == (1 if kind == "inline" else 2)
            )
            assert cancelled[-1].kind == kind
            assert not view._folded_file_paths
            assert not view._manually_folded_files
            if kind == "inline":
                await view.close_inline_comment_editor()
            else:
                await view.close_file_comment_editor()


@pytest.mark.asyncio
async def test_selecting_visible_file_header_preserves_scroll_position() -> None:
    first_lines = [
        DiffLine(line, line, f"one {line}", f"one {line}") for line in range(1, 13)
    ]
    second_lines = [
        DiffLine(line, line, f"two {line}", f"two {line}") for line in range(1, 21)
    ]
    diff = FileDiff(
        filename="All files",
        show_hunk_headers=False,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=len(first_lines),
                new_start=1,
                new_count=len(first_lines),
                lines=first_lines,
                starts_file=True,
                file_path="one.py",
            ),
            DiffHunk(
                old_start=1,
                old_count=len(second_lines),
                new_start=1,
                new_count=len(second_lines),
                lines=second_lines,
                starts_file=True,
                file_path="two.py",
            ),
        ],
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(80, 8)) as pilot:
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("All files", diff)
        await pilot.pause()
        diff_view.jump_to_line_index(11, side="RIGHT", focus=True)
        await pilot.pause()
        await pilot.pause()
        before = int(diff_view.scroll_y)

        diff_view.action_scroll_down()
        await pilot.pause()

        assert diff_view.selected_file_header_path() == "two.py"
        assert int(diff_view.scroll_y) == before


@pytest.mark.asyncio
async def test_open_inline_comment_editor_sits_below_current_line() -> None:
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

        assert len(app.screen_stack) == 1
        editor = diff_view.query_one("#diff-inline-comment-editor")
        body = editor.query_one("#comment-editor-body", TextArea)
        context = editor.query_one(".comment-editor-context", Static)

        assert editor in diff_view.walk_children()
        assert editor.region.y > diff_view.query_one("#line-0").region.y
        assert body.region.height >= 3
        assert diff_view.inline_comment_target() == ("test.py", 1, "LEFT")
        assert str(context.content) == "Selected: test.py:1 (old)"


@pytest.mark.asyncio
async def test_closing_inline_editor_removes_hit_target_and_restores_diff_focus() -> (
    None
):
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        view = app.query_one(DiffView)
        await view.show_diff("test.py", parse_patch("@@ -0,0 +1 @@\n+new", "test.py"))
        await pilot.pause()

        for _ in range(2):
            assert await view.open_inline_comment_editor()
            await pilot.pause()
            body = app.screen.query_one("#comment-editor-body", TextArea)
            x, y, _, _ = body.region
            x += 1
            assert body.is_attached
            assert app.screen.get_widget_at(x, y)[0] is body

            await view.close_inline_comment_editor()
            await wait_until(lambda: view.has_focus)
            assert not body.is_attached
            assert app.screen.get_widget_at(x, y)[0] is not body


@pytest.mark.asyncio
async def test_open_inline_comment_editor_prefills_selected_submitted_comment() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    comment = PRComment(
        id=501,
        node_id="PRRC_501",
        body="submitted body",
        user=PRUser(login="alice"),
        path="test.py",
        line=1,
        side="RIGHT",
    )
    store = PRStore()
    store.state.review_threads = [
        ReviewThread.model_validate(
            {
                "id": "thread-501",
                "path": "test.py",
                "line": 1,
                "diffSide": "RIGHT",
                "comments": {"nodes": [comment]},
            }
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.cursor_line = 1
        diff_view._comment_cursor_index = 1
        diff_view.focus()
        await pilot.pause()

        assert diff_view.active_review_comment() == comment
        assert await diff_view.open_inline_comment_editor(edit=True) is True
        await pilot.pause()
        await pilot.pause()

        editor = app.screen.query_one("#diff-inline-comment-editor")
        body = editor.query_one("#comment-editor-body", TextArea)
        title = editor.query_one(".comment-editor-title", Static)

        assert body.text == "submitted body"
        assert str(title.content) == "Edit comment"
        assert diff_view.inline_comment_edit_target() == comment
        assert diff_view.inline_comment_draft_index() is None

        await diff_view.close_inline_comment_editor()
        thread_widget = diff_view._comment_widgets_by_line[1][0]
        assert isinstance(thread_widget, ReviewThreadItem)
        thread_widget.collapsed = True
        assert diff_view.active_review_comment() is None
        assert diff_view.selected_reply_comment() == comment
        assert not await diff_view.open_inline_comment_editor(edit=True)
        assert await diff_view.open_reply_comment_editor()
        assert diff_view.reply_comment_target() == comment
        await diff_view.close_reply_comment_editor()


@pytest.mark.asyncio
async def test_growing_inline_editor_updates_virtualized_diff_geometry() -> None:
    patch = "@@ -1,40 +1,40 @@\n" + "\n".join(f" line {line}" for line in range(1, 41))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 24)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        assert diff_view._virt.active is True

        diff_view.cursor_line = 1
        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        initial_virtual_height = diff_view._virtual_content_height
        initial_offsets = list(diff_view._line_top_offsets)
        initial_scroll = diff_view.scroll_y
        body = app.screen.query_one("#comment-editor-body", TextArea)
        initial_body_height = body.region.height
        body.text = "\n".join(f"comment line {line}" for line in range(20))
        await wait_until(lambda: body.region.height > initial_body_height)

        await wait_until(
            lambda: diff_view._virtual_content_height > initial_virtual_height
        )
        delta = body.region.height - initial_body_height
        assert diff_view._virtual_content_height == initial_virtual_height + delta
        assert diff_view._line_top_offsets[:2] == initial_offsets[:2]
        assert diff_view._line_top_offsets[2] == initial_offsets[2] + delta
        assert diff_view.scroll_y == initial_scroll
        assert body.text.endswith("comment line 19")
        assert body.has_focus is True


@pytest.mark.asyncio
async def test_open_inline_comment_editor_uses_visual_selection_range() -> None:
    patch = """@@ -1,3 +1,4 @@
 line1
 line2
+line2.5
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.visual_mode = True
        diff_view.visual_type = "line"
        diff_view.visual_anchor_line = 0
        diff_view.cursor_line = 3
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()

        editor = app.screen.query_one("#diff-inline-comment-editor")
        context = editor.query_one(".comment-editor-context", Static)

        assert diff_view.inline_comment_target() == ("test.py", 4, "RIGHT")
        assert diff_view.inline_comment_start_line() == 1
        assert diff_view.inline_comment_start_side() == "RIGHT"
        assert str(context.content) == "Selected: test.py:1-4 (new)"


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
