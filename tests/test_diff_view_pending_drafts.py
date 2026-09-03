import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static, TextArea

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PendingReviewComment, PRComment, ReviewThread
from rit.state.pending_review import merge_pending_review_drafts
from rit.state.store import PRStore
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.diff_comments import estimate_pending_draft_height
from rit.ui.widgets.diff_view import DiffView
from rit.ui.widgets.review_thread_card import ReviewThreadItem
from tests.conftest import wait_until


def test_estimate_pending_draft_height_does_not_materialize_body_lines() -> None:
    class NoSplitLines(str):
        def splitlines(self, *_args: object, **_kwargs: object) -> list[str]:
            raise AssertionError(
                "pending draft height should count lines without split"
            )

    draft = PendingReviewComment(
        body="",
        path="test.py",
        line=1,
        side="RIGHT",
    )
    draft.body = NoSplitLines("one\ntwo\nthree\nfour")

    assert estimate_pending_draft_height(draft) == 7


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
async def test_diff_view_renders_pending_file_draft_below_file_header() -> None:
    store = PRStore()
    store.save_pending_file_comment("whole file", path="test.py")
    store.save_pending_inline_comment(
        "line comment",
        path="test.py",
        line=1,
        side="RIGHT",
    )
    store.state.review_threads = [
        ReviewThread.model_validate(
            {
                "path": "test.py",
                "subjectType": "FILE",
                "comments": {
                    "nodes": [
                        {
                            "databaseId": 41,
                            "body": "submitted file comment",
                            "path": "test.py",
                            "subjectType": "FILE",
                            "pullRequestReview": {"databaseId": 90},
                        }
                    ]
                },
            }
        )
    ]
    diff = FileDiff(
        filename="All files",
        show_hunk_headers=False,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[DiffLine(1, 1, "old", "new")],
                starts_file=True,
                file_path="test.py",
            )
        ],
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 0  # type: ignore[assignment]

        await diff_view.show_diff("All files", diff)
        await pilot.pause()
        await pilot.pause()

        assert diff_view._virt.active is True
        file_header = app.query_one("#file-header-0")
        draft_widget = app.query_one("#pending-file-draft-0-0", CommentCard)
        pending_item = next(
            ancestor
            for ancestor in draft_widget.ancestors
            if isinstance(ancestor, ReviewThreadItem)
        )
        submitted_item = app.query_one("#inline-thread-41", ReviewThreadItem)
        line_draft = app.query_one("#pending-draft-0-right-0", CommentCard)
        line_item = next(
            ancestor
            for ancestor in line_draft.ancestors
            if isinstance(ancestor, ReviewThreadItem)
        )

        assert pending_item.has_class("pending-draft-thread")
        assert "entire file" in str(pending_item.title)
        assert "entire file" in str(submitted_item.title)
        assert file_header.region.y < pending_item.region.y < submitted_item.region.y
        assert pending_item.region.x == line_item.region.x
        assert pending_item.region.width == line_item.region.width

        diff_view._set_file_header_selection(0)
        diff_view.focus()
        await pilot.press("j")
        assert diff_view.active_pending_draft_index() == 0
        assert "--cursor-line" in pending_item.classes

        await pilot.press("j")
        selected_comment = diff_view.active_review_comment()
        assert selected_comment is not None
        assert selected_comment.id == 41
        assert "--cursor-line" in submitted_item.classes

        await pilot.press("j")
        assert diff_view.selected_file_header_path() is None
        assert diff_view.active_review_comment() is None

        await pilot.press("k")
        assert diff_view.active_review_comment() == selected_comment
        await pilot.press("k")
        assert diff_view.active_pending_draft_index() == 0
        await pilot.press("k")
        assert diff_view.selected_file_header_path() == "test.py"
        assert diff_view._comment_cursor_index == 0

        await pilot.click(draft_widget)
        await pilot.pause()
        assert diff_view.active_pending_draft_index() == 0

        submitted_card = submitted_item.comment_card_at(0)
        assert submitted_card is not None
        await pilot.click(submitted_card)
        await pilot.pause()
        assert diff_view.active_review_comment() == selected_comment


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
        pending_item = next(
            ancestor
            for ancestor in draft_widget.ancestors
            if isinstance(ancestor, ReviewThreadItem)
        )

        assert isinstance(draft_widget, CommentCard)
        assert pending_item.has_class("--inline")
        assert pending_item.comment_card_at(0) is draft_widget
        assert len(app.query("CommentCard.pending-draft")) == 1
        assert draft_widget.region.y > line_widget.region.y


@pytest.mark.asyncio
async def test_virtualized_pending_draft_keeps_collapsed_state_after_remount() -> None:
    context_lines = "\n".join(f" line{line}" for line in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"
    store = PRStore()
    store.save_pending_inline_comment(
        "line one\nline two\nline three",
        path="test.py",
        line=1,
        side="RIGHT",
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(80, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await wait_until(
            lambda: (
                diff_view._virt.active
                and len(diff_view.query("CommentCard.pending-draft")) == 1
            )
        )

        diff_view.cursor_line = diff_view._comment_line_indices[0]
        diff_view.focus()
        await pilot.press("j")
        await pilot.pause()

        draft = diff_view.query_one("CommentCard.pending-draft", CommentCard)
        assert "--cursor-line" in draft.classes

        await pilot.press("enter")
        await pilot.pause()
        assert draft.collapsed is True

        diff_view.scroll_to(y=diff_view.max_scroll_y, animate=False)
        await wait_until(
            lambda: (
                diff_view._virt.window_start > 0
                and len(diff_view.query("CommentCard.pending-draft")) == 0
            )
        )

        diff_view.scroll_to(y=0, animate=False)
        await wait_until(
            lambda: (
                diff_view._virt.window_start == 0
                and len(diff_view.query("CommentCard.pending-draft")) == 1
            )
        )

        remounted_draft = diff_view.query_one("CommentCard.pending-draft", CommentCard)
        assert remounted_draft.collapsed is True


@pytest.mark.asyncio
async def test_pending_draft_keeps_collapsed_state_when_sync_replaces_model() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    diff = parse_patch(patch, "test.py")
    store = PRStore()
    original = store.save_pending_inline_comment(
        "pending comment",
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
        await diff_view.show_diff("test.py", diff)
        await wait_until(lambda: len(diff_view.query("CommentCard.pending-draft")) == 1)

        diff_view.cursor_line = diff_view._comment_line_indices[0]
        diff_view.focus()
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        collapsed_draft = diff_view.query_one("CommentCard.pending-draft", CommentCard)
        assert collapsed_draft.collapsed is True

        server_copy = original.model_copy(update={"review_comment_id": 91001})
        replacement = merge_pending_review_drafts([original], [server_copy])[0]
        assert replacement is not original
        assert replacement.review_comment_id == 91001
        store.state.pending_review_comments = [replacement]

        await diff_view.show_diff("test.py", diff)
        await wait_until(
            lambda: (
                len(diff_view.query("CommentCard.pending-draft")) == 1
                and next(iter(diff_view._pending_comment_drafts_by_line.values()))[0]
                is replacement
            )
        )

        remounted_draft = diff_view.query_one("CommentCard.pending-draft", CommentCard)
        assert remounted_draft.collapsed is True


@pytest.mark.asyncio
async def test_virtualized_pending_draft_collapse_updates_scroll_geometry() -> None:
    context_lines = "\n".join(f" line{line}" for line in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"
    store = PRStore()
    store.save_pending_inline_comment(
        "\n".join(f"comment line {line}" for line in range(1, 9)),
        path="test.py",
        line=1,
        side="RIGHT",
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(80, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await wait_until(
            lambda: (
                diff_view._virt.active
                and len(diff_view.query("CommentCard.pending-draft")) == 1
            )
        )

        draft_line = diff_view._comment_line_indices[0]
        next_line = draft_line + 1
        diff_view.cursor_line = draft_line
        diff_view.focus()
        await pilot.press("j")
        await pilot.pause()

        draft = diff_view.query_one("CommentCard.pending-draft", CommentCard)
        pending_item = next(
            ancestor
            for ancestor in draft.ancestors
            if isinstance(ancestor, ReviewThreadItem)
        )
        await wait_until(
            lambda: (
                len(draft.query(".comment-body-plain")) == 1
                and pending_item.region.height > 0
            )
        )
        expanded_virtual_height = diff_view._virtual_content_height
        expanded_next_line_top = diff_view._line_top_offsets[next_line]
        line_bottom_without_draft = (
            diff_view._line_top_offsets[draft_line]
            + diff_view._line_heights[draft_line]
        )
        expanded_draft_height = expanded_next_line_top - line_bottom_without_draft

        await pilot.press("enter")
        await wait_until(
            lambda: (
                draft.collapsed
                and pending_item.collapsed
                and diff_view._virtual_content_height < expanded_virtual_height
            )
        )

        collapsed_draft_height = (
            diff_view._line_top_offsets[next_line] - line_bottom_without_draft
        )
        geometry_delta = expanded_draft_height - collapsed_draft_height
        assert geometry_delta > 0
        assert (
            diff_view._virtual_content_height
            == expanded_virtual_height - geometry_delta
        )
        assert (
            diff_view._line_top_offsets[next_line]
            == expanded_next_line_top - geometry_delta
        )

        collapsed_virtual_height = diff_view._virtual_content_height
        collapsed_next_line_top = diff_view._line_top_offsets[next_line]
        collapsed_max_scroll_y = int(diff_view.max_scroll_y)

        diff_view.scroll_to(y=diff_view.max_scroll_y, animate=False)
        await wait_until(
            lambda: (
                diff_view._virt.window_start > 0
                and len(diff_view.query("CommentCard.pending-draft")) == 0
            )
        )

        assert diff_view._virtual_content_height == collapsed_virtual_height
        assert diff_view._line_top_offsets[next_line] == collapsed_next_line_top
        assert int(diff_view.max_scroll_y) == collapsed_max_scroll_y

        diff_view.scroll_to(y=0, animate=False)
        await wait_until(
            lambda: (
                diff_view._virt.window_start == 0
                and len(diff_view.query("CommentCard.pending-draft")) == 1
            )
        )

        remounted_draft = diff_view.query_one("CommentCard.pending-draft", CommentCard)
        assert remounted_draft.collapsed is True
        assert diff_view._virtual_content_height == collapsed_virtual_height
        assert diff_view._line_top_offsets[next_line] == collapsed_next_line_top
        assert int(diff_view.max_scroll_y) == collapsed_max_scroll_y


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
async def test_inline_comments_do_not_fill_wide_unified_view() -> None:
    patch = """@@ -1,3 +1,3 @@
 line1
-old alpha
+new alpha
 line2"""
    store = PRStore()
    store.save_pending_inline_comment(
        "hello draft",
        path="test.py",
        line=2,
        side="RIGHT",
    )
    store.state.review_threads = [_make_review_thread(root_id=101, side="RIGHT")]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 16)) as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        await pilot.pause()

        draft_widget = app.query_one("#pending-draft-1-right-0")
        thread_widget = app.query_one("#inline-thread-101")

        assert draft_widget.outer_size.width <= 96
        assert thread_widget.outer_size.width <= 96
        assert draft_widget.outer_size.width < diff_view.outer_size.width
        assert thread_widget.outer_size.width < diff_view.outer_size.width


@pytest.mark.asyncio
async def test_comments_follow_single_sided_hunk_inside_split_view() -> None:
    store = PRStore()
    store.save_pending_inline_comment(
        "hello draft",
        path="test.py",
        line=2,
        side="RIGHT",
    )
    store.state.review_threads = [_make_review_thread(root_id=201, side="RIGHT")]
    diff = FileDiff(
        filename="All files",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                file_path="test.py",
                file_additions=1,
                file_deletions=1,
                lines=[
                    DiffLine(
                        1,
                        1,
                        old_content="old alpha",
                        new_content="new alpha",
                        is_modified=True,
                    )
                ],
            ),
            DiffHunk(
                old_start=2,
                old_count=0,
                new_start=2,
                new_count=1,
                file_path="test.py",
                file_additions=1,
                file_deletions=0,
                lines=[DiffLine(None, 2, new_content="added beta", is_added=True)],
            ),
        ],
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 16)) as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("All files", diff)
        await pilot.pause()
        await pilot.pause()

        code_widget = app.query_one("#line-1-new .code-content", Static)
        draft_widget = app.query_one("#pending-draft-1-right-0")
        draft_item = next(
            ancestor
            for ancestor in draft_widget.ancestors
            if isinstance(ancestor, ReviewThreadItem)
        )
        thread_widget = app.query_one("#inline-thread-201")

        assert diff_view.split is True
        assert draft_item.region.x == code_widget.region.x
        assert thread_widget.region.x == code_widget.region.x


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
async def test_open_inline_comment_editor_on_line_starts_new_draft() -> None:
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
        draft = app.query_one("#pending-draft-1-right-0")
        body = editor.query_one("#comment-editor-body", TextArea)

        assert body.text == ""
        assert diff_view.inline_comment_draft_index() is None
        assert editor.region.y > draft.region.y


@pytest.mark.asyncio
async def test_open_inline_comment_editor_prefills_selected_draft() -> None:
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
        diff_view._comment_cursor_index = 1
        diff_view.focus()
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()

        editor = app.query_one("#diff-inline-comment-editor")
        body = editor.query_one("#comment-editor-body", TextArea)

        assert body.text == "hello draft"
        assert diff_view.inline_comment_draft_index() == 0
