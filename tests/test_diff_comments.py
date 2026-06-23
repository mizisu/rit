from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult

from rit.core.diff import parse_patch
from rit.state.models import NodeList, PRComment, ReviewThread
from rit.ui.messages import Flash
from rit.ui.widgets import diff_comments as _comments
from rit.ui.widgets.diff_view import DiffView


def _make_thread(
    *,
    line: int | None,
    original_line: int | None,
    side: str,
    diff_hunk: str = "",
    thread_diff_side: str = "",
) -> ReviewThread:
    root = PRComment(
        id=1,
        body="comment",
        path="test.py",
        line=line,
        original_line=original_line,
        side=side,
        diff_hunk=diff_hunk,
    )
    return ReviewThread(
        path="test.py",
        line=line,
        original_line=original_line,
        diff_side=thread_diff_side,
        comments_connection=NodeList(nodes=[root]),
    )


def _make_view_for_patch(patch: str) -> SimpleNamespace:
    diff = parse_patch(patch, "test.py")
    line_index_by_new_number: dict[int, int] = {}
    line_index_by_old_number: dict[int, int] = {}

    line_index = 0
    for hunk in diff.hunks:
        for line in hunk.lines:
            line.line_index = line_index
            if line.new_line_no is not None:
                line_index_by_new_number.setdefault(line.new_line_no, line_index)
            if line.old_line_no is not None:
                line_index_by_old_number.setdefault(line.old_line_no, line_index)
            line_index += 1

    return SimpleNamespace(
        _diff=diff,
        _line_index_by_new_number=line_index_by_new_number,
        _line_index_by_old_number=line_index_by_old_number,
    )


def test_resolve_line_index_prefers_left_side_mapping() -> None:
    view = SimpleNamespace(
        _line_index_by_new_number={20: 5},
        _line_index_by_old_number={10: 3},
    )
    comment = PRComment(
        body="comment",
        path="test.py",
        line=20,
        original_line=10,
        side="LEFT",
    )

    assert _comments._resolve_line_index(view, comment) == 3


def test_resolve_line_index_prefers_thread_diff_side_when_comment_side_absent() -> None:
    view = SimpleNamespace(
        _line_index_by_new_number={20: 5},
        _line_index_by_old_number={10: 3},
    )
    comment = PRComment(
        body="comment",
        path="test.py",
        line=20,
        original_line=10,
    )
    thread = ReviewThread(
        path="test.py",
        line=20,
        original_line=10,
        diff_side="LEFT",
        comments_connection=NodeList(nodes=[comment]),
    )

    assert _comments._resolve_line_index(view, comment, thread=thread) == 3
    assert _comments._comment_target_side(comment, thread=thread) == "old"


def test_inline_thread_widget_title_uses_anchor_line() -> None:
    thread = _make_thread(line=20, original_line=10, side="LEFT")

    widget = _comments._build_inline_thread_widget(thread)

    assert widget.title.endswith("test.py:10")
    assert widget.line == 10


def test_resolve_line_index_falls_back_to_diff_hunk_region() -> None:
    patch = """@@ -10,3 +10,3 @@
 line1
-old alpha
+new alpha
 line2"""
    view = _make_view_for_patch(patch)
    comment = PRComment(
        body="comment",
        path="test.py",
        line=None,
        original_line=None,
        side="RIGHT",
        diff_hunk=patch,
    )

    assert _comments._resolve_line_index(view, comment) == 1


@pytest.mark.asyncio
async def test_toggle_resolve_rolls_back_and_flashes_when_store_update_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = _make_thread(line=2, original_line=2, side="RIGHT")
    thread.id = "thread-1"
    updates: list[bool] = []

    class Store:
        async def resolve_thread(self, thread_id: str, root_id: int) -> bool:
            raise RuntimeError("mutation failed")

    class View:
        cursor_line = 0
        store = Store()
        _comment_threads_by_line: dict[int, list[ReviewThread]] = {}
        messages: list[Flash] = []

        def post_message(self, message: Flash) -> None:
            self.messages.append(message)

    view = View()
    monkeypatch.setattr(_comments, "active_thread", lambda *_args: thread)
    monkeypatch.setattr(
        _comments,
        "_update_thread_widget_resolved",
        lambda _view, _line, _thread, is_resolved: updates.append(is_resolved),
    )

    await _comments.toggle_resolve(view)

    assert updates == [True, False]
    assert view.messages[-1].content == "Error: mutation failed"
    assert view.messages[-1].style == "error"


@pytest.mark.asyncio
async def test_toggle_resolve_reraises_unexpected_success_flash_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = _make_thread(line=2, original_line=2, side="RIGHT")
    thread.id = "thread-1"
    updates: list[bool] = []

    class Store:
        async def resolve_thread(self, thread_id: str, root_id: int) -> bool:
            return True

    class View:
        cursor_line = 0
        store = Store()
        _comment_threads_by_line: dict[int, list[ReviewThread]] = {}

        def post_message(self, message: Flash) -> None:
            if message.style == "success":
                raise RuntimeError("flash dispatch failed")

    view = View()
    monkeypatch.setattr(_comments, "active_thread", lambda *_args: thread)
    monkeypatch.setattr(
        _comments,
        "_update_thread_widget_resolved",
        lambda _view, _line, _thread, is_resolved: updates.append(is_resolved),
    )

    with pytest.raises(RuntimeError, match="flash dispatch failed"):
        await _comments.toggle_resolve(view)

    assert updates == [True]


@pytest.mark.asyncio
async def test_unified_comment_jump_uses_new_side_anchor_for_right_comment() -> None:
    patch = """@@ -1,3 +1,3 @@
 line1
-old alpha
+new alpha
 line2"""
    thread = _make_thread(line=2, original_line=2, side="RIGHT")
    store = SimpleNamespace(state=SimpleNamespace(files=[], review_threads=[thread]))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("}")
        await pilot.pause()

        row = diff_view._current_row()
        assert row is not None
        assert diff_view.cursor_line == 1
        assert row.side == "new"


@pytest.mark.asyncio
async def test_split_comment_jump_sets_old_pane_for_left_comment() -> None:
    patch = """@@ -1,3 +1,3 @@
 line1
-old alpha
+new alpha
 line2"""
    thread = _make_thread(line=2, original_line=2, side="LEFT")
    store = SimpleNamespace(state=SimpleNamespace(files=[], review_threads=[thread]))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("}")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.active_pane == "old"


@pytest.mark.asyncio
async def test_split_comment_jump_uses_thread_diff_side_when_graphql_comment_has_no_side() -> None:
    patch = """@@ -1,3 +1,3 @@
 line1
-old alpha
+new alpha
 line2"""
    thread = _make_thread(
        line=2,
        original_line=2,
        side="",
        thread_diff_side="LEFT",
    )
    store = SimpleNamespace(state=SimpleNamespace(files=[], review_threads=[thread]))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("}")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.active_pane == "old"
