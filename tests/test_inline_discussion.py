import asyncio
from typing import Literal

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, TextArea

from rit.core.diff import parse_patch
from rit.state.models import PR, NodeList, PendingReviewComment, PRComment, ReviewThread
from rit.state.store import PRStore
from rit.ui.widgets import diff_comments, diff_virtual
from rit.ui.widgets.comment_editor import CommentEditorBuffer, InlineCommentEditor
from rit.ui.widgets.diff_composer import ComposerSession
from rit.ui.widgets.diff_view import DiffView
from rit.ui.widgets.inline_review_thread import InlineReviewThread
from tests.conftest import wait_until


@pytest.mark.parametrize(
    ("reply_count", "history_count"), [(0, 0), (2, 0), (5, 0), (6, 4), (9, 7)]
)
@pytest.mark.parametrize("expanded", [False, True])
@pytest.mark.parametrize("with_draft", [False, True])
def test_reply_history_threshold_matches_visible_entries_and_virtual_height(
    reply_count: int, history_count: int, expanded: bool, with_draft: bool
) -> None:
    comments = [
        PRComment(id=index + 1, body="Message", in_reply_to_id=1 if index else None)
        for index in range(reply_count + 1)
    ]
    thread = ReviewThread(comments_connection=NodeList(nodes=comments))
    drafts = (
        [PendingReviewComment(body="Pending", path="test.py", line=1, reply_to_id=1)]
        if with_draft
        else []
    )
    view = DiffView()
    view._pending_replies_by_root = {1: drafts}
    if expanded:
        view._discussion_state.history_roots.add(1)
    widget = InlineReviewThread(thread, drafts, view._discussion_state, title="Thread")

    assert widget.history_count(len(comments)) == history_count
    expected = (
        comments
        if expanded or not history_count
        else [comments[0], *comments[-2:]]
    )
    assert [
        comment
        for index in range(widget.comment_count)
        if (comment := widget.comment_at(index)) is not None
    ] == expected
    assert widget.comment_count == len(expected) + len(drafts) + bool(history_count)
    assert (
        widget._history_card in widget._review_thread_card._comment_cards
    ) == bool(history_count)
    if drafts:
        assert widget.pending_at(widget.comment_count - 1) is drafts[0]
    if not history_count:
        assert not widget.toggle_history(widget._history_card)
    assert diff_comments.thread_height(view, thread) == (
        diff_comments.COLLAPSED_THREAD_HEIGHT
        + 5 * (len(expected) + len(drafts))
        + bool(history_count)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_discussion_disclosure_reply_drafts_and_virtualized_input(
    mode: Literal["unified", "split"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    comments = [
        PRComment(
            id=index + 1,
            body=f"Message {index}",
            path="test.py",
            line=1,
            side="RIGHT",
            in_reply_to_id=1 if index else None,
        )
        for index in range(7)
    ]
    first = ReviewThread(
        id="thread-one",
        path="test.py",
        line=1,
        diff_side="RIGHT",
        comments_connection=NodeList(nodes=comments),
    )
    second = ReviewThread(
        id="thread-two",
        path="test.py",
        line=1,
        diff_side="RIGHT",
        comments_connection=NodeList(
            nodes=[
                PRComment(
                    id=10,
                    path="test.py",
                    line=1,
                    side="RIGHT",
                    body="Another topic",
                )
            ]
        ),
    )
    draft = PendingReviewComment(
        body="Pending reply",
        path="test.py",
        line=1,
        side="RIGHT",
        reply_to_id=1,
        reply_thread_id=first.id,
    )
    store.state.review_threads = [first, second]
    store.state.thread_cache = {1: first, 10: second}
    store.state.pending_review.comments = [draft]

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(store, mode=mode)

    app = TestApp()
    source = parse_patch(
        "@@ -1,400 +1,400 @@\n" + "\n".join(f" line {i}" for i in range(400)),
        "test.py",
    )
    async with app.run_test(size=(140, 36)) as pilot:
        view = app.query_one(DiffView)
        monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", 1)
        monkeypatch.setattr(view, "VIRTUAL_WINDOW_RADIUS", 12)
        await view.show_diff("test.py", source)
        await pilot.pause()
        view.focus()
        threads = list(view.query(InlineReviewThread))
        assert len(threads) == 2 and all(not thread.collapsed for thread in threads)
        assert all(thread.region.height > 3 for thread in threads)
        assert all(thread.styles.border_top[0] == "solid" for thread in threads)
        assert all("test.py:1" in thread.title for thread in threads)
        assert view._pending_comment_drafts_by_line == {}

        initial_scroll = view.scroll_offset
        await pilot.click(threads[1]._title)
        assert view.scroll_offset == initial_scroll
        assert view.selected_reply_comment() == second.root_comment
        assert not threads[0].collapsed and threads[1].collapsed
        assert view.has_focus
        await pilot.click(threads[0]._title)
        assert all(thread.collapsed for thread in threads)
        await pilot.click(threads[0]._title)
        assert view.scroll_offset == initial_scroll
        assert not threads[0].collapsed and threads[1].collapsed
        assert view.active_review_comment() == comments[0]
        assert threads[0].comment_count == 5
        history = threads[0]._history_card
        normal_background = history.styles.background
        await pilot.press("j")
        assert history.has_class("--cursor-line")
        assert history.styles.border_left[0] == "solid"
        assert history.styles.background != normal_background
        focused_background = history.styles.background
        history_width = history.region.width
        await pilot.press("enter")
        assert 1 in view._discussion_state.history_roots
        assert threads[0].comment_count == 9
        assert history.has_class("--cursor-line")
        assert history.styles.background == focused_background
        assert history.region.width == history_width
        assert "Enter" in history._header
        await pilot.press("j")
        assert view.active_review_comment() == comments[1]
        view._comment_cursor_index = threads[0].comment_count
        assert view.active_pending_draft_index() == 0

        assert await view.open_reply_comment_editor(draft_index=0)
        editor = view.query_one(InlineCommentEditor)
        body = editor.query_one(TextArea)
        await wait_until(lambda: body.has_focus)
        assert any(
            isinstance(parent, InlineReviewThread) for parent in editor.ancestors
        )
        assert body.text == draft.body
        assert len(app.screen_stack) == 1
        body.text = "Unsent edit"
        body.move_cursor((0, 4))
        await pilot.press("escape")
        await wait_until(lambda: not view.query(InlineCommentEditor))
        assert view.has_focus
        assert view._composer.session is not None
        assert view._composer.session.suspended
        assert view._composer.session.buffer.text == "Unsent edit"
        assert view._composer.estimate_height() == 0

        view._move_cursor(line=4)
        assert view.cursor_line == 4 and not editor.is_attached
        assert view._composer.resume()
        await wait_until(
            lambda: (
                any(area.has_focus for area in view.query(TextArea))
                and view.active_pending_draft_index() == 0
            )
        )
        body = view.query_one(TextArea)
        assert view.cursor_line == 0
        assert view.active_pending_draft_index() == 0
        assert body.cursor_location == (0, 4)
        await pilot.press("escape")

        diff_virtual._set_virtual_window_around(view, 220)
        view._virt.render_pending = True
        await diff_virtual._run_virtual_window_render_for_request(
            view, view._render_request_token
        )
        await pilot.pause()
        assert view._composer.widget is None
        assert view._composer.resume()
        await wait_until(
            lambda: (
                view._composer.widget is not None and view._composer.widget.is_mounted
            ),
            timeout=2,
        )
        restored = view.query_one(InlineCommentEditor).query_one(TextArea)
        await wait_until(lambda: restored.has_focus)
        assert restored.text == "Unsent edit"
        assert restored.cursor_location == (0, 4)

        view.mode = "split" if mode == "unified" else "unified"
        await pilot.pause()
        restored = view.query_one(InlineCommentEditor).query_one(TextArea)
        assert restored.text == "Unsent edit"
        assert restored.cursor_location == (0, 4)
        view.query_one("#comment-editor-post", Button).focus()
        view._composer.set_submitting(True)
        assert restored.has_focus
        buttons = view.query_one(InlineCommentEditor).query(Button)
        assert all(button.disabled for button in buttons)
        assert restored.read_only
        await pilot.press("escape")
        assert restored.is_attached and restored.has_focus
        view._composer.set_submitting(False, "Network error. Retry.")
        assert not restored.read_only
        assert view.query_one(".comment-editor-status").has_class("-error")
        await pilot.press("escape")
        await wait_until(lambda: not view.query(InlineCommentEditor))
        assert view._composer.resume()
        await wait_until(lambda: any(area.has_focus for area in view.query(TextArea)))
        assert view.query_one(TextArea).text == "Unsent edit"
        assert view.query_one(".comment-editor-status").has_class("-error")
        view.focus()
        await pilot.press("escape")
        await wait_until(lambda: not view.query(InlineCommentEditor))
        assert view._composer.session is not None
        assert view._composer.session.buffer.text == "Unsent edit"
        await view.close_reply_comment_editor()
        await wait_until(lambda: view.has_focus)
        assert not view.query(InlineCommentEditor)
        assert draft.body == "Pending reply"

        threads = list(view.query(InlineReviewThread))
        view._comment_cursor_index = threads[0].comment_count + 1
        await pilot.press("enter")
        assert not threads[0].collapsed and not threads[1].collapsed


@pytest.mark.asyncio
async def test_inline_buffer_survives_file_navigation_and_cancel_is_explicit() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified")

    app = TestApp()
    first = parse_patch("@@ -0,0 +1 @@\n+first", "one.py")
    second = parse_patch("@@ -0,0 +1 @@\n+second", "two.py")
    async with app.run_test() as pilot:
        view = app.query_one(DiffView)
        await view.show_diff("one.py", first)
        assert await view.open_inline_comment_editor()
        await pilot.press("escape")
        await wait_until(lambda: view._composer.session is None)
        assert not view.query(InlineCommentEditor)
        assert view.inline_comment_target() is None
        await view.show_diff("two.py", second)
        assert await view.open_inline_comment_editor()
        assert view.inline_comment_target() == ("two.py", 1, "RIGHT")
        await pilot.press("escape")
        await wait_until(lambda: view._composer.session is None)
        await view.show_diff("one.py", first)
        assert await view.open_inline_comment_editor()
        body = view.query_one(TextArea)
        await wait_until(lambda: body.has_focus)
        body.text = "Keep this text"
        button = view.query_one("#comment-editor-queue", Button)
        await wait_until(lambda: not button.disabled)
        button.focus()
        await pilot.press("escape")
        await wait_until(lambda: not view.query(InlineCommentEditor))
        assert view.has_focus
        await view.show_diff("two.py", second)
        assert not view.query(InlineCommentEditor)
        assert not await view.open_inline_comment_editor()
        assert view.inline_comment_target() == ("one.py", 1, "RIGHT")
        await view.show_diff("one.py", first)
        await pilot.pause()
        assert not view.query(InlineCommentEditor)
        assert view._composer.resume()
        await wait_until(lambda: any(area.has_focus for area in view.query(TextArea)))
        assert view.query_one(TextArea).text == "Keep this text"
        await pilot.resize_terminal(80, 12)
        await pilot.pause()
        editor = view.query_one(InlineCommentEditor)
        editor.query_one(TextArea).text = "\n".join(f"Line {i}" for i in range(10))
        cancel = editor.query_one("#comment-editor-cancel", Button)
        await wait_until(
            lambda: (
                editor.region.height > 8
                and cancel.region.bottom <= view.scrollable_content_region.bottom
                and editor.query_one(TextArea).region.y
                >= view.scrollable_content_region.y
            )
        )
        await asyncio.gather(
            view.close_inline_comment_editor(), view.close_inline_comment_editor()
        )
        await wait_until(lambda: view.has_focus)
        assert view._composer.session is None


def test_composer_validates_retained_draft_and_comparison_before_submitting() -> None:
    store = PRStore()
    store.state.pr = PR(base_sha="base", head_sha="head")
    draft = PendingReviewComment(body="before", path="one.py", line=1, side="RIGHT")
    other = PendingReviewComment(body="other", path="two.py", line=1, side="RIGHT")
    store.state.pending_review.comments = [draft, other]
    view = DiffView(store)
    composer = view._composer
    composer.session = ComposerSession(
        kind="inline",
        title="Edit comment",
        context="one.py:1",
        buffer=CommentEditorBuffer(text="unsent update"),
        draft=draft,
        comparison_refs=("base", "head"),
    )
    store.state.pending_review.comments = [draft.model_copy(), draft]
    assert composer.begin_submit()
    assert view.inline_comment_draft_index() == 1
    composer.set_submitting(False)
    store.state.pending_review.comments = [other, draft.model_copy()]
    assert composer.begin_submit()
    assert view.inline_comment_draft_index() == 1
    assert not composer.begin_submit()
    composer.set_submitting(False)
    store.state.pending_review.comments = [other]
    assert not composer.begin_submit()
    assert "no longer exists" in composer.session.buffer.error
    store.state.pending_review.comments = [draft]
    store.state.pr.head_sha = "new-head"
    assert not composer.begin_submit()
    assert "PR changed" in composer.session.buffer.error
    assert composer.session.buffer.text == "unsent update"
