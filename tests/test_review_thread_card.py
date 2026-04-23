"""Tests for reusable ReviewThreadCard widget."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Collapsible, Markdown, Static

from rit.state.models import PRComment
from rit.ui.widgets.review_thread_card import ReviewThreadCard


def make_comment(body: str, *, comment_id: int = 1) -> PRComment:
    """Create a PRComment for widget tests."""
    return PRComment.model_validate(
        {
            "databaseId": comment_id,
            "author": {"login": "alice", "avatarUrl": ""},
            "body": body,
            "path": "test.py",
            "line": 10,
            "createdAt": "2026-02-26T10:00:00Z",
            "updatedAt": "2026-02-26T10:00:00Z",
        }
    )


@pytest.mark.asyncio
async def test_compact_inline_preview_truncates_long_comment() -> None:
    """Compact inline variant should show short preview instead of full body."""
    long_body = "uv run rit https://github.com/lemonbase-tech/lemonbase/pull/19484 " * 8

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment(long_body)],
                compact=True,
                variant="inline",
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        preview = app.query_one(".inline-comment-preview", Static)
        text = getattr(preview.content, "plain", str(preview.content))

        assert len(text) < len(long_body)
        assert text.endswith(" …")


@pytest.mark.asyncio
async def test_compact_preview_strips_html_details_tags() -> None:
    """Compact preview should not leak raw <details>/<summary> tags."""
    body = "<details><summary>Prompt</summary>Line content</details>"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment(body)],
                compact=True,
                variant="inline",
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        preview = app.query_one(".inline-comment-preview", Static)
        text = getattr(preview.content, "plain", str(preview.content))
        assert "<details>" not in text
        assert "<summary>" not in text


@pytest.mark.asyncio
async def test_timeline_variant_mounts_markdown_content() -> None:
    """Non-compact timeline variant should mount markdown body widgets."""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment("Hello **world**")],
                compact=False,
                variant="timeline",
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        markdown_widgets = app.query(Markdown)
        assert len(markdown_widgets) >= 1


@pytest.mark.asyncio
async def test_timeline_variant_left_aligns_markdown_h1() -> None:
    """Markdown headings in thread cards should not use Textual's centered H1."""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment("# Test")],
                compact=False,
                variant="timeline",
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        h1 = app.query_one("MarkdownH1")
        assert h1.styles.content_align == ("left", "middle")


@pytest.mark.asyncio
async def test_timeline_variant_mounts_nested_details_without_mount_error() -> None:
    """Nested <details> blocks should mount without pre-mount MountError."""
    body = """<details>
<summary>Outer</summary>

<details>
<summary>Inner</summary>
Nested content
</details>

</details>"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment(body)],
                compact=False,
                variant="timeline",
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        collapsibles = app.query(Collapsible)
        assert len(collapsibles) >= 2
