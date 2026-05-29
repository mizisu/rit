"""Shared collapsible markdown helpers for rendering <details> blocks."""

import re
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Markdown, Collapsible, Static

from rit.ui.markdown_images import ImageFetcher, mount_markdown_image_parts
from rit.ui.messages import Flash

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


@dataclass
class MarkdownCodePart:
    content: str
    is_code: bool = False
    language: str = ""


class CopyableCodeBlock(Vertical):
    """Markdown code fence with a clickable copy affordance."""

    DEFAULT_CSS = """
    CopyableCodeBlock {
        width: 1fr;
        height: auto;
        margin: 1 0;
        border: solid #363a4f;
        background: #181926;
    }

    CopyableCodeBlock .code-copy-toolbar {
        layout: horizontal;
        width: 1fr;
        height: 1;
        padding: 0 1;
        background: #24273a;
    }

    CopyableCodeBlock .code-language {
        width: 1fr;
        height: 1;
        color: #6e738d;
        text-wrap: nowrap;
        overflow: hidden;
    }

    CopyableCodeBlock Button.code-copy-button {
        min-width: 3;
        width: 3;
        height: 1;
        margin: 0;
        padding: 0;
        border: none;
        background: #363a4f;
        color: #cad3f5;
        content-align: center middle;
    }

    CopyableCodeBlock Button.code-copy-button:hover {
        background: #494d64;
    }

    CopyableCodeBlock Markdown {
        width: 1fr;
        margin: 0;
        padding: 0;
    }

    CopyableCodeBlock MarkdownFence {
        width: 1fr;
        margin: 0;
        padding: 0;
        overflow: scroll hidden;
    }

    CopyableCodeBlock MarkdownFence > Label {
        padding: 1 2;
        text-wrap: nowrap;
    }
    """

    def __init__(self, code: str, *, language: str = "") -> None:
        super().__init__()
        self._code = code
        self._language = language.strip()
        self._copy_button = Button(
            "⧉",
            compact=True,
            tooltip="Copy code",
            classes="code-copy-button",
        )

    def compose(self) -> ComposeResult:
        with Horizontal(classes="code-copy-toolbar"):
            yield Static(self._language, classes="code-language", markup=False)
            yield self._copy_button
        yield Markdown(self._markdown_source())

    @on(Button.Pressed, ".code-copy-button")
    def on_copy_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.copy_code()

    def copy_code(self) -> None:
        self.app.copy_to_clipboard(self._code)
        self.post_message(Flash("Code block copied", style="success", duration=2.0))

    def _markdown_source(self) -> str:
        fence = "`" * max(3, self._longest_backtick_run() + 1)
        language = self._language
        return f"{fence}{language}\n{self._code}\n{fence}"

    def _longest_backtick_run(self) -> int:
        runs = re.findall(r"`+", self._code)
        return max((len(run) for run in runs), default=0)


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


def parse_fenced_code_blocks(body: str) -> list[MarkdownCodePart]:
    if not body:
        return []

    parts: list[MarkdownCodePart] = []
    text_lines: list[str] = []
    lines = body.splitlines(keepends=True)
    index = 0

    while index < len(lines):
        opener = _match_fence_opener(lines[index])
        if opener is None:
            text_lines.append(lines[index])
            index += 1
            continue

        fence_char, fence_len, language = opener
        code_lines: list[str] = []
        close_index = index + 1
        while close_index < len(lines) and not _is_fence_closer(
            lines[close_index], fence_char=fence_char, fence_len=fence_len
        ):
            code_lines.append(lines[close_index])
            close_index += 1

        if close_index >= len(lines):
            text_lines.extend(lines[index:])
            break

        _append_markdown_text_part(parts, text_lines)
        parts.append(
            MarkdownCodePart(
                content=_trim_trailing_line_break("".join(code_lines)),
                is_code=True,
                language=language,
            )
        )
        index = close_index + 1

    _append_markdown_text_part(parts, text_lines)
    return parts


def _append_markdown_text_part(
    parts: list[MarkdownCodePart], text_lines: list[str]
) -> None:
    content = "".join(text_lines).strip()
    text_lines.clear()
    if content:
        parts.append(MarkdownCodePart(content=content))


def _match_fence_opener(line: str) -> tuple[str, int, str] | None:
    match = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})([^\r\n]*)\r?\n?$", line)
    if match is None:
        return None
    fence = match.group(1)
    info = match.group(2).strip()
    return fence[0], len(fence), info.split(maxsplit=1)[0] if info else ""


def _is_fence_closer(line: str, *, fence_char: str, fence_len: int) -> bool:
    pattern = rf"^[ \t]{{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*\r?\n?$"
    return re.match(pattern, line) is not None


def _trim_trailing_line_break(text: str) -> str:
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n") or text.endswith("\r"):
        return text[:-1]
    return text


class _BaseDetailsCollapsible(Collapsible):
    def __init__(
        self,
        title: str,
        content: str,
        *,
        lazy_threshold: int = LAZY_LOAD_THRESHOLD,
        base_url: str | None = None,
        image_fetcher: ImageFetcher | None = None,
        **kwargs,
    ) -> None:
        self._inner_container = Vertical(classes="details-content")
        super().__init__(self._inner_container, title=title, collapsed=True, **kwargs)
        self._content = content
        self._lazy_threshold = lazy_threshold
        self._base_url = base_url
        self._image_fetcher = image_fetcher
        self._content_loaded = False

    def _load_content(self) -> None:
        if self._content_loaded:
            return

        self._content_loaded = True
        mount_markdown_with_details(
            self._inner_container,
            self._content,
            lazy_threshold=self._lazy_threshold,
            base_url=self._base_url,
            image_fetcher=self._image_fetcher,
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
    base_url: str | None = None,
    image_fetcher: ImageFetcher | None = None,
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
                    base_url=base_url,
                    image_fetcher=image_fetcher,
                )
                container.mount(collapsible)
            else:
                collapsible = EagerCollapsible(
                    title=part.details.summary,
                    content=content,
                    lazy_threshold=lazy_threshold,
                    base_url=base_url,
                    image_fetcher=image_fetcher,
                )
                container.mount(collapsible)
        else:
            mount_markdown_code_parts(
                container,
                part.content,
                base_url=base_url,
                image_fetcher=image_fetcher,
            )


def mount_markdown_code_parts(
    container: Vertical,
    body: str,
    *,
    base_url: str | None = None,
    image_fetcher: ImageFetcher | None = None,
) -> None:
    for part in parse_fenced_code_blocks(body):
        if part.is_code:
            container.mount(CopyableCodeBlock(part.content, language=part.language))
        elif part.content:
            mount_markdown_image_parts(
                container,
                part.content,
                base_url=base_url,
                image_fetcher=image_fetcher,
            )
