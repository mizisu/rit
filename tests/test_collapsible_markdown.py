"""Tests for collapsible markdown component."""

import base64

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches

from rit.ui.components.collapsible_markdown import (
    CopyableCodeBlock,
    DetailsBlock,
    ImageViewerScreen,
    MarkdownImageBlock,
    MarkdownPart,
    mount_markdown_with_details,
    parse_details_blocks,
    parse_fenced_code_blocks,
    parse_markdown_image_parts,
)


def _details(part: MarkdownPart) -> DetailsBlock:
    assert part.details is not None
    return part.details


class TestParseDetailsBlocks:
    """Tests for parse_details_blocks function."""

    def test_empty_string(self) -> None:
        """Empty string returns empty list."""
        result = parse_details_blocks("")
        assert result == []

    def test_none_input(self) -> None:
        """None input returns empty list."""
        result = parse_details_blocks(None)  # type: ignore
        assert result == []

    def test_no_details_tag(self) -> None:
        """Content without details tag returns single part."""
        body = "Just some **markdown** content."
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert result[0].content == body
        assert result[0].is_details is False

    def test_single_details_block(self) -> None:
        """Single details block is parsed correctly."""
        body = """<details>
<summary>Click to expand</summary>

Hidden content here.

</details>"""
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert result[0].is_details is True
        assert result[0].details is not None
        assert _details(result[0]).summary == "Click to expand"
        assert "Hidden content here." in _details(result[0]).content

    def test_details_with_surrounding_content(self) -> None:
        """Details block with content before and after."""
        body = """Some text before.

<details>
<summary>Summary</summary>

Inner content.

</details>

Some text after."""
        result = parse_details_blocks(body)

        assert len(result) == 3

        # Before text
        assert result[0].is_details is False
        assert "Some text before" in result[0].content

        # Details block
        assert result[1].is_details is True
        assert _details(result[1]).summary == "Summary"
        assert "Inner content" in _details(result[1]).content

        # After text
        assert result[2].is_details is False
        assert "Some text after" in result[2].content

    def test_multiple_details_blocks(self) -> None:
        """Multiple details blocks are parsed correctly."""
        body = """<details>
<summary>First</summary>
Content 1
</details>

Middle text.

<details>
<summary>Second</summary>
Content 2
</details>"""
        result = parse_details_blocks(body)

        assert len(result) == 3

        assert result[0].is_details is True
        assert _details(result[0]).summary == "First"

        assert result[1].is_details is False
        assert "Middle text" in result[1].content

        assert result[2].is_details is True
        assert _details(result[2]).summary == "Second"

    def test_details_with_code_block(self) -> None:
        """Details block containing code block."""
        body = """<details>
<summary>Show code</summary>

```python
def hello():
    print("world")
```

</details>"""
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert result[0].is_details is True
        assert "```python" in _details(result[0]).content
        assert 'print("world")' in _details(result[0]).content

    def test_case_insensitive(self) -> None:
        """Tags are case insensitive."""
        body = """<DETAILS>
<SUMMARY>Upper case</SUMMARY>
Content
</DETAILS>"""
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert result[0].is_details is True
        assert _details(result[0]).summary == "Upper case"

    def test_whitespace_handling(self) -> None:
        """Whitespace is handled correctly."""
        body = """<details>
  <summary>  Spaced summary  </summary>
  
  Spaced content.
  
</details>"""
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert result[0].is_details is True
        assert _details(result[0]).summary == "Spaced summary"
        assert "Spaced content" in _details(result[0]).content

    def test_nested_details_blocks(self) -> None:
        """Nested details blocks are parsed correctly."""
        body = """<details>
<summary>Outer</summary>

<details>
<summary>Inner 1</summary>
Content 1
</details>

<details>
<summary>Inner 2</summary>
Content 2
</details>

</details>"""
        result = parse_details_blocks(body)

        # Should be one top-level details block
        assert len(result) == 1
        assert result[0].is_details is True
        assert _details(result[0]).summary == "Outer"

        # Inner content should contain both nested details blocks
        inner_content = _details(result[0]).content
        assert "<details>" in inner_content.lower()
        assert "Inner 1" in inner_content
        assert "Inner 2" in inner_content
        assert "Content 1" in inner_content
        assert "Content 2" in inner_content

    def test_deeply_nested_details(self) -> None:
        """Deeply nested (3 levels) details blocks."""
        body = """<details>
<summary>Level 1</summary>

<details>
<summary>Level 2</summary>

<details>
<summary>Level 3</summary>
Deep content
</details>

</details>

</details>"""
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert _details(result[0]).summary == "Level 1"
        assert "Level 2" in _details(result[0]).content
        assert "Level 3" in _details(result[0]).content
        assert "Deep content" in _details(result[0]).content

    def test_mixed_nested_and_sibling_details(self) -> None:
        """Mix of nested and sibling details blocks."""
        body = """<details>
<summary>First</summary>

<details>
<summary>Nested in First</summary>
Nested content
</details>

</details>

<details>
<summary>Second</summary>
Second content
</details>"""
        result = parse_details_blocks(body)

        # Two top-level details blocks
        assert len(result) == 2
        assert _details(result[0]).summary == "First"
        assert "Nested in First" in _details(result[0]).content
        assert _details(result[1]).summary == "Second"
        assert "Second content" in _details(result[1]).content

    def test_coderabbit_style_nested(self) -> None:
        """CodeRabbit-style nested details (learnings pattern)."""
        body = """Actionable comments posted: 3

<details>
<summary>🧠 Learnings (2)</summary>

<details>
<summary>📜 Learning: 2026-01-13T02:43:12.093Z</summary>

Some learning content here.

</details>

<details>
<summary>📜 Learning: 2026-01-14T02:53:16.495Z</summary>

Another learning content.

</details>

</details>

<details>
<summary>🚀 Code graph analysis</summary>
Graph content
</details>"""
        result = parse_details_blocks(body)

        # Should have: text, Learnings details, Code graph details
        assert len(result) == 3

        # First is plain text
        assert result[0].is_details is False
        assert "Actionable comments posted: 3" in result[0].content

        # Second is Learnings with nested details
        assert result[1].is_details is True
        assert "Learnings (2)" in _details(result[1]).summary
        assert "Learning: 2026-01-13" in _details(result[1]).content
        assert "Learning: 2026-01-14" in _details(result[1]).content

        # Third is Code graph
        assert result[2].is_details is True
        assert "Code graph analysis" in _details(result[2]).summary


class TestParseMarkdownImages:
    def test_markdown_image_is_split_from_markdown_text(self) -> None:
        body = (
            'Before\n\n![Screenshot](https://example.com/screenshot.png "UI")\n\nAfter'
        )

        result = parse_markdown_image_parts(body)

        assert len(result) == 3
        assert result[0].content == "Before"
        assert result[1].is_image is True
        image = result[1].image
        assert image is not None
        assert image.alt == "Screenshot"
        assert image.src == "https://example.com/screenshot.png"
        assert image.title == "UI"
        assert result[2].content == "After"

    def test_standalone_html_image_tag_is_split_from_markdown_text(self) -> None:
        body = (
            'Before\n\n<img alt="Screenshot" src="https://example.com/ui.png">\n\nAfter'
        )

        result = parse_markdown_image_parts(body)

        assert len(result) == 3
        assert result[1].is_image is True
        image = result[1].image
        assert image is not None
        assert image.alt == "Screenshot"
        assert image.src == "https://example.com/ui.png"

    def test_inline_markdown_badges_stay_markdown_text(self) -> None:
        body = (
            "## [![Quality Gate Failed](https://example.com/qg-failed-20px.png "
            "'Quality Gate Failed')](https://example.com/dashboard) "
            "**Quality Gate failed**\n"
            "![](https://example.com/failed-16px.png '') "
            "[80.8% Coverage on New Code](https://example.com/coverage)"
        )

        result = parse_markdown_image_parts(body)

        assert len(result) == 1
        assert result[0].is_image is False
        assert result[0].content == body

    def test_inline_html_image_tag_stays_markdown_text(self) -> None:
        body = 'Before <img alt="Screenshot" src="https://example.com/ui.png"> After'

        result = parse_markdown_image_parts(body)

        assert len(result) == 1
        assert result[0].is_image is False
        assert result[0].content == body

    def test_relative_image_url_is_resolved_against_base_url(self) -> None:
        result = parse_markdown_image_parts(
            "![Diagram](assets/diagram.png)",
            base_url="https://github.com/owner/repo/pull/123",
        )

        image = result[0].image
        assert image is not None
        assert image.src == "https://github.com/owner/repo/pull/assets/diagram.png"
        assert image.github_context == "owner/repo"

    def test_github_user_attachment_keeps_repo_context(self) -> None:
        result = parse_markdown_image_parts(
            "![Screenshot](https://github.com/user-attachments/assets/abc123)",
            base_url="https://github.com/owner/repo/pull/123",
        )

        image = result[0].image
        assert image is not None
        assert image.src == "https://github.com/user-attachments/assets/abc123"
        assert image.github_context == "owner/repo"


class TestParseFencedCodeBlocks:
    def test_fenced_code_block_is_split_from_markdown_text(self) -> None:
        body = """Before.

```python
print("hello")
```

After."""

        result = parse_fenced_code_blocks(body)

        assert len(result) == 3
        assert result[0].content == "Before."
        assert result[0].is_code is False
        assert result[1].is_code is True
        assert result[1].language == "python"
        assert result[1].content == 'print("hello")'
        assert result[2].content == "After."

    def test_unclosed_fence_stays_markdown_text(self) -> None:
        body = """Before.

```python
print("hello")"""

        result = parse_fenced_code_blocks(body)

        assert len(result) == 1
        assert result[0].is_code is False
        assert "```python" in result[0].content


@pytest.mark.asyncio
async def test_markdown_image_block_loads_image_from_in_memory_bytes() -> None:
    body = "![Tiny](https://example.com/tiny.png)"
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1Pe"
        "AAAADUlEQVR42mP8z8BQDwAFgwJ/lOOFzgAAAABJRU5ErkJggg=="
    )
    fetched_urls: list[str] = []

    async def fetcher(url: str) -> bytes:
        fetched_urls.append(url)
        return png_bytes

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Vertical(id="root")

        def on_mount(self) -> None:
            root = self.query_one("#root", Vertical)
            mount_markdown_with_details(root, body, image_fetcher=fetcher)

    app = TestApp()
    async with app.run_test() as pilot:
        image_widget = None
        for _ in range(10):
            await pilot.pause(0.1)
            try:
                image_widget = app.query_one(".markdown-terminal-image")
            except NoMatches:
                continue
            if fetched_urls:
                break

        assert fetched_urls == ["https://example.com/tiny.png"]
        assert app.query_one(MarkdownImageBlock).image.alt == "Tiny"
        assert image_widget is not None
        assert image_widget.size.height > 0

        clicked = await pilot.click("MarkdownImageBlock .markdown-image-header")
        await pilot.pause(0.1)

        assert clicked is True
        assert isinstance(app.screen, ImageViewerScreen)


@pytest.mark.asyncio
async def test_eager_details_with_code_block_waits_for_inner_container_mount() -> None:
    body = """<details>
<summary>Patch</summary>

```diff
- old
+ new
```

</details>"""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Vertical(id="root")

        def on_mount(self) -> None:
            root = self.query_one("#root", Vertical)
            mount_markdown_with_details(root, body)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert app.query_one(CopyableCodeBlock) is not None


@pytest.mark.asyncio
async def test_removed_eager_details_does_not_load_unmounted_content() -> None:
    body = """<details>
<summary>Patch</summary>

```diff
- old
+ new
```

</details>"""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Vertical(id="root")

        def on_mount(self) -> None:
            root = self.query_one("#root", Vertical)
            mount_markdown_with_details(root, body)
            root.remove_children()

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert len(app.query(CopyableCodeBlock)) == 0


@pytest.mark.asyncio
async def test_copyable_code_block_copies_raw_code() -> None:
    body = """Before.

```python
print("hello")
```

After."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Vertical(id="root")

        def on_mount(self) -> None:
            root = self.query_one("#root", Vertical)
            mount_markdown_with_details(root, body)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.query_one(CopyableCodeBlock) is not None
        clicked = await pilot.click("CopyableCodeBlock Button.code-copy-button")
        await pilot.pause()

        assert clicked is True
        assert app.clipboard == 'print("hello")'
