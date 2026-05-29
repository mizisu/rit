"""Markdown image parsing and inline terminal image rendering."""

from __future__ import annotations

import asyncio
import base64
import binascii
import os
import re
import subprocess
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from html import unescape as html_unescape
from html.parser import HTMLParser
from io import BytesIO
from json import dumps as json_dumps
from urllib.error import HTTPError, URLError
from urllib.parse import unquote_to_bytes, urljoin, urlparse
from urllib.request import Request, urlopen

from PIL import Image as PILImage
from PIL import UnidentifiedImageError
from rich.markup import escape
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage as TerminalImage

from rit.ui.terminal_graphics import (
    configure_terminal_graphics,
    terminal_graphics_status_message,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_FETCH_TIMEOUT_SECONDS = 10
MAX_INLINE_IMAGE_WIDTH_CELLS = 88
MAX_INLINE_IMAGE_HEIGHT_CELLS = 24

ImageFetcher = Callable[[str], Awaitable[bytes]]


@dataclass(frozen=True)
class MarkdownImageRef:
    """A markdown image reference."""

    alt: str
    src: str
    title: str = ""
    github_context: str = ""

    def resolve(self, base_url: str | None) -> MarkdownImageRef:
        github_context = _github_context_from_url(base_url)
        if not base_url or _has_url_scheme(self.src):
            return MarkdownImageRef(
                self.alt,
                self.src,
                self.title,
                github_context=github_context,
            )
        if self.src.startswith("//"):
            return MarkdownImageRef(
                self.alt,
                f"https:{self.src}",
                self.title,
                github_context=github_context,
            )
        return MarkdownImageRef(
            self.alt,
            urljoin(base_url, self.src),
            self.title,
            github_context=github_context,
        )


@dataclass(frozen=True)
class MarkdownImagePart:
    """A markdown text or image segment."""

    content: str = ""
    image: MarkdownImageRef | None = None

    @property
    def is_image(self) -> bool:
        return self.image is not None


class ImageViewerScreen(ModalScreen[None]):
    """Large inline image viewer."""

    DEFAULT_CSS = """
    ImageViewerScreen {
        align: center middle;
    }

    #image-viewer-dialog {
        width: 96%;
        height: 96%;
        background: $surface;
        border: round $primary;
        padding: 1 2;
    }

    #image-viewer-title {
        height: auto;
        margin-bottom: 1;
        text-style: bold;
    }

    #image-viewer-status {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }

    #image-viewer-body {
        height: 1fr;
        width: 1fr;
        align: center middle;
        overflow: hidden;
    }

    #image-viewer-body .markdown-terminal-image {
        width: auto;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def __init__(
        self,
        image_ref: MarkdownImageRef,
        *,
        image: PILImage.Image | None = None,
        fetcher: ImageFetcher | None = None,
    ) -> None:
        super().__init__()
        self._image_ref = image_ref
        self._image = image
        self._fetcher = fetcher

    def compose(self) -> ComposeResult:
        with Vertical(id="image-viewer-dialog"):
            yield Static(self._title_text(), id="image-viewer-title")
            yield Static("Loading image…", id="image-viewer-status")
            yield Vertical(id="image-viewer-body")

    def on_mount(self) -> None:
        configure_terminal_graphics()
        if status_message := terminal_graphics_status_message():
            self.query_one("#image-viewer-status", Static).update(status_message)
            return
        if self._image is not None:
            self.call_after_refresh(self._display_loaded_image)
            return
        self.run_worker(self._load_image(), name=f"image-viewer-{id(self)}")

    async def _load_image(self) -> None:
        try:
            if self._fetcher is None:
                data = await fetch_image_bytes_async(
                    self._image_ref.src,
                    github_context=self._image_ref.github_context,
                )
            else:
                data = await self._fetcher(self._image_ref.src)
            self._image = await asyncio.to_thread(_decode_image, data)
        except Exception as error:
            self.query_one("#image-viewer-status", Static).update(
                escape(_format_image_error(error))
            )
            return
        await self._display_image()

    def _display_loaded_image(self) -> None:
        self.run_worker(self._display_image(), name=f"image-viewer-display-{id(self)}")

    async def _display_image(self) -> None:
        if self._image is None:
            return
        body = self.query_one("#image-viewer-body", Vertical)
        image_widget = TerminalImage(self._image, classes="markdown-terminal-image")
        self._size_viewer_image_widget(image_widget, self._image, body)
        await body.remove_children()
        await body.mount(image_widget)
        self.query_one("#image-viewer-status", Static).update(
            f"{self._image.width}×{self._image.height}px • Esc to close"
        )

    def _size_viewer_image_widget(
        self,
        image_widget: TerminalImage,
        image: PILImage.Image,
        body: Vertical,
    ) -> None:
        available_width = max(1, body.size.width or self.app.size.width - 6)
        available_height = max(1, body.size.height or self.app.size.height - 8)
        cell_size = get_cell_size()
        natural_width = max(1, round(image.width / cell_size.width))
        target_width = min(available_width, natural_width)
        target_pixel_width = target_width * cell_size.width
        target_height = max(
            1,
            round((image.height / image.width) * target_pixel_width / cell_size.height),
        )

        if target_height > available_height:
            target_height = available_height
            target_pixel_height = target_height * cell_size.height
            target_width = max(
                1,
                round(
                    (image.width / image.height) * target_pixel_height / cell_size.width
                ),
            )
            target_width = min(target_width, available_width, natural_width)

        image_widget.styles.width = target_width
        image_widget.styles.height = target_height

    def _title_text(self) -> str:
        label = (
            self._image_ref.alt
            or self._image_ref.title
            or _filename_from_url(self._image_ref.src)
        )
        return f"🖼 {escape(label or 'image')}"

    def action_close(self) -> None:
        self.dismiss(None)


class MarkdownImageBlock(Vertical):
    """Inline terminal image block for markdown images."""

    DEFAULT_CSS = """
    MarkdownImageBlock {
        height: auto;
        margin: 1 0;
        border: solid #363a4f;
        background: #181926;
    }

    MarkdownImageBlock .markdown-image-header {
        height: auto;
        padding: 0 1;
        background: #24273a;
        color: #cad3f5;
    }

    MarkdownImageBlock .markdown-image-header:hover {
        background: #363a4f;
    }

    MarkdownImageBlock .markdown-image-body {
        height: auto;
        padding: 0 1 1 1;
    }

    MarkdownImageBlock .markdown-image-status {
        height: auto;
        color: #6e738d;
    }

    MarkdownImageBlock .markdown-terminal-image {
        width: 100%;
        height: auto;
    }
    """

    def __init__(
        self,
        image: MarkdownImageRef,
        *,
        fetcher: ImageFetcher | None = None,
    ) -> None:
        super().__init__()
        self.image = image
        self._fetcher = fetcher
        self._header = Static(self._header_text(), classes="markdown-image-header")
        self._body = Vertical(classes="markdown-image-body")
        self._status = Static(
            "Loading preview in background…",
            classes="markdown-image-status",
        )
        self._loaded = False
        self._loading = False
        self._pil_image: PILImage.Image | None = None

    def compose(self) -> ComposeResult:
        yield self._header
        with self._body:
            yield self._status

    def on_mount(self) -> None:
        self.call_after_refresh(self.load_image)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.open_viewer()

    def open_viewer(self) -> None:
        self.app.push_screen(
            ImageViewerScreen(
                self.image,
                image=self._pil_image,
                fetcher=self._fetcher,
            )
        )

    def load_image(self) -> None:
        if self._loaded or self._loading:
            return
        if not _is_fetchable_image_source(self.image.src):
            self._show_status("Unsupported image source.")
            return
        configure_terminal_graphics()
        if status_message := terminal_graphics_status_message():
            self._show_status(status_message)
            return

        self._loading = True
        self._show_status("Loading preview…")
        self._header.update(self._header_text())
        self.run_worker(
            self._load_image(),
            name=f"markdown-image-{id(self)}",
            exclusive=True,
        )

    async def _load_image(self) -> None:
        try:
            if self._fetcher is None:
                data = await fetch_image_bytes_async(
                    self.image.src,
                    github_context=self.image.github_context,
                )
            else:
                data = await self._fetcher(self.image.src)
            pil_image = await asyncio.to_thread(_decode_image, data)
            self._pil_image = pil_image
        except Exception as error:
            self._loading = False
            self._header.update(self._header_text())
            self._show_status(_format_image_error(error))
            return

        image_widget = TerminalImage(pil_image, classes="markdown-terminal-image")
        self._size_image_widget(image_widget, pil_image)
        await self._body.remove_children()
        await self._body.mount(image_widget)
        self._loaded = True
        self._loading = False
        self._header.update(self._header_text())

    def _size_image_widget(
        self, image_widget: TerminalImage, image: PILImage.Image
    ) -> None:
        available_width = max(
            1,
            self._body.size.width or self.size.width - 2 or self.app.size.width - 4,
        )
        cell_size = get_cell_size()
        natural_width = max(1, round(image.width / cell_size.width))
        target_width = min(
            available_width,
            MAX_INLINE_IMAGE_WIDTH_CELLS,
            natural_width,
        )
        target_pixel_width = target_width * cell_size.width
        target_height = max(
            1,
            round((image.height / image.width) * target_pixel_width / cell_size.height),
        )

        if target_height > MAX_INLINE_IMAGE_HEIGHT_CELLS:
            target_height = MAX_INLINE_IMAGE_HEIGHT_CELLS
            target_pixel_height = target_height * cell_size.height
            target_width = max(
                1,
                round(
                    (image.width / image.height) * target_pixel_height / cell_size.width
                ),
            )
            target_width = min(
                target_width,
                available_width,
                MAX_INLINE_IMAGE_WIDTH_CELLS,
                natural_width,
            )

        image_widget.styles.width = target_width
        image_widget.styles.height = target_height

    def _header_text(self) -> str:
        label = self.image.alt or self.image.title or _filename_from_url(self.image.src)
        if getattr(self, "_loaded", False):
            action = "open large"
        elif getattr(self, "_loading", False):
            action = "loading preview"
        else:
            action = "open large"
        return f"🖼 [bold]{escape(label or 'image')}[/] [#8aadf4]{action}[/]"

    def _show_status(self, message: str) -> None:
        self._status.update(f"[#6e738d]{escape(message)}[/]")


class ImageFetchError(Exception):
    """Raised when an image cannot be fetched."""


class _ImgTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.attrs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img" and not self.attrs:
            self.attrs = {name.lower(): value or "" for name, value in attrs}


def parse_markdown_image_parts(
    body: str,
    *,
    base_url: str | None = None,
) -> list[MarkdownImagePart]:
    """Split markdown into text and image segments."""
    if not body:
        return []

    parts: list[MarkdownImagePart] = []
    pos = 0

    for start, end, image in _iter_image_matches(body):
        if start > pos:
            text = body[pos:start].strip()
            if text:
                parts.append(MarkdownImagePart(content=text))

        parts.append(MarkdownImagePart(image=image.resolve(base_url)))
        pos = end

    if pos < len(body):
        text = body[pos:].strip()
        if text:
            parts.append(MarkdownImagePart(content=text))

    if not parts and body.strip():
        parts.append(MarkdownImagePart(content=body.strip()))

    return parts


def mount_markdown_image_parts(
    container: Vertical,
    body: str,
    *,
    base_url: str | None = None,
    image_fetcher: ImageFetcher | None = None,
) -> None:
    """Mount markdown text and inline image widgets."""
    from textual.widgets import Markdown

    for part in parse_markdown_image_parts(body, base_url=base_url):
        if part.image:
            container.mount(MarkdownImageBlock(part.image, fetcher=image_fetcher))
        elif part.content:
            container.mount(Markdown(part.content))


async def fetch_image_bytes_async(
    url: str,
    *,
    github_context: str = "",
) -> bytes:
    """Fetch image bytes without writing them to disk."""
    return await asyncio.to_thread(
        fetch_image_bytes,
        url,
        github_context=github_context,
    )


def fetch_image_bytes(
    url: str,
    *,
    timeout: int = IMAGE_FETCH_TIMEOUT_SECONDS,
    max_bytes: int = MAX_IMAGE_BYTES,
    github_context: str = "",
) -> bytes:
    """Fetch image bytes from http(s) or data URLs."""
    if url.startswith("data:"):
        return _read_data_url(url, max_bytes=max_bytes)

    if _is_github_user_attachment_url(url) and github_context:
        url = _resolve_github_user_attachment_url(url, github_context=github_context)

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ImageFetchError(f"Unsupported URL scheme: {parsed.scheme or 'none'}")

    request = Request(url, headers=_headers_for_url(url))
    try:
        with urlopen(request, timeout=timeout) as response:
            length_header = response.headers.get("Content-Length")
            if length_header:
                try:
                    content_length = int(length_header)
                except ValueError:
                    content_length = 0
                if content_length > max_bytes:
                    raise ImageFetchError("Image is too large to render inline")

            data = response.read(max_bytes + 1)
    except HTTPError as error:
        raise ImageFetchError(f"HTTP {error.code} while fetching image") from error
    except URLError as error:
        raise ImageFetchError(f"Failed to fetch image: {error.reason}") from error
    except TimeoutError as error:
        raise ImageFetchError("Timed out while fetching image") from error

    if len(data) > max_bytes:
        raise ImageFetchError("Image is too large to render inline")
    return data


def _iter_image_matches(body: str) -> Iterator[tuple[int, int, MarkdownImageRef]]:
    matches: list[tuple[int, int, MarkdownImageRef]] = []

    for match in _MARKDOWN_IMAGE_RE.finditer(body):
        image = _parse_markdown_image_match(match)
        if image is not None and _is_standalone_image_match(
            body,
            match.start(),
            match.end(),
        ):
            matches.append((match.start(), match.end(), image))

    for match in _HTML_IMAGE_RE.finditer(body):
        image = _parse_html_image_match(match.group(0))
        if image is not None and _is_standalone_image_match(
            body,
            match.start(),
            match.end(),
        ):
            matches.append((match.start(), match.end(), image))

    matches.sort(key=lambda item: item[0])
    last_end = -1
    for start, end, image in matches:
        if start < last_end:
            continue
        last_end = end
        yield start, end, image


def _is_standalone_image_match(body: str, start: int, end: int) -> bool:
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    if line_end == -1:
        line_end = len(body)

    return body[line_start:start].strip() == "" and body[end:line_end].strip() == ""


def _parse_markdown_image_match(match: re.Match[str]) -> MarkdownImageRef | None:
    src, title = _parse_link_destination(match.group("dest"))
    if not src:
        return None
    return MarkdownImageRef(
        alt=_unescape_markdown(match.group("alt")),
        src=src,
        title=title,
    )


def _parse_html_image_match(tag: str) -> MarkdownImageRef | None:
    parser = _ImgTagParser()
    try:
        parser.feed(tag)
    except Exception:
        return None

    src = parser.attrs.get("src", "").strip()
    if not src:
        return None
    return MarkdownImageRef(
        alt=html_unescape(parser.attrs.get("alt", "").strip()),
        src=html_unescape(src),
        title=html_unescape(parser.attrs.get("title", "").strip()),
    )


def _parse_link_destination(raw: str) -> tuple[str, str]:
    value = raw.strip()
    if not value:
        return "", ""

    if value.startswith("<"):
        close_index = value.find(">")
        if close_index != -1:
            src = value[1:close_index].strip()
            title = value[close_index + 1 :].strip()
            return _unescape_markdown(src), _strip_title(title)

    match = re.match(
        r"(?P<src>\S+)(?:\s+(?P<title>\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*$",
        value,
        re.DOTALL,
    )
    if match is None:
        return _unescape_markdown(value), ""
    return _unescape_markdown(match.group("src")), _strip_title(
        match.group("title") or ""
    )


def _strip_title(title: str) -> str:
    value = title.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    if len(value) >= 2 and value[0] == "(" and value[-1] == ")":
        return value[1:-1]
    return value


def _unescape_markdown(value: str) -> str:
    return re.sub(r"\\([\\`*{}\[\]()#+\-.!_>])", r"\1", value)


def _decode_image(data: bytes) -> PILImage.Image:
    try:
        with PILImage.open(BytesIO(data)) as image:
            image.load()
            return image.copy()
    except UnidentifiedImageError as error:
        raise ImageFetchError("Downloaded content is not a supported image") from error


def _read_data_url(url: str, *, max_bytes: int) -> bytes:
    header, separator, payload = url.partition(",")
    if separator != ",":
        raise ImageFetchError("Invalid data URL")

    try:
        if ";base64" in header.lower():
            data = base64.b64decode(payload, validate=True)
        else:
            data = unquote_to_bytes(payload)
    except (binascii.Error, ValueError) as error:
        raise ImageFetchError("Invalid data URL image payload") from error

    if len(data) > max_bytes:
        raise ImageFetchError("Image is too large to render inline")
    return data


def _headers_for_url(url: str) -> dict[str, str]:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "User-Agent": "rit/0.1",
    }
    if token := _github_token_for_url(url):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_token_for_url(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    if not _is_github_owned_host(host):
        return None
    return _github_auth_token()


def _is_github_user_attachment_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname == "github.com"
        and parsed.path.startswith("/user-attachments/assets/")
    )


def _github_context_from_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.hostname != "github.com":
        return ""
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0] == "user-attachments":
        return ""
    return f"{path_parts[0]}/{path_parts[1]}"


def _resolve_github_user_attachment_url(url: str, *, github_context: str) -> str:
    payload = json_dumps(
        {
            "text": f"![image]({url})",
            "mode": "gfm",
            "context": github_context,
        }
    )
    try:
        result = subprocess.run(
            ["gh", "api", "/markdown", "--input", "-"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        raise ImageFetchError("Could not resolve private GitHub image URL") from error

    image = _parse_html_image_match(result.stdout)
    if image is None or image.src == url:
        raise ImageFetchError("Could not resolve private GitHub image URL")
    return image.src


def _is_github_owned_host(host: str) -> bool:
    return (
        host == "github.com"
        or host.endswith(".github.com")
        or host.endswith("githubusercontent.com")
    )


@lru_cache(maxsize=1)
def _github_auth_token() -> str | None:
    for env_name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(env_name, "").strip()
        if token:
            return token

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return None

    return result.stdout.strip() or None


def _format_image_error(error: Exception) -> str:
    if isinstance(error, ImageFetchError):
        return f"Could not render image inline: {error}"
    return "Could not render image inline"


def _has_url_scheme(src: str) -> bool:
    return bool(urlparse(src).scheme)


def _is_fetchable_image_source(src: str) -> bool:
    return src.startswith("data:") or urlparse(src).scheme in {"http", "https"}


def _filename_from_url(src: str) -> str:
    path = urlparse(src).path.rstrip("/")
    if not path:
        return "image"
    return path.rsplit("/", 1)[-1] or "image"


_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>(?:\\.|[^\\\]])*)\]\((?P<dest>(?:\\.|[^\\)])*)\)",
    re.DOTALL,
)
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
