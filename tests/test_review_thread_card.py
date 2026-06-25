"""Tests for reusable ReviewThreadCard widget."""

from datetime import datetime, timezone
from inspect import signature
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Collapsible, Markdown, Static

from rit.state.models import PRComment
from rit.ui.widgets import review_thread_card as review_thread_card_module
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.review_thread_card import ReviewThreadCard, ReviewThreadItem

ROOT = Path(__file__).parents[1]


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


def test_sorts_missing_and_aware_comment_dates() -> None:
    missing_date = PRComment.model_validate(
        {
            "databaseId": 1,
            "author": {"login": "alice", "avatarUrl": ""},
            "body": "Missing date",
            "path": "test.py",
            "line": 10,
        }
    )
    aware_date = make_comment("Aware date", comment_id=2)

    card = ReviewThreadCard(
        comments=[aware_date, missing_date],
        show_diff_hunk=False,
    )

    assert [comment.id for comment in card._comments] == [1, 2]


def test_keeps_already_ordered_comments_without_sorting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_comment("First", comment_id=1)
    second = make_comment("Second", comment_id=2)

    monkeypatch.setattr(
        review_thread_card_module,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("already ordered thread comments should not sort")
        ),
        raising=False,
    )

    card = ReviewThreadCard(
        comments=[first, second],
        show_diff_hunk=False,
    )

    assert card._comments == [first, second]


def test_swaps_two_out_of_order_comments_without_sorting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = make_comment("First", comment_id=1).model_copy(
        update={"created_at": datetime(2026, 2, 26, 10, tzinfo=timezone.utc)}
    )
    second = make_comment("Second", comment_id=2).model_copy(
        update={"created_at": datetime(2026, 2, 26, 11, tzinfo=timezone.utc)}
    )

    monkeypatch.setattr(
        review_thread_card_module,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("two thread comments should not call sorted")
        ),
        raising=False,
    )

    card = ReviewThreadCard(
        comments=[second, first],
        show_diff_hunk=False,
    )

    assert card._comments == [first, second]


def test_single_comment_thread_skips_timeline_order_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comment = make_comment("Only comment")
    monkeypatch.setattr(
        review_thread_card_module,
        "datetime_sort_key",
        lambda _created_at: (_ for _ in ()).throw(
            AssertionError("single-comment threads should not compute sort keys")
        ),
    )

    card = ReviewThreadCard(
        comments=[comment],
        show_diff_hunk=False,
    )

    assert card._comments == [comment]


def test_inline_thread_css_keeps_collapsed_card_frame() -> None:
    """Collapsed inline threads should still read as framed comment cards."""
    css = ReviewThreadItem.DEFAULT_CSS
    inline_block = css.split("ReviewThreadItem.--inline {", 1)[1].split("}", 1)[0]
    cursor_block = css.split("ReviewThreadItem.--inline.--cursor-line {", 1)[
        1
    ].split("}", 1)[0]

    assert "border: solid #363a4f;" in inline_block
    assert "border-top: none;" not in inline_block
    assert "border-left: blank;" not in inline_block
    assert "background:" not in cursor_block


def test_thread_comment_cards_keep_breathing_room_between_meta_and_body() -> None:
    """Thread cards should not compress comment text against its metadata."""
    css = CommentCard.DEFAULT_CSS
    thread_block = css.split("CommentCard.thread-comment {", 1)[1].split("}", 1)[0]
    reply_block = css.split("CommentCard.thread-reply {", 1)[1].split("}", 1)[0]
    header_block = css.split("CommentCard.thread-comment .comment-header,", 1)[
        1
    ].split("}", 1)[0]

    assert "padding: 1 1 1 1;" in thread_block
    assert "padding: 1 1 1 3;" in reply_block
    assert "margin: 0 0 1 0;" in header_block
    assert "margin: 0;" not in header_block


def test_pending_drafts_keep_the_shared_comment_card_surface() -> None:
    """Pending drafts should not look like a separate card system."""
    css = CommentCard.DEFAULT_CSS
    pending_block = css.split("CommentCard.pending-draft {", 1)[1].split("}", 1)[0]
    cursor_block = css.split("CommentCard.pending-draft.--cursor-line {", 1)[
        1
    ].split("}", 1)[0]

    assert "border:" not in pending_block
    assert "background:" not in pending_block
    assert "border-left: thick #8aadf4;" in cursor_block
    assert "tint:" not in cursor_block


def test_timeline_thread_contents_keep_padding_without_affecting_inline_threads() -> None:
    """PR Info thread bodies need inset while DiffView inline threads stay compact."""
    css = ReviewThreadItem.DEFAULT_CSS
    thread_contents_block = css.split("ReviewThreadItem.--thread > Contents {", 1)[
        1
    ].split("}", 1)[0]
    inline_contents_block = css.split("ReviewThreadItem.--inline > Contents {", 1)[
        1
    ].split("}", 1)[0]

    assert "padding: 1;" in thread_contents_block
    assert "padding: 0;" in inline_contents_block


def test_pr_info_review_threads_are_inset_without_affecting_inline_threads() -> None:
    """Timeline review threads should sit inside the parent review rhythm."""
    pr_info_css = (ROOT / "src/rit/ui/components/pr_info.tcss").read_text()
    pr_info_thread_block = pr_info_css.split("PRInfo ReviewThreadItem.--thread {", 1)[
        1
    ].split("}", 1)[0]
    inline_block = ReviewThreadItem.DEFAULT_CSS.split(
        "ReviewThreadItem.--inline {", 1
    )[1].split("}", 1)[0]

    assert "margin: 1 0 1 4;" in pr_info_thread_block
    assert "margin: 0;" in inline_block


def test_review_thread_diff_hunk_preview_streams_last_lines() -> None:
    class NoSplitDiffHunk(str):
        def split(self, *_args: object, **_kwargs: object) -> list[str]:
            raise AssertionError("diff hunk preview should not split all lines")

    diff_hunk = NoSplitDiffHunk(
        "\n".join(
            [
                "@@ -1,7 +1,7 @@",
                " line1",
                "-line2",
                "+line2 new",
                " line3",
                "-line4",
                "+line4 new",
            ]
        )
    )

    rendered = ReviewThreadCard._render_diff_hunk(diff_hunk)

    assert "@@ -1,7 +1,7 @@" not in rendered.plain
    assert "line1" not in rendered.plain
    assert "-line2" in rendered.plain
    assert "+line4 new" in rendered.plain


def test_comment_card_markdown_headings_create_section_breaks() -> None:
    """Markdown headings should separate sections without loosening every line."""
    css = CommentCard.DEFAULT_CSS
    body_text_block = css.split(
        "CommentCard .comment-content MarkdownParagraph,", 1
    )[1].split("}", 1)[0]
    heading_block = css.split("CommentCard .comment-content MarkdownH1,", 1)[1].split(
        "}", 1
    )[0]

    assert "MarkdownBulletList" in body_text_block
    assert "margin: 0;" in body_text_block
    assert "MarkdownH2" in heading_block
    assert "MarkdownH3" in heading_block
    assert "margin: 1 0 0 0;" in heading_block
    assert "margin: 0;" not in heading_block


def test_review_comment_rhythm_is_shared_across_nested_blocks() -> None:
    """Comment surfaces should use one block rhythm across cards, details, and hunks."""
    card_css = CommentCard.DEFAULT_CSS
    thread_css = ReviewThreadCard.DEFAULT_CSS
    pr_info_css = (ROOT / "src/rit/ui/components/pr_info.tcss").read_text()

    details_block = card_css.split("CommentCard Collapsible {", 1)[1].split("}", 1)[0]
    diff_hunk_block = thread_css.split("ReviewThreadCard .diff-hunk {", 1)[1].split(
        "}", 1
    )[0]

    assert "margin: 1 0;" in details_block
    assert "margin-bottom: 1;" in diff_hunk_block
    assert "PRInfo CommentCard Collapsible {" not in pr_info_css


def test_pr_info_does_not_redefine_shared_comment_card_surface() -> None:
    """PR Info should keep shared comment surfaces centralized in CommentCard."""
    pr_info_css = (ROOT / "src/rit/ui/components/pr_info.tcss").read_text()

    assert "PRInfo CommentCard.description-container {" not in pr_info_css
    assert "PRInfo CommentCard.comment-box {" not in pr_info_css
    assert "PRInfo CommentCard.description-container .comment-header" not in pr_info_css
    assert "PRInfo CommentCard.comment-box .comment-header" not in pr_info_css
    assert "PRInfo CommentCard .comment-content {" not in pr_info_css
    assert "PRInfo CommentCard Collapsible {" not in pr_info_css
    assert "PRInfo CommentCard CollapsibleTitle" not in pr_info_css
    assert "PRInfo CommentCard Collapsible > Contents" not in pr_info_css
    assert "PRInfo .thread-resolved .thread-header" not in pr_info_css
    assert "PRInfo CommentCard.description-container.--selected" in pr_info_css
    assert "PRInfo CommentCard.comment-box.--selected" in pr_info_css


def test_pr_info_does_not_keep_legacy_comment_style_hooks() -> None:
    """Old timeline comment classes should disappear after CommentCard unification."""
    pr_info_css = (ROOT / "src/rit/ui/components/pr_info.tcss").read_text()

    for selector in [
        ".comment-author",
        ".comment-time",
        ".review-state-approved",
        ".review-state-changes",
        ".review-state-commented",
        ".comments-section-title",
        ".resolved-indicator",
    ]:
        assert selector not in pr_info_css


def test_review_thread_card_api_does_not_expose_unused_variant() -> None:
    """Thread rendering should be driven by shared CommentCard options only."""
    assert "variant" not in signature(ReviewThreadCard).parameters


@pytest.mark.asyncio
async def test_compact_inline_preview_truncates_long_comment() -> None:
    """Compact inline variant should show short preview instead of full body."""
    long_body = "uv run rit https://github.com/lemonbase-tech/lemonbase/pull/19484 " * 8

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment(long_body)],
                compact=True,
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
async def test_non_compact_thread_card_mounts_markdown_content() -> None:
    """Non-compact thread cards should mount markdown body widgets."""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment("Hello **world**")],
                compact=False,
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        markdown_widgets = app.query(Markdown)
        assert len(markdown_widgets) >= 1


@pytest.mark.asyncio
async def test_thread_card_uses_shared_comment_cards() -> None:
    """Thread comments should render through the shared comment card surface."""
    root = make_comment("Root comment", comment_id=1)
    reply = make_comment("Reply comment", comment_id=2)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[reply, root],
                compact=False,
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        assert len(app.query("CommentCard.thread-comment")) == 1
        assert len(app.query("CommentCard.thread-reply")) == 1


@pytest.mark.asyncio
async def test_thread_card_left_aligns_markdown_h1() -> None:
    """Markdown headings in thread cards should not use Textual's centered H1."""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield ReviewThreadCard(
                comments=[make_comment("# Test")],
                compact=False,
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        h1 = app.query_one("MarkdownH1")
        assert h1.styles.content_align == ("left", "middle")


@pytest.mark.asyncio
async def test_thread_card_mounts_nested_details_without_mount_error() -> None:
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
                show_diff_hunk=False,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        collapsibles = app.query(Collapsible)
        assert len(collapsibles) >= 2
