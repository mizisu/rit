from collections.abc import Callable
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Collapsible, Static

from rit.ui.widgets import comment_card as comment_card_module
from rit.ui.widgets.comment_card import CommentCard
from tests.conftest import wait_until


@pytest.mark.asyncio
async def test_comment_card_defers_markdown_until_body_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[tuple[float, Callable[[], None]]] = []

    def capture_timer(
        _self: CommentCard,
        delay: float,
        callback: Callable[[], None],
    ) -> None:
        scheduled.append((delay, callback))

    monkeypatch.setattr(CommentCard, "set_timer", capture_timer)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Header",
                "# Body",
                body_mount_delay=0.25,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        assert len(app.query("MarkdownH1")) == 0
        assert scheduled[0][0] == 0.25

        scheduled[0][1]()
        await wait_until(lambda: len(app.query("MarkdownH1")) == 1, timeout=2.0)


@pytest.mark.asyncio
async def test_markdown_comment_card_preserves_line_breaks_and_paragraph_gaps() -> None:
    first_paragraph = "[단순 질문]\n아래의 prefetch도 is_done 기준으로 가져오나요?"
    body = (
        f"{first_paragraph}\n\n**추가 질문**\n다음 줄도 유지되나요?\n\n"
        "## 확인 사항\n\n- First\n  - Nested\n- Second\n\n1. Verify\n2. Test"
    )

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard("Header", body, classes="thread-comment")

    app = TestApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.query("MarkdownList > Horizontal")) == 5)

        first, second = app.query("Markdown > MarkdownParagraph").results(Static)
        await wait_until(lambda: first.size.height == second.size.height == 2)

        assert str(getattr(first.content, "plain", first.content)) == first_paragraph
        assert str(getattr(second.content, "plain", second.content)) == (
            "추가 질문\n다음 줄도 유지되나요?"
        )
        assert second.region.y == first.region.bottom + 1
        heading = app.query_one("MarkdownHeader")
        assert heading.styles.margin.top == 2
        assert heading.styles.margin.bottom == 1
        for paragraph in app.query("MarkdownList MarkdownParagraph"):
            assert paragraph.styles.margin.bottom == 1
        nested_item = app.query_one("MarkdownList MarkdownList > Horizontal")
        assert nested_item.styles.margin.bottom == 0

        card = app.query_one(CommentCard)
        original_region = card.region
        original_content = card.content_region
        assert card.styles.background.a == 0
        assert card.styles.border.left[0] == "blank"
        for selection in ("--cursor-line", "--selected"):
            card.add_class(selection)
            await pilot.pause()
            assert card.styles.border.left[0] == "solid"
            assert card.styles.border.top[0] == ""
            assert card.region == original_region
            assert card.content_region == original_content
            card.remove_class(selection)


@pytest.mark.asyncio
async def test_plain_comment_card_preserves_whitespace_in_single_static_body() -> None:
    content = "  Plain body() text\n\n    indented  content\nlast line  "

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard(
                "Header",
                content,
                body_mount_delay=0.01,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause(0)

        await wait_until(lambda: len(app.query(".comment-body-plain")) == 1)
        body = app.query_one(".comment-body-plain", Static)
        text = str(getattr(body.content, "plain", body.content))

        assert text == content
        await wait_until(lambda: body.size.height == 4)
        assert len(app.query(".comment-body-preview")) == 0
        assert len(app.query("Markdown")) == 0

        card = app.query_one(CommentCard)
        original_region = card.region
        original_border = card.styles.border
        assert card.styles.background.hex == "#1E2030"
        assert original_border.top[0] == "solid"
        assert original_border.top == original_border.left
        card.add_class("--selected")
        await pilot.pause()
        assert card.region == original_region
        assert card.styles.border.top == original_border.top
        assert card.styles.border.left != original_border.left


@pytest.mark.asyncio
async def test_document_markdown_style_is_shared_with_comments_and_details() -> None:
    body = (
        "# Title\n\nText with **bold**, *emphasis*, ~~deleted~~, `code` and "
        "[a link](https://example.com).\n\n"
        "## Section\n\n- d\n  - f\n    - deeper\n  - g\n- h\n\n"
        "3. Ordered\n   1. Nested\n   2. Next\n4. Last\n\n"
        "> Quote\n>\n> Another paragraph\n\n---\n\n"
        "| Name | Value |\n| --- | --- |\n| a | b |\n\n"
        "```python\nprint('hello')\n```\n\n"
        "<details><summary>More</summary>\n\n- inside\n  - nested\n\n</details>"
    )

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield CommentCard("Description", body, classes="description-container")
            yield CommentCard("Reply", body, classes="thread-reply")

    app = TestApp(css_path=Path(__file__).parents[1] / "src/rit/rit.tcss")
    app.theme = "catppuccin-macchiato"
    async with app.run_test(size=(100, 100)) as pilot:
        await wait_until(lambda: len(app.query("MarkdownTableContent")) == 2)
        await pilot.pause()
        for card in app.query(CommentCard):
            headings = list(card.query("MarkdownHeader"))
            assert [heading.styles.margin.top for heading in headings] == [0, 2]
            assert all(heading.styles.text_style.bold for heading in headings)
            assert [
                bullet.render_line(0).text.strip()
                for bullet in card.query(
                    "MarkdownBulletList > Horizontal > MarkdownBullet"
                )
            ] == ["•", "◦", "▪", "◦", "•"]
            for paragraph in card.query("MarkdownList MarkdownParagraph"):
                assert paragraph.styles.margin.bottom == 1
            paragraph = card.query_one("Markdown > MarkdownParagraph")
            body_color = paragraph.styles.color
            assert body_color.r == body_color.g == body_color.b
            assert all(heading.styles.color.a > body_color.a for heading in headings)
            strong_style = paragraph.get_component_styles("strong")
            assert strong_style.text_style.bold
            assert strong_style.color.a > body_color.a
            if card.has_class("description-container"):
                assert card.query_one(".comment-header").styles.color.a < body_color.a
                assert card.styles.padding.left == card.styles.padding.right == 3
            code_style = paragraph.get_component_styles("code_inline")
            assert code_style.color == paragraph.styles.color
            assert 0 < code_style.background.a <= 0.1
            assert paragraph.styles.link_style_hover.underline
            quote = card.query_one("MarkdownBlockQuote")
            assert quote.styles.background.a == 0
            assert quote.styles.border.left[0] == "solid"
            rule = card.query_one("MarkdownHorizontalRule")
            assert rule.region.height == 2
            assert any(
                "─" in strip.text[rule.region.x : rule.region.right]
                for strip in app.screen._compositor.render_strips()[
                    rule.region.y : rule.region.bottom
                ]
            )
            table_header = card.query_one("MarkdownTableContent > .header")
            assert table_header.styles.text_style.bold
            assert (
                card.query_one("CopyableCodeBlock MarkdownFence").styles.padding.left
                == 0
            )
            card.query_one(Collapsible).collapsed = False
        await wait_until(lambda: len(app.query(".details-content MarkdownBullet")) == 4)
        await pilot.pause()
        for card in app.query(CommentCard):
            assert [
                bullet.render_line(0).text.strip()
                for bullet in card.query(".details-content MarkdownBullet")
            ] == ["•", "◦"]


@pytest.mark.parametrize(
    ("body", "plain"),
    [
        ("1. First\n2. Second", False),
        ("1) First", False),
        ("+ Item", False),
        ("~~Deleted~~", False),
        ("Version 1.2", True),
    ],
)
def test_markdown_syntax_is_not_rendered_as_plain_text(body: str, plain: bool) -> None:
    assert comment_card_module._is_plain_body(body) is plain


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
