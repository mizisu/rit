"""Tests for cursor navigation through inline comments and pending drafts."""

import pytest
from textual.app import App, ComposeResult

from rit.core.diff import parse_patch
from rit.state.models import PRComment, ReviewThread
from rit.state.store import PRStore
from rit.ui.widgets.diff_view import DiffView
from rit.ui.widgets.diff_visual import LineContent
from rit.ui.widgets.review_thread_card import ReviewThreadItem


def _make_review_thread(
    *, root_id: int, line: int, body: str = "comment"
) -> ReviewThread:
    root = PRComment.model_validate(
        {
            "databaseId": root_id,
            "body": body,
            "path": "test.py",
            "line": line,
            "originalLine": line,
            "side": "RIGHT",
        }
    )
    return ReviewThread.model_validate(
        {
            "path": "test.py",
            "line": line,
            "originalLine": line,
            "comments": {"nodes": [root]},
        }
    )


def _block_row_content_bounds(diff_view: DiffView, line_index: int) -> tuple[int, int]:
    block = diff_view._unified_blocks_by_line[line_index]
    row_offset = list(block.line_indices).index(line_index)
    top = int(diff_view.scroll_y) + (
        block.region.y - diff_view.scrollable_content_region.y
    ) + row_offset
    return top, top + 1


@pytest.mark.asyncio
async def test_landing_on_diff_line_does_not_select_comment() -> None:
    """When cursor lands on a diff line, none of its comments should be highlighted."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
    store = PRStore()
    store.save_pending_inline_comment(
        "draft body",
        path="test.py",
        line=2,
        side="RIGHT",
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 1
        await pilot.pause()
        await pilot.pause()

        assert diff_view._comment_cursor_index == 0
        draft = app.query_one("#pending-draft-1-right-0")
        assert "--cursor-line" not in draft.classes


@pytest.mark.asyncio
async def test_j_after_diff_line_selects_first_comment_then_advances() -> None:
    """Pressing j on the last diff row of a line should select comments before next line."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
    store = PRStore()
    store.save_pending_inline_comment(
        "first draft",
        path="test.py",
        line=1,
        side="RIGHT",
    )
    store.state.review_threads.append(_make_review_thread(root_id=1, line=1))
    store.state.review_threads.append(_make_review_thread(root_id=2, line=1))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 0
        await pilot.pause()
        assert diff_view._comment_cursor_index == 0
        assert diff_view.cursor_line == 0

        await pilot.press("j")
        await pilot.pause()
        assert diff_view.cursor_line == 0
        assert diff_view._comment_cursor_index == 1
        draft = app.query_one("#pending-draft-0-right-0")
        assert "--cursor-line" in draft.classes
        thread1 = app.query_one("#inline-thread-1")
        assert "--cursor-line" not in thread1.classes

        await pilot.press("j")
        await pilot.pause()
        assert diff_view.cursor_line == 0
        assert diff_view._comment_cursor_index == 2
        assert "--cursor-line" not in draft.classes
        assert "--cursor-line" in thread1.classes

        await pilot.press("j")
        await pilot.pause()
        assert diff_view.cursor_line == 0
        assert diff_view._comment_cursor_index == 3
        thread2 = app.query_one("#inline-thread-2")
        assert "--cursor-line" not in thread1.classes
        assert "--cursor-line" in thread2.classes

        await pilot.press("j")
        await pilot.pause()
        assert diff_view.cursor_line == 1
        assert diff_view._comment_cursor_index == 0
        assert "--cursor-line" not in thread2.classes


@pytest.mark.asyncio
async def test_k_steps_back_through_comments_then_to_previous_line() -> None:
    """Pressing k should reverse the comment-step navigation."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
    store = PRStore()
    store.save_pending_inline_comment(
        "draft body",
        path="test.py",
        line=0 + 1,
        side="RIGHT",
    )
    store.state.review_threads.append(_make_review_thread(root_id=10, line=1))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 1
        await pilot.pause()

        await pilot.press("k")
        await pilot.pause()
        assert diff_view.cursor_line == 0
        assert diff_view._comment_cursor_index == 2
        thread = app.query_one("#inline-thread-10")
        assert "--cursor-line" in thread.classes

        await pilot.press("k")
        await pilot.pause()
        assert diff_view.cursor_line == 0
        assert diff_view._comment_cursor_index == 1
        draft = app.query_one("#pending-draft-0-right-0")
        assert "--cursor-line" in draft.classes

        await pilot.press("k")
        await pilot.pause()
        assert diff_view.cursor_line == 0
        assert diff_view._comment_cursor_index == 0
        assert "--cursor-line" not in draft.classes


@pytest.mark.asyncio
async def test_enter_does_not_toggle_when_no_comment_selected() -> None:
    """Pressing Enter on a diff line with comments shouldn't toggle the first comment."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
    store = PRStore()
    store.state.review_threads.append(_make_review_thread(root_id=42, line=1))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 0
        await pilot.pause()
        assert diff_view._comment_cursor_index == 0

        thread_widget = app.query_one("#inline-thread-42", ReviewThreadItem)
        initial_collapsed = thread_widget.collapsed

        await pilot.press("enter")
        await pilot.pause()

        assert thread_widget.collapsed == initial_collapsed


@pytest.mark.asyncio
async def test_enter_toggles_only_active_comment() -> None:
    """Enter should toggle the currently selected (highlighted) comment."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
    store = PRStore()
    store.state.review_threads.append(_make_review_thread(root_id=100, line=1))
    store.state.review_threads.append(_make_review_thread(root_id=200, line=1))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 0
        await pilot.pause()

        thread1 = app.query_one("#inline-thread-100", ReviewThreadItem)
        thread2 = app.query_one("#inline-thread-200", ReviewThreadItem)
        thread1_initial = thread1.collapsed
        thread2_initial = thread2.collapsed

        await pilot.press("j")
        await pilot.pause()
        assert diff_view._comment_cursor_index == 1

        await pilot.press("j")
        await pilot.pause()
        assert diff_view._comment_cursor_index == 2

        await pilot.press("enter")
        await pilot.pause()

        assert thread1.collapsed == thread1_initial
        assert thread2.collapsed != thread2_initial


@pytest.mark.asyncio
async def test_diff_line_cursor_block_swaps_with_comment_highlight() -> None:
    """`-cursor` on the parent diff line should hide while a comment is selected."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
    store = PRStore()
    store.state.review_threads.append(_make_review_thread(root_id=999, line=2))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 1
        await pilot.pause()

        code_widgets = diff_view._get_code_widgets(1)
        assert all("-cursor" in w.classes for w in code_widgets)
        thread = app.query_one("#inline-thread-999")
        assert "--cursor-line" not in thread.classes

        await pilot.press("j")
        await pilot.pause()
        code_widgets = diff_view._get_code_widgets(1)
        assert all("-cursor" not in w.classes for w in code_widgets)
        assert "--cursor-line" in thread.classes

        await pilot.press("k")
        await pilot.pause()
        code_widgets = diff_view._get_code_widgets(1)
        assert all("-cursor" in w.classes for w in code_widgets)
        assert "--cursor-line" not in thread.classes


@pytest.mark.asyncio
async def test_cursor_line_change_clears_comment_selection() -> None:
    """Switching diff lines should reset the comment cursor index to 0."""

    patch = "@@ -1,3 +1,3 @@\n line1\n line2\n line3"
    store = PRStore()
    store.state.review_threads.append(_make_review_thread(root_id=20, line=1))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 0
        await pilot.pause()

        await pilot.press("j")
        await pilot.pause()
        assert diff_view._comment_cursor_index == 1

        diff_view.cursor_line = 2
        await pilot.pause()
        assert diff_view._comment_cursor_index == 0
        thread = app.query_one("#inline-thread-20")
        assert "--cursor-line" not in thread.classes


@pytest.mark.asyncio
async def test_cursor_line_with_comment_is_highlighted_after_grouped_block() -> None:
    """Moving from a block row to a comment line should repaint the comment line."""

    patch = "@@ -1,130 +1,130 @@\n" + "\n".join(f" line{i}" for i in range(1, 131))
    store = PRStore()
    store.state.review_threads.append(_make_review_thread(root_id=31, line=31))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 29
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()

        assert diff_view.cursor_line == 30
        assert diff_view._comment_cursor_index == 0
        code_widgets = diff_view._get_code_widgets(30)
        assert code_widgets
        assert all("-cursor" in widget.classes for widget in code_widgets)


@pytest.mark.asyncio
async def test_cursor_line_after_comment_is_highlighted_inside_grouped_block() -> None:
    """The next line after a comment should show row cursor styling in block rendering."""

    patch = "@@ -1,130 +1,130 @@\n" + "\n".join(f" line{i}" for i in range(1, 131))
    store = PRStore()
    store.state.review_threads.append(_make_review_thread(root_id=30, line=31))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 30
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        assert diff_view._comment_cursor_index == 1

        await pilot.press("j")
        await pilot.pause()

        assert diff_view.cursor_line == 31
        assert diff_view._comment_cursor_index == 0
        block = diff_view._unified_blocks_by_line[31]
        visual = block._code._render()
        assert isinstance(visual, LineContent)
        block_row = list(block.line_indices).index(31)
        assert visual.line_styles[block_row] == "on $primary 25%"


@pytest.mark.asyncio
async def test_cursor_line_after_many_comments_stays_visible_inside_grouped_block() -> (
    None
):
    """Leaving a tall comment stack should reveal the next grouped-block row."""

    patch = "@@ -1,180 +1,180 @@\n" + "\n".join(
        f" line{i}" for i in range(1, 181)
    )
    body = "\n".join(f"comment line {i}" for i in range(20))
    store = PRStore()
    for root_id in range(100, 106):
        store.state.review_threads.append(
            _make_review_thread(root_id=root_id, line=31, body=body)
        )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(120, 10)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_line = 30
        await pilot.pause()
        await pilot.pause()

        row = diff_view._current_row()
        assert row is not None
        top, _ = diff_view._row_vertical_bounds(row) or (None, None)
        assert top is not None
        diff_view.scroll_y = max(0, top - 2)
        await pilot.pause()
        await pilot.pause()

        for _ in range(7):
            await pilot.press("j")
            await pilot.pause()

        assert diff_view.cursor_line == 31
        assert diff_view._comment_cursor_index == 0

        top, bottom = _block_row_content_bounds(diff_view, 31)
        viewport_top = int(diff_view.scroll_y)
        viewport_bottom = viewport_top + diff_view.scrollable_content_region.height
        assert top >= viewport_top
        assert bottom <= viewport_bottom
