"""Shared collapsible markdown helpers for rendering <details> blocks."""

import re
from dataclasses import dataclass

from textual.containers import Vertical
from textual.widgets import Markdown, Collapsible

# Threshold for lazy loading large content (in characters)
LAZY_LOAD_THRESHOLD = 2000


@dataclass
class DetailsBlock:

    summary: str
    content: str


@dataclass
class MarkdownPart:

    content: str
    is_details: bool = False
    details: DetailsBlock | None = None


def _find_matching_close_tag(text: str, start_pos: int) -> int:
    depth = 1
    pos = start_pos
    text_lower = text.lower()

    while pos < len(text) and depth > 0:
        next_open = text_lower.find("<details", pos)
        next_close = text_lower.find("</details>", pos)

        if next_close == -1:
            return -1

        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open + len("<details")
        else:
            depth -= 1
            if depth == 0:
                return next_close
            pos = next_close + len("</details>")

    return -1


def parse_details_blocks(body: str) -> list[MarkdownPart]:
    if not body:
        return []

    parts: list[MarkdownPart] = []
    pos = 0
    body_lower = body.lower()

    while pos < len(body):
        details_match = re.search(r"<details\s*>", body_lower[pos:], re.IGNORECASE)
        if not details_match:
            remaining = body[pos:].strip()
            if remaining:
                parts.append(MarkdownPart(content=remaining))
            break

        details_start = pos + details_match.start()

        before = body[pos:details_start].strip()
        if before:
            parts.append(MarkdownPart(content=before))

        tag_end = pos + details_match.end()
        summary_match = re.search(
            r"<summary>(.*?)</summary>", body[tag_end:], re.IGNORECASE | re.DOTALL
        )

        if not summary_match:
            pos = tag_end
            continue

        summary = summary_match.group(1).strip()
        content_start = tag_end + summary_match.end()

        close_pos = _find_matching_close_tag(body, content_start)
        if close_pos == -1:
            pos = content_start
            continue

        content = body[content_start:close_pos].strip()

        parts.append(
            MarkdownPart(
                content="",
                is_details=True,
                details=DetailsBlock(summary=summary, content=content),
            )
        )

        pos = close_pos + len("</details>")

    if not parts and body.strip():
        parts.append(MarkdownPart(content=body.strip()))

    return parts


class _BaseDetailsCollapsible(Collapsible):

    def __init__(
        self,
        title: str,
        content: str,
        *,
        lazy_threshold: int = LAZY_LOAD_THRESHOLD,
        **kwargs,
    ) -> None:
        self._inner_container = Vertical(classes="details-content")
        super().__init__(self._inner_container, title=title, collapsed=True, **kwargs)
        self._content = content
        self._lazy_threshold = lazy_threshold
        self._content_loaded = False

    def _load_content(self) -> None:
        if self._content_loaded:
            return

        self._content_loaded = True
        mount_markdown_with_details(
            self._inner_container,
            self._content,
            lazy_threshold=self._lazy_threshold,
        )


class EagerCollapsible(_BaseDetailsCollapsible):

    def on_mount(self) -> None:
        self.call_after_refresh(self._load_content)


class LazyCollapsible(_BaseDetailsCollapsible):

    def __init__(
        self,
        title: str,
        lazy_content: str,
        **kwargs,
    ) -> None:
        super().__init__(title=title, content=lazy_content, **kwargs)

    def _on_collapsible_expanded(self, event: Collapsible.Expanded) -> None:
        if event.collapsible is self and not self._content_loaded:
            self._load_content()


def mount_markdown_with_details(
    container: Vertical,
    body: str,
    *,
    lazy_threshold: int = LAZY_LOAD_THRESHOLD,
) -> None:
    if not body:
        return

    parts = parse_details_blocks(body)

    for part in parts:
        if part.is_details and part.details:
            content = part.details.content

            if len(content) > lazy_threshold:
                collapsible = LazyCollapsible(
                    title=part.details.summary,
                    lazy_content=content,
                    lazy_threshold=lazy_threshold,
                )
                container.mount(collapsible)
            else:
                collapsible = EagerCollapsible(
                    title=part.details.summary,
                    content=content,
                    lazy_threshold=lazy_threshold,
                )
                container.mount(collapsible)
        else:
            if part.content:
                md = Markdown(part.content)
                container.mount(md)
