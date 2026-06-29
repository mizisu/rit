from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine
from rit.state.models import (
    NodeList,
    PendingReviewComment,
    PRComment,
    PRReview,
    ReviewState,
    ReviewThread,
)
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


def test_build_comment_map_skips_sort_when_no_comment_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        state = SimpleNamespace(review_threads=[])

        def get_pending_file_comments(self, _filename: str) -> list[object]:
            return []

    view = SimpleNamespace(
        store=Store(),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )
    monkeypatch.setattr(
        _comments,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty comment maps should not sort")
        ),
        raising=False,
    )

    _comments.build_comment_map(view)

    assert view._comment_line_indices == []


def test_build_comment_map_skips_sort_for_single_comment_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        state = SimpleNamespace(review_threads=[])

    draft = PendingReviewComment(path="test.py", line=7)
    view = SimpleNamespace(
        store=Store(),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )
    monkeypatch.setattr(
        _comments,
        "_pending_comments_for_current_diff",
        lambda *_args: [draft],
    )
    monkeypatch.setattr(_comments, "_resolve_pending_line_index", lambda *_args: 4)
    monkeypatch.setattr(
        _comments,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single comment line should not sort")
        ),
        raising=False,
    )

    _comments.build_comment_map(view)

    assert view._comment_line_indices == [4]


def test_build_comment_map_skips_sort_for_single_thread_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread = _make_thread(line=7, original_line=7, side="RIGHT")
    view = SimpleNamespace(
        store=SimpleNamespace(state=SimpleNamespace(review_threads=[thread])),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )
    monkeypatch.setattr(_comments, "_resolve_line_index", lambda *_args, **_kwargs: 4)
    monkeypatch.setattr(
        _comments,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single thread line should not sort")
        ),
        raising=False,
    )

    _comments.build_comment_map(view)

    assert view._comment_line_indices == [4]


def test_build_comment_map_skips_pending_review_thread_already_rendered_as_draft() -> (
    None
):
    root = PRComment(
        id=5,
        body="comment",
        path="test.py",
        line=13,
        side="RIGHT",
        pull_request_review_id=91,
    )
    thread = ReviewThread(
        path="test.py",
        line=13,
        diff_side="RIGHT",
        comments_connection=NodeList(nodes=[root]),
    )
    draft = PendingReviewComment(
        body="comment",
        path="test.py",
        line=13,
        side="RIGHT",
        review_comment_id=5,
    )
    view = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(
                pending_review_id=91,
                pending_review_comments=[draft],
                review_threads=[thread],
            ),
            get_pending_file_comments=lambda _filename: [draft],
        ),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _line_index_by_new_number={13: 4},
        _line_index_by_old_number={},
        _line_index_by_file_new_number={("test.py", 13): 4},
        _line_index_by_file_old_number={},
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )

    _comments.build_comment_map(view)

    assert view._pending_comment_drafts_by_line == {4: [draft]}
    assert view._comment_threads_by_line == {}
    assert view._comment_line_indices == [4]


def test_build_comment_map_skips_pending_review_thread_when_server_reissues_id() -> (
    None
):
    root = PRComment(
        id=99,
        body="comment",
        path="test.py",
        line=13,
        side="RIGHT",
        pull_request_review_id=91,
    )
    thread = ReviewThread(
        path="test.py",
        line=13,
        diff_side="RIGHT",
        comments_connection=NodeList(nodes=[root]),
    )
    draft = PendingReviewComment(
        body="comment",
        path="test.py",
        line=13,
        side="RIGHT",
        review_comment_id=5,
    )
    view = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(
                pending_review_id=91,
                pending_review_comments=[draft],
                review_threads=[thread],
            ),
            get_pending_file_comments=lambda _filename: [draft],
        ),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _line_index_by_new_number={13: 4},
        _line_index_by_old_number={},
        _line_index_by_file_new_number={("test.py", 13): 4},
        _line_index_by_file_old_number={},
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )

    _comments.build_comment_map(view)

    assert view._pending_comment_drafts_by_line == {4: [draft]}
    assert view._comment_threads_by_line == {}
    assert view._comment_line_indices == [4]


def test_build_comment_map_skips_stale_pending_review_thread_after_resync() -> None:
    root = PRComment(
        id=5,
        body="comment",
        path="test.py",
        line=13,
        side="RIGHT",
        pull_request_review_id=91,
    )
    thread = ReviewThread(
        path="test.py",
        line=13,
        diff_side="RIGHT",
        comments_connection=NodeList(nodes=[root]),
    )
    draft = PendingReviewComment(
        body="comment",
        path="test.py",
        line=13,
        side="RIGHT",
        review_comment_id=100001,
    )
    view = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(
                pending_review_id=100,
                pending_review_comments=[draft],
                reviews=[PRReview(id=91, state=ReviewState.PENDING)],
                review_threads=[thread],
            ),
            get_pending_file_comments=lambda _filename: [draft],
        ),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _line_index_by_new_number={13: 4},
        _line_index_by_old_number={},
        _line_index_by_file_new_number={("test.py", 13): 4},
        _line_index_by_file_old_number={},
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )

    _comments.build_comment_map(view)

    assert view._pending_comment_drafts_by_line == {4: [draft]}
    assert view._comment_threads_by_line == {}
    assert view._comment_line_indices == [4]


def test_build_comment_map_hides_replaced_pending_thread_matching_remaining_draft() -> (
    None
):
    root = PRComment(
        id=91001,
        body="remaining draft",
        path="test.py",
        line=13,
        side="RIGHT",
        pull_request_review_id=91,
    )
    thread = ReviewThread(
        path="test.py",
        line=13,
        diff_side="RIGHT",
        comments_connection=NodeList(nodes=[root]),
    )
    draft = PendingReviewComment(
        body="remaining draft",
        path="test.py",
        line=13,
        side="RIGHT",
        review_comment_id=100001,
    )
    view = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(
                pending_review_id=100,
                pending_review_comments=[draft],
                reviews=[PRReview(id=100, state=ReviewState.PENDING)],
                review_threads=[thread],
                obsolete_pending_review_ids={91},
            ),
            get_pending_file_comments=lambda _filename: [draft],
        ),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _line_index_by_new_number={13: 4},
        _line_index_by_old_number={},
        _line_index_by_file_new_number={("test.py", 13): 4},
        _line_index_by_file_old_number={},
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )

    _comments.build_comment_map(view)

    assert view._pending_comment_drafts_by_line == {4: [draft]}
    assert view._comment_threads_by_line == {}
    assert view._comment_line_indices == [4]


def test_build_comment_map_keeps_submitted_thread_matching_draft_content() -> None:
    root = PRComment(
        id=91001,
        body="same text",
        path="test.py",
        line=13,
        side="RIGHT",
        pull_request_review_id=91,
    )
    thread = ReviewThread(
        path="test.py",
        line=13,
        diff_side="RIGHT",
        comments_connection=NodeList(nodes=[root]),
    )
    draft = PendingReviewComment(
        body="same text",
        path="test.py",
        line=13,
        side="RIGHT",
        review_comment_id=100001,
    )
    view = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(
                pending_review_id=100,
                pending_review_comments=[draft],
                reviews=[
                    PRReview(id=91, state=ReviewState.COMMENTED),
                    PRReview(id=100, state=ReviewState.PENDING),
                ],
                review_threads=[thread],
            ),
            get_pending_file_comments=lambda _filename: [draft],
        ),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _line_index_by_new_number={13: 4},
        _line_index_by_old_number={},
        _line_index_by_file_new_number={("test.py", 13): 4},
        _line_index_by_file_old_number={},
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )

    _comments.build_comment_map(view)

    assert view._pending_comment_drafts_by_line == {4: [draft]}
    assert view._comment_threads_by_line == {4: [thread]}
    assert view._comment_line_indices == [4]


def test_build_comment_map_hides_pending_review_thread_after_local_delete() -> None:
    root = PRComment(
        id=5,
        body="deleted draft",
        path="test.py",
        line=13,
        side="RIGHT",
        pull_request_review_id=91,
    )
    thread = ReviewThread(
        path="test.py",
        line=13,
        diff_side="RIGHT",
        comments_connection=NodeList(nodes=[root]),
    )
    view = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(
                pending_review_id=91,
                pending_review_comments=[],
                reviews=[PRReview(id=91, state=ReviewState.PENDING)],
                review_threads=[thread],
            ),
            get_pending_file_comments=lambda _filename: [],
        ),
        current_file="test.py",
        _diff_file_paths=frozenset({"test.py"}),
        _all_lines=[],
        _line_index_by_new_number={13: 4},
        _line_index_by_old_number={},
        _line_index_by_file_new_number={("test.py", 13): 4},
        _line_index_by_file_old_number={},
        _comment_threads_by_line={},
        _comment_line_indices=[],
        _comment_widgets_by_line={},
        _comment_layout_widgets_by_line={},
        _comment_side_by_line={},
        _pending_comment_drafts_by_line={},
        _pending_comment_widgets_by_line={},
        _pending_comment_layout_widgets_by_line={},
    )

    _comments.build_comment_map(view)

    assert view._pending_comment_drafts_by_line == {}
    assert view._comment_threads_by_line == {}
    assert view._comment_line_indices == []


def test_pending_comments_for_current_diff_skips_empty_state_scan() -> None:
    class EmptyDrafts(list):
        def __iter__(self):
            raise AssertionError("empty pending drafts should not be scanned")

    drafts = EmptyDrafts()
    view = SimpleNamespace(
        store=SimpleNamespace(
            state=SimpleNamespace(pending_review_comments=drafts),
        ),
    )

    assert (
        _comments._pending_comments_for_current_diff(
            view,
            frozenset({"a.py", "b.py"}),
        )
        == ()
    )


def test_comment_widgets_in_order_skips_list_copy_for_empty_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = SimpleNamespace(
        _pending_comment_widgets_by_line={},
        _comment_widgets_by_line={},
    )
    monkeypatch.setattr(
        _comments,
        "list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty comment lines should not copy widget lists")
        ),
        raising=False,
    )

    assert _comments.comment_widgets_in_order(view, 7) == []


def test_comment_widgets_in_order_reuses_single_widget_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_widgets = [object()]
    comment_widgets = [object()]

    monkeypatch.setattr(
        _comments,
        "list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single comment widget groups should not be copied")
        ),
        raising=False,
    )

    pending_only_view = SimpleNamespace(
        _pending_comment_widgets_by_line={7: pending_widgets},
        _comment_widgets_by_line={},
    )
    comment_only_view = SimpleNamespace(
        _pending_comment_widgets_by_line={},
        _comment_widgets_by_line={7: comment_widgets},
    )

    assert _comments.comment_widgets_in_order(pending_only_view, 7) is pending_widgets
    assert _comments.comment_widgets_in_order(comment_only_view, 7) is comment_widgets


def test_total_comments_at_line_skips_default_list_allocation() -> None:
    class NoDefaultDict(dict):
        def get(self, key: object, default: object = None) -> object:
            if default is not None:
                raise AssertionError(
                    "empty comment counts should not allocate defaults"
                )
            return super().get(key)

    view = SimpleNamespace(
        _pending_comment_widgets_by_line=NoDefaultDict(),
        _comment_widgets_by_line=NoDefaultDict(),
    )

    assert _comments.total_comments_at_line(view, 7) == 0


def test_active_thread_skips_default_list_allocation() -> None:
    class NoDefaultDict(dict):
        def get(self, key: object, default: object = None) -> object:
            if default is not None:
                raise AssertionError("empty active-thread lookup should not allocate")
            return super().get(key)

    view = SimpleNamespace(
        _comment_cursor_index=1,
        _pending_comment_drafts_by_line=NoDefaultDict(),
        _comment_threads_by_line=NoDefaultDict(),
    )

    assert _comments.active_thread(view, 7) is None


def test_active_pending_draft_skips_default_list_allocation() -> None:
    class NoDefaultDict(dict):
        def get(self, key: object, default: object = None) -> object:
            if default is not None:
                raise AssertionError("empty pending-draft lookup should not allocate")
            return super().get(key)

    view = SimpleNamespace(
        _comment_cursor_index=1,
        _pending_comment_drafts_by_line=NoDefaultDict(),
    )

    assert _comments.active_pending_draft(view, 7) is None


def test_pending_draft_widget_builds_side_id_without_lower_call() -> None:
    class Side(str):
        def lower(self) -> str:
            raise AssertionError("pending draft side is already a fixed literal")

    draft = PendingReviewComment.model_construct(
        body="note",
        path="test.py",
        line=7,
        side=Side("LEFT"),
        is_diff_line=True,
    )

    widget = _comments._build_pending_draft_widget(draft, line_index=3, index=0)

    assert widget.id == "pending-draft-3-left-0"
    assert widget._header == "test.py:7 (pending)"


def test_nearest_line_index_in_hunk_scans_lines_once() -> None:
    class SinglePassLines(list[DiffLine]):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("nearest hunk line lookup should scan once")
            return super().__iter__()

    lines = SinglePassLines(
        [
            DiffLine(10, 10, old_content="same", new_content="same", line_index=0),
            DiffLine(
                11,
                11,
                old_content="old",
                new_content="new",
                is_modified=True,
                line_index=1,
            ),
            DiffLine(
                12,
                12,
                old_content="older",
                new_content="newer",
                is_modified=True,
                line_index=2,
            ),
        ]
    )
    hunk = DiffHunk(old_start=10, old_count=3, new_start=10, new_count=3, lines=lines)

    assert _comments._nearest_line_index_in_hunk(hunk, "new", 12) == 2


@pytest.mark.asyncio
async def test_refresh_thread_metadata_updates_inline_thread_without_rerender() -> None:
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

        line_index = diff_view._line_index_by_new_number[2]
        widget = diff_view._comment_widgets_by_line[line_index][0]
        store.state.review_threads = [
            thread.model_copy(update={"id": "thread-1", "is_resolved": True})
        ]

        diff_view.refresh_thread_metadata()

        assert diff_view._comment_widgets_by_line[line_index][0] is widget
        assert diff_view._comment_threads_by_line[line_index][0].id == "thread-1"
        assert getattr(widget, "is_resolved") is True


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
async def test_split_comment_jump_uses_thread_diff_side_when_graphql_comment_has_no_side() -> (
    None
):
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
