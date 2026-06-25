from typing import cast

import pytest
from textual.app import App
from textual.widgets import OptionList, Static, TextArea

from rit.state.models import PendingReviewComment
from rit.ui.screens.review_submit import ReviewSubmitScreen
from rit.ui.widgets.comment_card import CommentCard


@pytest.mark.asyncio
async def test_review_submit_screen_places_actions_below_body_in_requested_order() -> (
    None
):
    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(ReviewSubmitScreen())

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        children = list(screen.query_one("#review-submit-dialog").children)
        options = screen.query_one("#review-submit-actions", OptionList)

        assert children[1].id == "review-submit-body"
        assert children[2].id == "review-submit-actions"
        assert [option.id for option in options.options] == [
            "COMMENT",
            "APPROVE",
            "REQUEST_CHANGES",
        ]
        assert options.region.height >= len(options.options)


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

        assert children[3].id == "review-submit-pending"
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
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.result == ("COMMENT", "")


@pytest.mark.asyncio
async def test_review_submit_screen_submits_selected_action_with_enter() -> None:
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

        await pilot.press("ctrl+s")
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
