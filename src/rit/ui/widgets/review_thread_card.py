"""Review thread card widget (shared by timeline and inline diff)."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Literal

from rich.text import Text
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Collapsible, Static

from rit.state.models import PRComment
from rit.ui.collapsible_markdown import mount_markdown_with_details
from rit.ui.icons import get_file_icon


class ReviewThreadCard(Vertical):
    DEFAULT_CSS = """
    ReviewThreadCard {
        height: auto;
        width: 1fr;
    }

    ReviewThreadCard > Vertical {
        height: auto;
    }

    ReviewThreadCard .diff-hunk {
        background: #181926;
        padding: 0 1;
        color: #6e738d;
        overflow: hidden;
        height: auto;
    }

    ReviewThreadCard .thread-comment {
        padding: 0 1;
        background: #1e2030;
        height: auto;
    }

    ReviewThreadCard .thread-reply {
        padding: 0 1 0 3;
        background: #24273a;
        height: auto;
    }

    ReviewThreadCard .comment-header {
        height: 1;
        margin: 0;
    }

    ReviewThreadCard .comment-content {
        height: auto;
    }

    ReviewThreadCard .comment-content Markdown {
        margin: 0;
        padding: 0;
    }

    ReviewThreadCard .comment-content MarkdownParagraph {
        margin: 0;
    }

    ReviewThreadCard .comment-content MarkdownFence {
        margin: 0;
        padding: 0 1;
    }

    ReviewThreadCard .comment-content MarkdownBulletList {
        margin: 0;
    }

    ReviewThreadCard .comment-content MarkdownOrderedList {
        margin: 0;
    }

    ReviewThreadCard .comment-content MarkdownBlockQuote {
        margin: 0;
    }

    ReviewThreadCard .comment-content MarkdownH1,
    ReviewThreadCard .comment-content MarkdownH2,
    ReviewThreadCard .comment-content MarkdownH3 {
        margin: 0;
    }

    ReviewThreadCard .comment-content MarkdownH1 {
        content-align: left middle;
    }

    /* Nested markdown <details> style (shared across timeline/diff) */
    ReviewThreadCard Collapsible {
        height: auto;
        margin: 0;
        background: #181926;
        border: solid #363a4f;
        padding: 0;
    }

    ReviewThreadCard CollapsibleTitle {
        color: #8aadf4;
        padding: 0 1;
    }

    ReviewThreadCard CollapsibleTitle:hover {
        background: #363a4f;
    }

    ReviewThreadCard Collapsible > Contents {
        height: auto;
        padding: 0 1;
    }

    ReviewThreadCard .details-content {
        height: auto;
    }

    ReviewThreadCard .inline-comment-preview {
        color: $foreground;
        height: auto;
        margin-left: 1;
        text-wrap: nowrap;
        overflow: hidden;
    }
    """

    def __init__(
        self,
        comments: list[PRComment],
        *,
        diff_hunk: str = "",
        compact: bool = False,
        variant: Literal["timeline", "inline"] = "timeline",
        show_diff_hunk: bool = True,
        preview_max_lines: int = 2,
        preview_max_chars: int = 120,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.styles.height = "auto"
        self._comments = sorted(comments, key=lambda comment: comment.created_at)
        self._diff_hunk = diff_hunk
        self._compact = compact
        self._variant = variant
        self._show_diff_hunk = show_diff_hunk
        self._preview_max_lines = preview_max_lines
        self._preview_max_chars = preview_max_chars
        self._pending_markdown_mounts: list[tuple[Vertical, str]] = []

    def on_mount(self) -> None:
        if self._show_diff_hunk and self._diff_hunk:
            self.mount(
                Static(self._render_diff_hunk(self._diff_hunk), classes="diff-hunk")
            )

        for index, comment in enumerate(self._comments):
            is_reply = index > 0
            comment_box = self._build_comment_box(comment, is_reply=is_reply)
            self.mount(comment_box)

        if self._pending_markdown_mounts:
            self.call_after_refresh(self._mount_pending_markdown)

    def _build_comment_box(self, comment: PRComment, *, is_reply: bool) -> Vertical:
        box_class = self._reply_class if is_reply else self._root_class

        if self._compact:
            preview = self._build_preview(comment.body or "")
            return Vertical(
                Static(
                    self._format_comment_meta(comment, is_reply=is_reply),
                    classes=self._meta_class,
                ),
                Static(preview, classes="inline-comment-preview", markup=False),
                classes=box_class,
            )

        content_container = Vertical(classes=self._content_class)
        self._pending_markdown_mounts.append(
            (content_container, comment.body or "*No content*")
        )

        return Vertical(
            Static(
                self._format_comment_meta(comment, is_reply=is_reply),
                classes=self._meta_class,
            ),
            content_container,
            classes=box_class,
        )

    def _mount_pending_markdown(self) -> None:
        for container, body in self._pending_markdown_mounts:
            mount_markdown_with_details(container, body)
        self._pending_markdown_mounts.clear()

    @property
    def _root_class(self) -> str:
        return "thread-comment"

    @property
    def _reply_class(self) -> str:
        return "thread-reply"

    @property
    def _meta_class(self) -> str:
        return "comment-header"

    @property
    def _content_class(self) -> str:
        return "comment-content"

    def _format_comment_meta(self, comment: PRComment, *, is_reply: bool) -> str:
        author = comment.user.login if comment.user else "unknown"
        formatted = self._format_relative_time(comment.created_at)
        if is_reply:
            return f"[#6e738d]↳[/] [bold]{author}[/] {formatted}"
        return f"[bold]{author}[/] {formatted}"

    def _build_preview(self, body: str) -> str:
        cleaned_lines = [
            self._sanitize_preview_line(line) for line in body.splitlines()
        ]
        cleaned_lines = [line for line in cleaned_lines if line]
        if not cleaned_lines:
            return "(No content)"

        preview = " ".join(cleaned_lines[: self._preview_max_lines])

        truncated = False
        if len(cleaned_lines) > self._preview_max_lines:
            truncated = True

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

        # Remove HTML-like tags such as <details>/<summary>.
        stripped = re.sub(r"<[^>]+>", " ", stripped)

        # Remove common markdown markers.
        stripped = stripped.replace("```", " ").replace("`", "")
        stripped = stripped.replace("**", "").replace("__", "")
        stripped = stripped.lstrip("-*># ")

        # Collapse whitespace
        stripped = " ".join(stripped.split())
        return stripped

    @staticmethod
    def _render_diff_hunk(diff_hunk: str) -> Text:
        diff_text = Text()
        hunk_lines = diff_hunk.split("\n")
        display_lines = hunk_lines[-5:] if len(hunk_lines) > 5 else hunk_lines

        for line in display_lines:
            if line.startswith("+") and not line.startswith("+++"):
                diff_text.append(f"  {line}\n", style="#a6da95 on #2d4f3c")
            elif line.startswith("-") and not line.startswith("---"):
                diff_text.append(f"  {line}\n", style="#ed8796 on #4f2d3c")
            elif line.startswith("@@"):
                diff_text.append(f"  {line}\n", style="#6e738d")
            else:
                diff_text.append(f"  {line}\n", style="#6e738d")

        return diff_text

    @staticmethod
    def _format_relative_time(dt: datetime) -> str:
        if dt == datetime.min:
            return ""

        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        diff = now - dt
        seconds = diff.total_seconds()

        if seconds < 60:
            return "[#6e738d]just now[/]"
        if seconds < 3600:
            return f"[#6e738d]{int(seconds / 60)}m ago[/]"
        if seconds < 86400:
            return f"[#6e738d]{int(seconds / 3600)}h ago[/]"
        if seconds < 604800:
            return f"[#6e738d]{int(seconds / 86400)}d ago[/]"
        return f"[#6e738d]{dt.strftime('%b %d, %Y')}[/]"


class ReviewThreadItem(Collapsible):
    DEFAULT_CSS = """
    ReviewThreadItem {
        height: auto;
    }

    ReviewThreadItem.--thread {
        height: auto;
        background: #181926;
        border: solid #363a4f;
        padding: 0;
    }

    ReviewThreadItem.--thread > Contents {
        height: auto;
        padding: 0;
    }

    ReviewThreadItem.--thread CollapsibleTitle {
        color: #8aadf4;
        padding: 0 1;
    }

    ReviewThreadItem.--thread CollapsibleTitle:hover {
        background: #363a4f;
    }

    ReviewThreadItem.--thread.--resolved {
        border: solid #363a4f;
    }

    ReviewThreadItem.--thread.--resolved CollapsibleTitle {
        color: #a6da95;
    }

    ReviewThreadItem .thread-container {
        height: auto;
        background: #1e2030;
    }

    ReviewThreadItem .thread-header {
        background: #363a4f;
        padding: 0 1;
        color: #8aadf4;
        height: auto;
    }

    ReviewThreadItem .thread-resolved .thread-header {
        background: #2d4f3c;
    }

    /* Inline DiffView variant (activated by --inline class) */

    ReviewThreadItem.--inline {
        margin: 0;
        padding: 0;
        border-top: none;
        border-left: blank;
        background: #1e2030;
    }

    ReviewThreadItem.--inline.--cursor-line {
        border-left: thick #8aadf4;
        background: #24273a;
    }

    ReviewThreadItem.--inline.--resolved.--cursor-line {
        border-left: thick #a6da95;
        background: #1e2030;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        path: str,
        line: int | None,
        comments: list[PRComment],
        diff_hunk: str = "",
        is_resolved: bool = False,
        compact: bool = False,
        show_diff_hunk: bool = True,
        show_path_header: bool = True,
        collapsed: bool = False,
        classes: str | None = None,
        id: str | None = None,
    ) -> None:
        self.path = path
        self.line = line
        self.is_resolved = is_resolved
        self._show_path_header = show_path_header

        children: list[Widget] = []

        if show_path_header:
            self._header_widget = Static(
                self._build_path_header_text(),
                classes="thread-header",
            )
            children.append(self._header_widget)
        else:
            self._header_widget = None

        children.append(
            ReviewThreadCard(
                comments=comments,
                diff_hunk=diff_hunk,
                compact=compact,
                variant="timeline",
                show_diff_hunk=show_diff_hunk,
            )
        )

        self._thread_container = Vertical(*children, classes="thread-container")
        if is_resolved:
            self._thread_container.add_class("thread-resolved")

        super().__init__(
            self._thread_container,
            title=title,
            collapsed=collapsed,
            classes=classes,
            id=id,
        )

    def set_resolved(self, is_resolved: bool, *, title: str | None = None) -> None:
        self.is_resolved = is_resolved

        if title is not None:
            self.title = title

        if is_resolved:
            self.add_class("--resolved")
            self._thread_container.add_class("thread-resolved")
        else:
            self.remove_class("--resolved")
            self._thread_container.remove_class("thread-resolved")

        if self._header_widget is not None:
            self._header_widget.update(self._build_path_header_text())

    def _build_path_header_text(self) -> str:
        line_info = f":{self.line}" if self.line else ""
        file_icon = get_file_icon(self.path)
        resolved_indicator = "[#a6da95]✓[/] " if self.is_resolved else ""
        return f"{resolved_indicator}[bold]{file_icon} {self.path}{line_info}[/]"
