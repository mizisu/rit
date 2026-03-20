"""Tests for collapsible markdown component."""

import pytest

from rit.ui.components.collapsible_markdown import (
    parse_details_blocks,
    DetailsBlock,
    MarkdownPart,
)


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
        assert result[0].details.summary == "Click to expand"
        assert "Hidden content here." in result[0].details.content

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
        assert result[1].details.summary == "Summary"
        assert "Inner content" in result[1].details.content

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
        assert result[0].details.summary == "First"

        assert result[1].is_details is False
        assert "Middle text" in result[1].content

        assert result[2].is_details is True
        assert result[2].details.summary == "Second"

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
        assert "```python" in result[0].details.content
        assert 'print("world")' in result[0].details.content

    def test_case_insensitive(self) -> None:
        """Tags are case insensitive."""
        body = """<DETAILS>
<SUMMARY>Upper case</SUMMARY>
Content
</DETAILS>"""
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert result[0].is_details is True
        assert result[0].details.summary == "Upper case"

    def test_whitespace_handling(self) -> None:
        """Whitespace is handled correctly."""
        body = """<details>
  <summary>  Spaced summary  </summary>
  
  Spaced content.
  
</details>"""
        result = parse_details_blocks(body)

        assert len(result) == 1
        assert result[0].is_details is True
        assert result[0].details.summary == "Spaced summary"
        assert "Spaced content" in result[0].details.content

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
        assert result[0].details.summary == "Outer"

        # Inner content should contain both nested details blocks
        inner_content = result[0].details.content
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
        assert result[0].details.summary == "Level 1"
        assert "Level 2" in result[0].details.content
        assert "Level 3" in result[0].details.content
        assert "Deep content" in result[0].details.content

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
        assert result[0].details.summary == "First"
        assert "Nested in First" in result[0].details.content
        assert result[1].details.summary == "Second"
        assert "Second content" in result[1].details.content

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
        assert "Learnings (2)" in result[1].details.summary
        assert "Learning: 2026-01-13" in result[1].details.content
        assert "Learning: 2026-01-14" in result[1].details.content

        # Third is Code graph
        assert result[2].is_details is True
        assert "Code graph analysis" in result[2].details.summary
