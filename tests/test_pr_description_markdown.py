"""Exercise description reading geometry without repeatedly booting the full app."""

import asyncio
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from unicodedata import normalize

import pytest
from PIL import Image
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.content import Content
from textual.widgets import Collapsible, Static

import rit.ui.markdown_images as markdown_images_module
from rit.ui.collapsible_markdown import CopyableCodeBlock
from rit.ui.markdown_images import (
    MAX_INLINE_IMAGE_HEIGHT_CELLS,
    ImageFetchError,
    ImageViewerScreen,
    MarkdownImageBlock,
)
from rit.ui.widgets.comment_card import CommentCard
from tests.conftest import wait_until

ROOT = Path(__file__).parents[1]


@dataclass(frozen=True)
class DescriptionCase:
    """A distinct Markdown shape that must fit inside the reading column."""

    name: str
    body: str


def _description_cases() -> tuple[DescriptionCase, ...]:
    korean = "긴 한국어 문장을 여러 줄로 표시해도 순서와 내용을 잃지 않아야 합니다. "
    code = "review_answer_check_matching_service_" * 6
    link = "https://example.com/" + "long-review-path/" * 12
    return (
        DescriptionCase("plain", korean * 4),
        DescriptionCase("plain-lines", "  첫 줄\n\n    두 번째 줄\n마지막 줄  "),
        DescriptionCase("unbroken-korean", "가독성확인" * 35),
        DescriptionCase("unbroken-ascii", "abcdefghij" * 24),
        DescriptionCase("source-breaks", "첫 줄\n두 번째 줄\n\n**문단**\n마지막 줄"),
        DescriptionCase("inline-code", f"조회: `{code}` 다음 설명입니다."),
        DescriptionCase(
            "inline-korean", "결과: `한국어_식별자_" + "상태값" * 30 + "` 끝"
        ),
        DescriptionCase("link", f"[{korean * 3}]({link})"),
        DescriptionCase("autolink", f"<{link}>"),
        DescriptionCase("unicode", "### 문자\n\n한글 e\u0301 🤖 한글 English " * 3),
        DescriptionCase(
            "headings",
            "\n\n".join(
                f"{'#' * level} {korean * 2}\n\n본문 {level}" for level in range(1, 7)
            ),
        ),
        DescriptionCase(
            "nested",
            f"- {korean}\n  - {korean}\n    - {korean}\n\n      9. {korean}\n      10. {korean}\n      11. {korean}",
        ),
        DescriptionCase("loose-list", f"- {korean}\n\n  {korean}\n\n- 마지막 항목"),
        DescriptionCase("tasks", "- [x] 확인 완료\n- [ ] 확인 예정\n  - 세부 확인"),
        DescriptionCase("quote", f"> {korean * 3}\n>\n> **강조**와 `code`"),
        DescriptionCase("rule", "이전 문단\n\n---\n\n다음 문단"),
        DescriptionCase(
            "table",
            f"| 항목 | 설명 |\n| --- | --- |\n| 이름 | {korean * 2} |\n| 코드 | `{code}` |",
        ),
        DescriptionCase(
            "many-columns",
            "| 상태 | 설명 | 경로 | 담당 | 검증 | 메모 |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| 완료 | 확인 결과 | 파일 이름 | 작성자 | 테스트 | 추가 설명 |",
        ),
        DescriptionCase("fence", f"```python\nvalue = '{code}'\nprint(value)\n```"),
        DescriptionCase(
            "details",
            f"앞 문단\n\n<details><summary>자세한 내용</summary>\n\n### 내부 제목\n\n- {korean}\n  - `{code}`\n\n</details>\n\n뒤 문단",
        ),
        DescriptionCase(
            "image-fallback",
            "### 첨부\n\n![로컬 이미지](file:///unavailable.png)\n\n이미지 뒤 설명",
        ),
    )


def _compact_text(text: str) -> str:
    return "".join(normalize("NFC", text).split())


class DescriptionMatrixApp(App[None]):
    """Mount all content shapes once and exercise resizing and theme changes."""

    CSS_PATH = ROOT / "src/rit/rit.tcss"

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            for case in _description_cases():
                yield CommentCard(
                    case.name,
                    case.body,
                    id=case.name,
                    classes="description-container",
                )


@pytest.mark.asyncio
async def test_description_content_matrix_preserves_text_and_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = DescriptionMatrixApp()
    opened_urls: list[str] = []
    monkeypatch.setattr(app, "open_url", opened_urls.append)
    async with app.run_test(size=(180, 60)) as pilot:
        cards = list(app.query(CommentCard))
        await wait_until(
            lambda: all(card.query(".comment-body-plain, Markdown") for card in cards)
        )
        app.query_one(Collapsible).collapsed = False
        await wait_until(lambda: bool(app.query(".details-content MarkdownParagraph")))

        for theme in ("catppuccin-macchiato", "textual-light"):
            app.theme = theme
            for width in (180, 80, 40, 180):
                await pilot.resize_terminal(width, 60)
                await pilot.pause()
                scroll = app.query_one(VerticalScroll)
                assert scroll.max_scroll_x == 0
                for card in cards:
                    body = card.query_one(".comment-content")
                    assert body.content_region.width == min(
                        100, card.content_region.width
                    ), card.id
                    assert card.max_scroll_x == body.max_scroll_x == 0, card.id
                    for block in card.query(
                        "MarkdownHeader, MarkdownParagraph, .comment-body-plain, MarkdownTableCellContents"
                    ).results(Static):
                        rendered = [
                            block.render_line(i).text for i in range(block.size.height)
                        ]
                        assert _compact_text("".join(rendered)) == _compact_text(
                            str(block.content)
                        ), (theme, width, card.id, str(block.content), rendered)
                        assert all(
                            Content(line).cell_length <= block.content_size.width
                            for line in rendered
                        )
                        assert body.content_region.x <= block.region.x, (width, card.id)
                        assert block.region.right <= body.content_region.right, (
                            width,
                            card.id,
                        )
                        assert block.region.bottom <= body.content_region.bottom, (
                            width,
                            card.id,
                        )

                ordered = app.query_one("#nested MarkdownOrderedList")
                bullets = list(ordered.query("MarkdownBullet"))
                assert [bullet.render_line(0).text.strip() for bullet in bullets] == [
                    "9.",
                    "10.",
                    "11.",
                ]
                assert len({bullet.region.right for bullet in bullets}) == 1
                paragraphs = list(ordered.query("MarkdownParagraph"))
                assert all(p.region.x == bullets[0].region.right for p in paragraphs)

                code_block = app.query_one(CopyableCodeBlock)
                fence = code_block.query_one("MarkdownFence")
                assert fence.max_scroll_x > 0
                assert fence.region.right <= code_block.content_region.right
                code_block.copy_code()
                assert (
                    app.clipboard
                    == "value = '"
                    + "review_answer_check_matching_service_" * 6
                    + "'\nprint(value)"
                )

        details = app.query_one(Collapsible)
        paragraphs_before = tuple(details.query("MarkdownParagraph"))
        details.collapsed = True
        await pilot.pause()
        details.collapsed = False
        await pilot.wait_for_scheduled_animations()
        assert tuple(details.query("MarkdownParagraph")) == paragraphs_before
        status = app.query_one("#image-fallback .markdown-image-status", Static)
        assert (
            Content.from_markup(str(status.content)).plain
            == "Unsupported image source."
        )

        card = app.query_one("#inline-code", CommentCard)
        original_body = card.query_one(".comment-content")
        original_region = original_body.virtual_region
        card.add_class("--selected")
        await pilot.pause()
        assert original_body.virtual_region == original_region
        card.toggle_collapsed()
        await pilot.pause()
        assert not original_body.display
        card.toggle_collapsed()
        await pilot.pause()
        assert original_body.display
        assert original_body.virtual_region == original_region

        link = app.query_one("#link MarkdownParagraph")
        scroll.scroll_to_widget(link, top=True, animate=False)
        await pilot.pause()
        assert await pilot.click(link)
        await wait_until(lambda: bool(opened_urls))
        assert opened_urls == ["https://example.com/" + "long-review-path/" * 12]


class DescriptionImagesApp(App[None]):
    """Cover screenshot comparisons shaped like real PR descriptions."""

    CSS_PATH = ROOT / "src/rit/rit.tcss"

    def compose(self) -> ComposeResult:
        wide = '<img src="https://example.com/wide.png" />'
        portrait = '<img src="https://example.com/portrait.png" />'
        missing = '<img src="https://example.com/missing.png" />'
        with VerticalScroll():
            yield CommentCard(
                "Standalone", wide, id="standalone", classes="description-container"
            )
            yield CommentCard(
                "Before / After",
                f"| Before | After |\n| --- | --- |\n| {portrait} | {portrait} |",
                id="comparison",
                classes="description-container",
            )
            yield CommentCard(
                "Mixed results",
                "| 구분 | Before | After |\n| --- | --- | --- |\n"
                f"| 작성 | {wide} | {missing} |\n| 결과 | {missing} | {wide} |",
                id="mixed",
                classes="description-container",
            )
            yield CommentCard(
                "Details",
                "<details><summary>Outer</summary>\n\n"
                "<details><summary>Inner</summary>\n\n"
                f"{wide}\n\n</details>\n\n</details>",
                id="image-details",
                classes="description-container",
            )
            yield CommentCard("Comment", wide, id="image-comment")


@pytest.mark.asyncio
async def test_description_images_fit_after_loading_failure_and_resize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images: dict[str, bytes] = {}
    for name, size in (("wide", (2000, 800)), ("portrait", (400, 700))):
        buffer = BytesIO()
        Image.new("RGB", size, "white").save(buffer, format="PNG")
        images[f"https://example.com/{name}.png"] = buffer.getvalue()

    finish_loading = asyncio.Event()

    async def fetch_image(url: str, *, github_context: str = "") -> bytes:
        await finish_loading.wait()
        if url not in images:
            raise ImageFetchError("HTTP 400 while fetching image")
        return images[url]

    monkeypatch.setattr(markdown_images_module, "fetch_image_bytes_async", fetch_image)
    app = DescriptionImagesApp()
    async with app.run_test(size=(180, 60)) as pilot:
        await wait_until(lambda: len(app.query(MarkdownImageBlock)) == 8)
        app.query_one(Collapsible).collapsed = False
        await wait_until(lambda: len(app.query(Collapsible)) == 2)
        list(app.query(Collapsible))[1].collapsed = False
        await wait_until(lambda: len(app.query(MarkdownImageBlock)) == 9)
        await pilot.pause()
        for status in app.query(".description-container .markdown-image-status"):
            assert status.size.width >= 5
            assert status.size.height > 0
        finish_loading.set()
        await wait_until(
            lambda: all(
                block._loaded or "HTTP 400" in str(block._status.content)
                for block in app.query(MarkdownImageBlock)
            )
        )
        await pilot.pause()
        comment_image = app.query_one("#image-comment .markdown-terminal-image")
        original_comment_size = comment_image.size
        for width in (180, 80, 40, 180):
            await pilot.resize_terminal(width, 60)
            await pilot.pause()
            for card in app.query(".description-container"):
                body = card.query_one(".comment-content")
                for block in body.query(
                    "MarkdownImageTable, .markdown-image-table-row, "
                    ".markdown-image-table-cell, MarkdownImageBlock, "
                    ".markdown-terminal-image, .markdown-image-status"
                ):
                    assert body.content_region.x <= block.region.x
                    assert block.region.right <= body.content_region.right, (
                        width,
                        card.id,
                        type(block).__name__,
                        block.region,
                    )
                    assert block.size.width > 0
                for status in body.query(".markdown-image-status").results(Static):
                    rendered = "".join(
                        status.render_line(i).text for i in range(status.size.height)
                    )
                    assert _compact_text(rendered) == _compact_text(
                        Content.from_markup(str(status.content)).plain
                    )
                for image in body.query(".markdown-terminal-image"):
                    assert image.size.width >= 5, (
                        width,
                        card.id,
                        image.region,
                    )
                    assert 0 < image.size.height <= MAX_INLINE_IMAGE_HEIGHT_CELLS
            for table in app.query("MarkdownImageTable"):
                rows = list(table.query(".markdown-image-table-row"))
                columns = [
                    [(cell.region.x, cell.size.width) for cell in row.children]
                    for row in rows
                ]
                assert all(cells == columns[0] for cells in columns)
            assert comment_image.size == original_comment_size
        preview = app.query_one("#standalone MarkdownImageBlock", MarkdownImageBlock)
        app.query_one(VerticalScroll).scroll_to_widget(preview, animate=False)
        await pilot.pause()
        assert await pilot.click(preview)
        await wait_until(lambda: isinstance(app.screen, ImageViewerScreen))
        await pilot.press("escape")
        assert not isinstance(app.screen, ImageViewerScreen)
