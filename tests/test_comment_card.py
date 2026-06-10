import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.ui.widgets.comment_card import BODY_PREVIEW_RETIRE_DELAY, CommentCard


@pytest.mark.asyncio
async def test_comment_card_can_delay_markdown_body_mount() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Header",
                "# Body",
                body_mount_delay=0.05,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        assert len(app.query("MarkdownH1")) == 0

        await pilot.pause(0.1)

        assert len(app.query("MarkdownH1")) == 1


@pytest.mark.asyncio
async def test_comment_card_shows_plain_preview_while_body_mount_is_delayed() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Header",
                "# Body\n\n- first item",
                body_mount_delay=0.05,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        preview = app.query_one(".comment-body-preview", Static)
        text = str(getattr(preview.content, "plain", preview.content))

        assert "Body" in text
        assert "first item" in text
        assert len(app.query("MarkdownH1")) == 0

        await pilot.pause(0.1)

        assert len(app.query(".comment-body-preview")) == 1
        assert len(app.query("MarkdownH1")) == 1

        await pilot.pause(BODY_PREVIEW_RETIRE_DELAY + 0.1)

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
        await pilot.pause(BODY_PREVIEW_RETIRE_DELAY + 0.1)

        assert len(app.query(".comment-body-preview")) == 1
        assert len(app.query("Markdown")) == 0
