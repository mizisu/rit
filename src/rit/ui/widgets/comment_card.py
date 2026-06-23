"""Shared comment card surface for PR timeline and review threads."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from rit.ui.collapsible_markdown import mount_markdown_with_details

__all__ = (
    "BODY_PREVIEW_RETIRE_DELAY",
    "CommentCard",
)


BODY_PREVIEW_RETIRE_DELAY = 0.8


class CommentCard(Vertical):
    """Shared rendered surface for authored PR comments."""

    DEFAULT_CSS = """
    CommentCard {
        height: auto;
        width: 1fr;
        background: #1e2030;
        border: solid #363a4f;
        padding: 1;
        margin-bottom: 1;
    }

    CommentCard.thread-comment {
        border: none;
        margin-bottom: 0;
        padding: 1 1 1 1;
        background: #1e2030;
    }

    CommentCard.thread-reply {
        border: none;
        margin-bottom: 0;
        padding: 1 1 1 3;
        background: #24273a;
    }

    CommentCard.pending-draft {
        margin: 0;
    }

    CommentCard.timeline-loading {
        background: #1a1c29;
        border: solid #2f3348;
        color: #6e738d;
    }

    CommentCard.timeline-loading .comment-header {
        color: #8aadf4;
        text-style: bold;
    }

    CommentCard.pending-draft.--cursor-line {
        border-left: thick #8aadf4;
    }

    CommentCard.pending-draft .comment-header {
        color: $warning;
        text-style: bold;
    }

    CommentCard.review-submit-pending-item {
        margin: 0 0 1 0;
    }

    CommentCard .comment-header {
        height: 1;
        margin: 0 0 1 0;
    }

    CommentCard.thread-comment .comment-header,
    CommentCard.thread-reply .comment-header {
        margin: 0 0 1 0;
    }

    CommentCard .comment-content {
        height: auto;
    }

    CommentCard .comment-body-preview {
        height: auto;
        color: #cad3f5;
    }

    CommentCard .comment-content Markdown {
        margin: 0;
        padding: 0;
    }

    CommentCard .comment-content MarkdownParagraph,
    CommentCard .comment-content MarkdownFence,
    CommentCard .comment-content MarkdownBulletList,
    CommentCard .comment-content MarkdownOrderedList,
    CommentCard .comment-content MarkdownBlockQuote {
        margin: 0;
    }

    CommentCard .comment-content MarkdownH1,
    CommentCard .comment-content MarkdownH2,
    CommentCard .comment-content MarkdownH3 {
        margin: 1 0 0 0;
    }

    CommentCard .comment-content MarkdownFence {
        padding: 0 1;
    }

    CommentCard .comment-content MarkdownH1 {
        content-align: left middle;
    }

    CommentCard Collapsible {
        height: auto;
        margin: 1 0;
        background: #181926;
        border: solid #363a4f;
        padding: 0;
    }

    CommentCard CollapsibleTitle {
        color: #8aadf4;
        padding: 0 1;
    }

    CommentCard CollapsibleTitle:hover {
        background: #363a4f;
    }

    CommentCard Collapsible > Contents {
        height: auto;
        padding: 0 1;
    }

    CommentCard .details-content {
        height: auto;
    }

    CommentCard .inline-comment-preview {
        color: $foreground;
        height: auto;
        margin-left: 1;
        text-wrap: nowrap;
        overflow: hidden;
    }
    """

    def __init__(
        self,
        header: str,
        body: str,
        *,
        compact: bool = False,
        preview_max_lines: int = 2,
        preview_max_chars: int = 120,
        markdown_base_url: str | None = None,
        body_mount_delay: float = 0.0,
        header_id: str | None = None,
        content_id: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.styles.height = "auto"
        self._header = header
        self._body = body
        self._compact = compact
        self._preview_max_lines = preview_max_lines
        self._preview_max_chars = preview_max_chars
        self._markdown_base_url = markdown_base_url
        self._body_mount_delay = body_mount_delay
        self._is_ready = False
        self._body_mount_generation = 0
        self._header_widget = Static(header, classes="comment-header", id=header_id)
        self._content_container = Vertical(
            id=content_id,
            classes="comment-content",
        )
        self._preview_widget = Static(
            "",
            classes="inline-comment-preview",
            markup=False,
        )

    def compose(self) -> ComposeResult:
        yield self._header_widget
        if self._compact:
            self._preview_widget.update(self._build_preview(self._body))
            yield self._preview_widget
        else:
            yield self._content_container

    def on_mount(self) -> None:
        self._is_ready = True
        if not self._compact:
            self._schedule_body_mount()

    def set_content(
        self,
        header: str,
        body: str,
        *,
        markdown_base_url: str | None = None,
    ) -> None:
        """Replace the card header and markdown body."""
        self._header = header
        self._body = body
        self._markdown_base_url = markdown_base_url
        self._header_widget.update(header)

        if self._compact:
            self._preview_widget.update(self._build_preview(body))
        elif self._is_ready:
            self._schedule_body_mount()

    def _schedule_body_mount(self) -> None:
        self._body_mount_generation += 1
        generation = self._body_mount_generation
        self._show_body_preview()
        if self.has_class("timeline-loading"):
            return
        if self._body_mount_delay > 0:
            self.set_timer(
                self._body_mount_delay,
                lambda: self._mount_body(generation),
            )
        else:
            self.call_after_refresh(lambda: self._mount_body(generation))

    def _show_body_preview(self) -> None:
        self._content_container.remove_children()
        preview = self._build_preview(self._body)
        if preview:
            self._content_container.mount(
                Static(
                    preview,
                    classes="comment-body-preview",
                    markup=False,
                )
            )

    def _mount_body(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._body_mount_generation:
            return
        self._remove_rendered_body_widgets()
        mount_markdown_with_details(
            self._content_container,
            self._body or "*No content*",
            base_url=self._markdown_base_url,
        )
        self.set_timer(
            BODY_PREVIEW_RETIRE_DELAY,
            lambda: self._retire_body_preview(generation),
        )

    def _remove_rendered_body_widgets(self) -> None:
        for child in list(self._content_container.children):
            if child.has_class("comment-body-preview"):
                continue
            child.remove()

    def _retire_body_preview(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._body_mount_generation:
            return
        for child in list(self._content_container.children):
            if child.has_class("comment-body-preview"):
                child.remove()

    def _build_preview(self, body: str) -> str:
        cleaned_lines = [
            self._sanitize_preview_line(line) for line in body.splitlines()
        ]
        cleaned_lines = [line for line in cleaned_lines if line]
        if not cleaned_lines:
            return "(No content)"

        preview = " ".join(cleaned_lines[: self._preview_max_lines])
        truncated = len(cleaned_lines) > self._preview_max_lines

        if len(preview) > self._preview_max_chars:
            preview = preview[: self._preview_max_chars].rstrip()
            truncated = True

        if truncated and not preview.endswith("…"):
            preview += " …"

        return preview

    @staticmethod
    def _sanitize_preview_line(line: str) -> str:
        stripped = line.strip()
        if not stripped:
            return ""

        stripped = re.sub(r"<!--.*?-->", " ", stripped)
        stripped = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", stripped)
        stripped = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", stripped)
        stripped = re.sub(r"\[!([A-Z]+)\]", r"\1", stripped)
        stripped = re.sub(r"\[[ xX]\]", " ", stripped)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        stripped = stripped.replace("```", " ").replace("`", "")
        stripped = stripped.replace("**", "").replace("__", "")
        stripped = stripped.lstrip("-*># ")
        return " ".join(stripped.split())
