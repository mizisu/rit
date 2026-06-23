from collections.abc import Callable

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.ui.widgets import comment_card as comment_card_module
from rit.ui.widgets.comment_card import CommentCard
from tests.conftest import wait_until


@pytest.mark.asyncio
async def test_comment_card_can_delay_markdown_body_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled_callbacks: list[tuple[float, Callable[[], None]]] = []

    def capture_timer(
        self: CommentCard,
        delay: float,
        callback: Callable[[], None],
        *args: object,
        **kwargs: object,
    ) -> None:
        scheduled_callbacks.append((delay, callback))

    monkeypatch.setattr(CommentCard, "set_timer", capture_timer)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Header",
                "# Body",
                body_mount_delay=0.01,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        assert len(app.query("MarkdownH1")) == 0

        mount_delay, mount_callback = scheduled_callbacks.pop(0)
        assert mount_delay == 0.01
        mount_callback()

        await wait_until(lambda: len(app.query("MarkdownH1")) == 1, timeout=2.0)

        assert len(app.query("MarkdownH1")) == 1


@pytest.mark.asyncio
async def test_comment_card_shows_plain_preview_while_body_mount_is_delayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(comment_card_module, "BODY_PREVIEW_RETIRE_DELAY", 0.01)
    scheduled_callbacks: list[tuple[float, Callable[[], None]]] = []

    def capture_timer(
        self: CommentCard,
        delay: float,
        callback: Callable[[], None],
        *args: object,
        **kwargs: object,
    ) -> None:
        scheduled_callbacks.append((delay, callback))

    monkeypatch.setattr(CommentCard, "set_timer", capture_timer)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Header",
                "# Body\n\n- first item",
                body_mount_delay=0.01,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        preview = app.query_one(".comment-body-preview", Static)
        text = str(getattr(preview.content, "plain", preview.content))

        assert "Body" in text
        assert "first item" in text
        assert len(app.query("MarkdownH1")) == 0

        mount_delay, mount_callback = scheduled_callbacks.pop(0)
        assert mount_delay == 0.01
        mount_callback()

        await wait_until(lambda: len(app.query("MarkdownH1")) == 1, timeout=2.0)

        assert len(app.query(".comment-body-preview")) == 1
        assert len(app.query("MarkdownH1")) == 1

        retire_delay, retire_callback = scheduled_callbacks.pop(0)
        assert retire_delay == 0.01
        retire_callback()

        await wait_until(lambda: len(app.query(".comment-body-preview")) == 0)

        assert len(app.query(".comment-body-preview")) == 0


def test_comment_card_preview_strips_markdown_links_and_images() -> None:
    card = CommentCard(
        "Header",
        "![Review Change Stack](https://example.com/review.svg) "
        "[docs](https://example.com/docs) `code` **bold**",
    )

    preview = card._build_preview(card._body)

    assert preview == "Review Change Stack docs code bold"
    assert "https://" not in preview
    assert "![" not in preview
    assert "](" not in preview


@pytest.mark.asyncio
async def test_loading_comment_card_does_not_duplicate_plain_body_as_markdown() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Loading",
                "Fetching title and description...",
                classes="timeline-loading",
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        assert len(app.query(".comment-body-preview")) == 1
        assert len(app.query("Markdown")) == 0
