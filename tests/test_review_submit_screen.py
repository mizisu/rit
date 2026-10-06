import asyncio
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from textual.app import App
from textual.widgets import Button, OptionList, Static, TextArea

from rit.app import RitApp
from rit.state.models import PR, PendingReviewComment, PRReview, ReviewState
from rit.state.store import GitHubError, PRStore
from rit.ui.screens.review_submit import ReviewEvent, ReviewSubmitScreen
from rit.ui.widgets.comment_card import CommentCard
from tests.conftest import wait_until


@pytest.mark.asyncio
async def test_review_submit_screen_places_actions_below_body_in_requested_order() -> (
    None
):
    class TestApp(App):
        CSS_PATH = RitApp.CSS_PATH

        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen())

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        children = list(screen.query_one("#review-submit-dialog").children)
        options = screen.query_one("#review-submit-actions", OptionList)
        buttons = [
            screen.query_one("#review-submit-confirm", Button),
            screen.query_one("#review-submit-cancel", Button),
        ]

        assert screen.styles.background.a == 0
        assert screen.query_one("#review-submit-dialog").styles.background.a == 1
        assert app.screen_stack[0] in app._background_screens
        assert children[1].id == "review-submit-body"
        assert children[2].id == "review-submit-emoji-options"
        assert children[3].id == "review-submit-actions"
        assert children[4].id == "review-submit-buttons"
        assert [option.id for option in options.options] == [
            "COMMENT",
            "APPROVE",
            "REQUEST_CHANGES",
        ]
        assert options.region.height >= len(options.options)
        assert [str(button.label) for button in buttons] == [
            "Submit review  Ctrl+Enter",
            "Cancel  Esc",
        ]
        assert buttons[0].variant == "primary"

        actions = screen.query_one("#review-submit-buttons")
        for width in (44, 80):
            await pilot.resize_terminal(width, 32)
            await pilot.pause()

            assert all(
                actions.content_region.contains_region(button.region)
                for button in buttons
            )
            if width == 44:
                assert buttons[0].region.bottom <= buttons[1].region.y
            else:
                assert buttons[0].region.y == buttons[1].region.y


@pytest.mark.asyncio
async def test_review_submit_screen_shows_pending_draft_details() -> None:
    pending_comments = [
        PendingReviewComment(
            body="first draft line",
            path="src/app.py",
            line=7,
            side="RIGHT",
        ),
        PendingReviewComment(
            body="second draft line",
            path="tests/test_app.py",
            line=11,
            side="LEFT",
        ),
    ]

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen(pending_comments=pending_comments))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        children = list(screen.query_one("#review-submit-dialog").children)
        pending_list = screen.query_one("#review-submit-pending-list")
        first_item = screen.query_one("#review-submit-pending-item-0", CommentCard)
        second_item = screen.query_one("#review-submit-pending-item-1", CommentCard)
        plain_widgets = list(screen.query(".comment-body-plain"))

        assert children[4].id == "review-submit-pending"
        assert pending_list.region.height >= 8
        assert len(screen.query("CommentCard.review-submit-pending-item")) == 2
        assert str(first_item.query_one(".comment-header").render()) == (
            "src/app.py:7 • new side"
        )
        assert str(second_item.query_one(".comment-header").render()) == (
            "tests/test_app.py:11 • old side"
        )
        assert [
            str(
                getattr(
                    cast(Static, widget).content, "plain", cast(Static, widget).content
                )
            )
            for widget in plain_widgets
        ] == ["first draft line", "second draft line"]


@pytest.mark.asyncio
async def test_review_submit_screen_labels_file_level_comment_without_line() -> None:
    pending_comment = PendingReviewComment(
        body="whole file",
        path="src/app.py",
        line=0,
        side="RIGHT",
        subject_type="file",
    )

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen(pending_comments=[pending_comment]))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        header = app.screen.query_one(
            "#review-submit-pending-item-0 .comment-header", Static
        )
        assert str(header.render()) == "src/app.py • entire file"


@pytest.mark.asyncio
async def test_review_submit_screen_prefills_initial_body() -> None:
    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen(initial_body="saved summary"))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        textarea = screen.query_one("#review-submit-body", TextArea)

        assert textarea.text == "saved summary"


@pytest.mark.asyncio
async def test_review_submit_screen_selects_and_submits_emoji_shortcode() -> None:
    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: tuple[str, str] | None = None

        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen(), self._capture)

        def _capture(self, result: tuple[str, str] | None) -> None:
            self.result = result

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        textarea = screen.query_one("#review-submit-body", TextArea)
        textarea.text = "ship :roc"
        textarea.move_cursor((0, len("ship :roc")))
        await pilot.pause()

        options = screen.query_one("#review-submit-emoji-options", OptionList)
        highlighted = options.highlighted_option

        assert not options.has_class("-hidden")
        assert highlighted is not None
        assert highlighted.id == "rocket"

        await pilot.press("enter")
        await pilot.pause()

        assert textarea.text == "ship 🚀"

        await pilot.press("ctrl+enter")
        await pilot.pause()

        assert app.result == ("COMMENT", "ship 🚀")


@pytest.mark.asyncio
async def test_review_submit_screen_allows_empty_comment_when_pending_drafts_exist() -> (
    None
):
    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: tuple[str, str] | None = None

        def on_mount(self) -> None:
            self.push_screen(
                ReviewSubmitScreen(pending_comments_count=2), self._capture
            )

        def _capture(self, result: tuple[str, str] | None) -> None:
            self.result = result

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("ctrl+enter")
        await pilot.pause()

        assert app.result == ("COMMENT", "")


@pytest.mark.asyncio
async def test_review_submit_screen_requires_explicit_submit_after_action_choice() -> (
    None
):
    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: tuple[str, str] | None = None

        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen(initial_body="ship it"), self._capture)

        def _capture(self, result: tuple[str, str] | None) -> None:
            self.result = result

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("tab")
        await pilot.press("j")
        await pilot.press("enter")
        await pilot.pause()

        submit = app.screen.query_one("#review-submit-confirm", Button)
        assert app.result is None
        assert app.screen.focused is submit

        await pilot.press("enter")
        await pilot.pause()

        assert app.result == ("APPROVE", "ship it")


@pytest.mark.asyncio
async def test_review_submit_screen_returns_selected_event_and_body() -> None:
    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: tuple[str, str] | None = None

        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen(), self._capture)

        def _capture(self, result: tuple[str, str] | None) -> None:
            self.result = result

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("tab")
        await pilot.press("j")
        await pilot.press("j")
        await pilot.press("tab")
        await pilot.pause()

        screen = app.screen
        textarea = screen.query_one("#review-submit-body", TextArea)
        textarea.text = "needs work"

        await pilot.press("ctrl+enter")
        await pilot.pause()

        assert app.result == ("REQUEST_CHANGES", "needs work")


@pytest.mark.asyncio
async def test_review_submit_body_keeps_j_and_k_as_text() -> None:
    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen())

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        textarea = screen.query_one("#review-submit-body", TextArea)
        options = screen.query_one("#review-submit-actions", OptionList)
        highlighted_before = options.highlighted

        await pilot.press("j", "k")
        await pilot.pause()

        assert textarea.text == "jk"
        assert options.highlighted == highlighted_before


@pytest.mark.parametrize("key", ["escape", "ctrl+enter"])
async def test_submit_dialog_remains_usable_after_autosave_failure(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    store = PRStore(pr_number=123)
    store.state.pending_review.review_id = 91
    error = "gh: Could not edit a review with a missing body."
    save = AsyncMock(side_effect=GitHubError(error))
    monkeypatch.setattr(store, "save_pending_review_body", save)
    submitted = AsyncMock(return_value=PRReview(id=91, state=ReviewState.COMMENTED))
    monkeypatch.setattr(store._service, "submit_pending_review", submitted)
    monkeypatch.setattr(
        store._service, "list_review_comments", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        store._service, "get_pr_all", AsyncMock(return_value=PR(number=123))
    )
    notify = Mock()
    screen = ReviewSubmitScreen(store=store)
    monkeypatch.setattr(screen, "notify", notify)

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(screen, self.capture)

        async def capture(self, result: tuple[ReviewEvent, str] | None) -> None:
            if result is not None:
                await store.submit_review(*result)

    app = TestApp()
    async with app.run_test() as pilot:
        screen.query_one("#review-submit-body", TextArea).text = "keep this summary"
        await wait_until(lambda: notify.called, timeout=3)
        assert notify.call_args.args[0] == f"Failed to save review draft: {error}"
        submitted.assert_not_awaited()

        await pilot.press(key)
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen is not screen, f"Autosave error blocked {key}"
        if key == "escape":
            assert store.state.pending_review.body == "keep this summary"
            submitted.assert_not_awaited()
            save.reset_mock()
            reopened = ReviewSubmitScreen(store=store)
            await app.push_screen(reopened, app.capture)
            assert (
                reopened.query_one("#review-submit-body", TextArea).text
                == "keep this summary"
            )
            await pilot.pause(0.95)
            save.assert_not_awaited()
        else:
            save.assert_awaited_once()
            submitted.assert_awaited_once_with(
                123, 91, event="COMMENT", body="keep this summary"
            )


async def test_review_body_autosaves_without_submitting_and_flushes_on_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore(pr_number=123)

    async def create(pr_number, *, comments, body, commit_id) -> PRReview:
        assert comments == []
        return PRReview(id=91, state=ReviewState.PENDING, body=body)

    async def update(pr_number, review_id, *, body) -> PRReview:
        if not body.strip():
            raise ValueError("Could not edit a review with a missing body.")
        return PRReview(id=review_id, state=ReviewState.PENDING, body=body)

    create_draft = AsyncMock(side_effect=create)
    update_draft = AsyncMock(side_effect=update)
    submit = AsyncMock()
    monkeypatch.setattr(store._service, "create_pending_review", create_draft)
    monkeypatch.setattr(store._service, "update_pending_review", update_draft)
    monkeypatch.setattr(store._service, "submit_pending_review", submit)
    delete_draft = AsyncMock()
    monkeypatch.setattr(store._service, "delete_pending_review", delete_draft)
    monkeypatch.setattr(
        store._service, "list_review_comments", AsyncMock(return_value=[])
    )
    reviews_changed = Mock()
    monkeypatch.setattr(store, "_post_message", reviews_changed)

    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: tuple[str, str] | None = None

        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen(store=store), self.capture)

        def capture(self, result: tuple[str, str] | None) -> None:
            self.result = result

    app = TestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        textarea = screen.query_one("#review-submit-body", TextArea)
        assert create_draft.await_count == 0
        textarea.text = "first"
        await wait_until(lambda: store.state.pending_review.body == "first")
        textarea.text = "latest\n\nparagraph"
        await wait_until(lambda: store.state.pending_review.review_id == 91, timeout=3)
        create_draft.assert_awaited_once_with(
            123, comments=[], body="latest\n\nparagraph", commit_id=None
        )
        assert app.screen is screen
        assert app.result is None
        submit.assert_not_awaited()

        textarea.text = "save now"
        await pilot.press("ctrl+s")
        await wait_until(lambda: update_draft.await_count == 1)
        assert app.screen is screen
        assert app.result is None

        notify = Mock()
        monkeypatch.setattr(screen, "notify", notify)
        update_draft.side_effect = RuntimeError("offline")
        textarea.text = "keep this"
        await pilot.press("escape")
        await wait_until(lambda: app.screen is not screen)
        assert store.state.pending_review.body == "keep this"
        assert "offline" in notify.call_args.args[0]
        update_draft.assert_awaited_with(123, 91, body="keep this")
        submit.assert_not_awaited()

        reopened = ReviewSubmitScreen(store=store)
        await app.push_screen(reopened, app.capture)
        textarea = reopened.query_one("#review-submit-body", TextArea)
        assert textarea.text == "keep this"
        save_started, allow_save = asyncio.Event(), asyncio.Event()

        async def blocking_update(*args, **kwargs) -> PRReview:
            save_started.set()
            await allow_save.wait()
            return await update(*args, **kwargs)

        update_draft.side_effect = blocking_update
        textarea.text = "saving in progress"
        await pilot.press("ctrl+s")
        await asyncio.wait_for(save_started.wait(), timeout=1)
        try:
            textarea.text = "ready to submit"
            await pilot.press("ctrl+enter")
            assert app.screen is reopened
            assert app.result is None
        finally:
            allow_save.set()
        await wait_until(lambda: app.result == ("COMMENT", "ready to submit"))
        update_draft.assert_awaited_with(123, 91, body="saving in progress")

        cleared = ReviewSubmitScreen(store=store)
        await app.push_screen(cleared, app.capture)
        cleared.query_one("#review-submit-body", TextArea).text = ""
        await pilot.press("escape")
        await wait_until(lambda: app.screen is not cleared)
        delete_draft.assert_awaited_once_with(123, 91, require_empty=True)
        assert store.state.pending_review.review_id is None
        assert store.state.pending_review.body == ""
        assert reviews_changed.call_args.args[0].reviews == []
        update_draft.assert_awaited_with(123, 91, body="saving in progress")

        empty = ReviewSubmitScreen(store=store)
        await app.push_screen(empty, app.capture)
        assert empty.query_one("#review-submit-body", TextArea).text == ""
        await pilot.press("escape")
        await wait_until(lambda: app.screen is not empty)
        create_draft.assert_awaited_once()
        delete_draft.assert_awaited_once()
        submit.assert_not_awaited()
