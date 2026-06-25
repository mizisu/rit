import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.ui.widgets import comment_card as comment_card_module
from rit.ui.widgets.comment_card import CommentCard
from tests.conftest import wait_until


@pytest.mark.asyncio
async def test_comment_card_mounts_markdown_without_delayed_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_timer(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("comment body should not use delayed preview swaps")

    monkeypatch.setattr(CommentCard, "set_timer", fail_timer)

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

        await wait_until(lambda: len(app.query("MarkdownH1")) == 1, timeout=2.0)

        assert len(app.query(".comment-body-preview")) == 0
        assert len(app.query("MarkdownH1")) == 1


@pytest.mark.asyncio
async def test_plain_comment_card_uses_single_static_body() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Header",
                "Plain body() text",
                body_mount_delay=0.01,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        await wait_until(lambda: len(app.query(".comment-body-plain")) == 1)
        body = app.query_one(".comment-body-plain", Static)
        text = str(getattr(body.content, "plain", body.content))

        assert text == "Plain body() text"
        assert len(app.query(".comment-body-preview")) == 0
        assert len(app.query("Markdown")) == 0


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


def test_comment_card_plain_preview_line_skips_regex_sanitizers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        comment_card_module.re,
        "sub",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("plain preview lines should not run regex sanitizers")
        ),
    )

    card = CommentCard("Header", "Plain preview text")

    assert card._build_preview(card._body) == "Plain preview text"


def test_comment_card_plain_preview_marker_check_skips_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        comment_card_module,
        "any",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preview marker checks should not allocate an any iterator")
        ),
        raising=False,
    )

    card = CommentCard("Header", "Plain preview text")

    assert card._build_preview(card._body) == "Plain preview text"


def test_comment_card_preview_streams_body_lines() -> None:
    class NoSplitLines(str):
        def splitlines(self, *_args: object, **_kwargs: object) -> list[str]:
            raise AssertionError("comment previews should stream body lines")

    card = CommentCard("Header", "")

    preview = card._build_preview(
        NoSplitLines("# First\n\n- second\n\nthird should not be needed")
    )

    assert preview == "First second …"


def test_comment_card_retires_tracked_preview_without_copying_children() -> None:
    card = CommentCard("Header", "Body")
    card._body_preview_widget = Static("preview")

    card._retire_body_preview()

    assert card._body_preview_widget is None


def test_comment_card_removes_rendered_body_without_copying_children() -> None:
    class Container:
        def remove_children(self) -> None:
            calls.append("removed")

    calls: list[str] = []
    card = CommentCard("Header", "Body")
    card._content_container = Container()  # type: ignore[assignment]

    card._remove_rendered_body_widgets()

    assert calls == ["removed"]


@pytest.mark.asyncio
async def test_empty_comment_card_has_no_placeholder_or_body_gap() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard("Header", "")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        card = app.query_one(CommentCard)
        assert card.has_class("--empty-body")
        assert len(app.query(".comment-body-preview")) == 0
        assert len(app.query("Markdown")) == 0


def test_empty_comment_card_preview_is_blank() -> None:
    card = CommentCard("Header", "")

    assert card._build_preview(card._body) == ""


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

        await wait_until(lambda: len(app.query(".comment-body-plain")) == 1)
        assert len(app.query(".comment-body-preview")) == 0
        assert len(app.query("Markdown")) == 0
