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
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static
from textual_image._terminal import get_cell_size
from textual_image.widget import TGPImage as TerminalImage

from rit.ui.terminal_graphics import (
    configure_terminal_graphics,
    terminal_graphics_status_message,
)

__all__ = (
    "AvailableWidthProvider",
    "ImageFetchError",
    "ImageFetcher",
    "ImageViewerScreen",
    "MarkdownImageBlock",
    "MarkdownImagePart",
    "MarkdownImageRef",
    "MarkdownImageTable",
    "MarkdownImageTableCell",
    "MarkdownImageTableData",
    "MarkdownImageTableRow",
    "fetch_image_bytes",
    "fetch_image_bytes_async",
    "mount_markdown_image_parts",
    "parse_markdown_image_parts",
)


MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_FETCH_TIMEOUT_SECONDS = 10
MAX_INLINE_IMAGE_WIDTH_CELLS = 112
MAX_INLINE_IMAGE_HEIGHT_CELLS = 36
IMAGE_BLOCK_HORIZONTAL_PADDING_CELLS = 2
IMAGE_TABLE_LABEL_WIDTH_CELLS = 10
IMAGE_TABLE_CELL_HORIZONTAL_PADDING_CELLS = 2

ImageFetcher = Callable[[str], Awaitable[bytes]]
AvailableWidthProvider = Callable[[], int | None]


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
    table: MarkdownImageTableData | None = None

    @property
    def is_image(self) -> bool:
        return self.image is not None

    @property
    def is_table(self) -> bool:
        return self.table is not None


@dataclass(frozen=True)
class MarkdownImageTableCell:
    """A markdown table cell that may contain an image."""

    content: str = ""
    image: MarkdownImageRef | None = None


@dataclass(frozen=True)
class MarkdownImageTableRow:
    """A markdown table row with image-aware cells."""

    cells: tuple[MarkdownImageTableCell, ...]


@dataclass(frozen=True)
class MarkdownImageTableData:
    """A markdown table that contains renderable image cells."""

    headers: tuple[str, ...]
    rows: tuple[MarkdownImageTableRow, ...]


@dataclass(frozen=True)
class _ParsedMarkdownImageTable:
    table: MarkdownImageTableData
    next_index: int


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
        Binding("enter", "close", "Close", show=False),
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
            f"{self._image.width}×{self._image.height}px • Esc/Enter to close"
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
        return "Image preview"

    def action_close(self) -> None:
        self.dismiss(None)


class MarkdownImageBlock(Vertical):
    """Inline terminal image block for markdown images."""

    DEFAULT_CSS = """
    MarkdownImageBlock {
        height: auto;
        width: auto;
        margin: 1 0;
        border: solid #363a4f;
        background: #181926;
    }

    MarkdownImageBlock.markdown-image-compact {
        margin: 0;
        border: none;
        background: transparent;
    }

    MarkdownImageBlock .markdown-image-body {
        height: auto;
        padding: 1;
    }

    MarkdownImageBlock.markdown-image-compact .markdown-image-body {
        padding: 0;
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
        compact: bool = False,
        fetcher: ImageFetcher | None = None,
        on_preview_width: Callable[[int], None] | None = None,
        available_width: AvailableWidthProvider | None = None,
    ) -> None:
        classes = "markdown-image-compact" if compact else None
        super().__init__(classes=classes)
        self.image = image
        self.compact = compact
        self._fetcher = fetcher
        self._on_preview_width = on_preview_width
        self._available_width = available_width
        self._body = Vertical(classes="markdown-image-body")
        self._status = Static(
            "Loading preview in background…",
            classes="markdown-image-status",
        )
        self._loaded = False
        self._loading = False
        self._pil_image: PILImage.Image | None = None

    def compose(self) -> ComposeResult:
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
            self._show_status(_format_image_error(error))
            return

        image_widget = TerminalImage(pil_image, classes="markdown-terminal-image")
        preview_width = self._size_image_widget(image_widget, pil_image)
        self._size_block_to_preview(preview_width)
        await self._body.remove_children()
        await self._body.mount(image_widget)
        self._loaded = True
        self._loading = False

    def _size_image_widget(
        self, image_widget: TerminalImage, image: PILImage.Image
    ) -> int:
        available_width = self._available_image_width()
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
        return target_width

    def _available_image_width(self) -> int:
        if self._available_width is not None:
            provided_width = self._available_width()
            if provided_width is not None and provided_width > 0:
                return provided_width

        return max(
            1,
            self._body.size.width or self.size.width - 2 or self.app.size.width - 4,
        )

    def _size_block_to_preview(self, preview_width: int) -> None:
        frame_width = preview_width
        if not self.compact:
            frame_width += IMAGE_BLOCK_HORIZONTAL_PADDING_CELLS

        self.styles.width = frame_width
        self._body.styles.width = frame_width
        if self._on_preview_width is not None:
            self._on_preview_width(frame_width)

    def _show_status(self, message: str) -> None:
        self._status.update(f"[#6e738d]{escape(message)}[/]")


class MarkdownImageTable(Vertical):
    """Markdown table renderer for rows with image cells."""

    DEFAULT_CSS = """
    MarkdownImageTable {
        height: auto;
        width: auto;
        margin: 1 0;
        border: solid #363a4f;
        background: #181926;
    }

    MarkdownImageTable .markdown-image-table-row {
        layout: horizontal;
        width: auto;
        height: auto;
    }

    MarkdownImageTable .markdown-image-table-data-row {
        border-top: solid #363a4f;
    }

    MarkdownImageTable .markdown-image-table-first-row {
        border-top: none;
    }

    MarkdownImageTable .markdown-image-table-cell {
        height: auto;
        padding: 0 1;
    }

    MarkdownImageTable .markdown-image-table-label-cell {
        width: 10;
        content-align: center middle;
        border-right: solid #363a4f;
    }

    MarkdownImageTable .markdown-image-table-image-cell {
        width: auto;
    }

    MarkdownImageTable .markdown-image-table-text {
        height: auto;
        margin: 1 0;
    }

    MarkdownImageTable MarkdownImageBlock {
        margin: 1 0;
    }
    """

    def __init__(
        self,
        table: MarkdownImageTableData,
        *,
        fetcher: ImageFetcher | None = None,
    ) -> None:
        super().__init__(classes="markdown-image-table")
        self.table = table
        self._fetcher = fetcher
        self._image_columns = self._collect_image_columns()
        self._image_column_widths = dict.fromkeys(self._image_columns, 1)
        self.styles.width = self._table_width()

    def compose(self) -> ComposeResult:
        for row_index, row in enumerate(self.table.rows):
            row_classes = "markdown-image-table-row markdown-image-table-data-row"
            if row_index == 0:
                row_classes += " markdown-image-table-first-row"
            with Horizontal(
                classes=row_classes,
            ):
                for index, cell in enumerate(row.cells):
                    with Vertical(classes=self._cell_classes(index)):
                        if cell.image is not None:
                            yield MarkdownImageBlock(
                                cell.image,
                                compact=True,
                                fetcher=self._fetcher,
                                on_preview_width=lambda width, index=index: (
                                    self._set_image_column_width(index, width)
                                ),
                                available_width=lambda index=index: (
                                    self._available_image_column_width(index)
                                ),
                            )
                        else:
                            yield Static(
                                cell.content or " ",
                                classes="markdown-image-table-text",
                                markup=False,
                            )

    def _collect_image_columns(self) -> set[int]:
        image_columns: set[int] = set()
        for row in self.table.rows:
            for index, cell in enumerate(row.cells):
                if cell.image is not None:
                    image_columns.add(index)
        return image_columns

    def _cell_classes(self, index: int) -> str:
        classes = ["markdown-image-table-cell"]
        if index in self._image_columns:
            classes.append("markdown-image-table-image-cell")
        else:
            classes.append("markdown-image-table-label-cell")
        return " ".join(classes)

    def _set_image_column_width(self, index: int, width: int) -> None:
        if index not in self._image_columns:
            return
        current_width = self._image_column_widths.get(index, 1)
        if width <= current_width:
            return
        self._image_column_widths[index] = width
        self.styles.width = self._table_width()

    def _available_image_column_width(self, index: int) -> int | None:
        table_width = self._available_table_width()
        if table_width is None:
            return None

        reserved_width = IMAGE_TABLE_CELL_HORIZONTAL_PADDING_CELLS * len(
            self.table.headers
        )
        for column_index in range(len(self.table.headers)):
            if column_index == index:
                continue
            if column_index in self._image_columns:
                reserved_width += self._image_column_widths.get(column_index, 1)
            else:
                reserved_width += IMAGE_TABLE_LABEL_WIDTH_CELLS

        return max(1, table_width - reserved_width)

    def _available_table_width(self) -> int | None:
        parent = self.parent
        if isinstance(parent, Widget) and parent.size.width > 0:
            return parent.size.width
        if self.size.width > 0:
            return self.size.width
        if self.app.size.width > 0:
            return self.app.size.width - 4
        return None

    def _table_width(self) -> int:
        width = 0
        for index in range(len(self.table.headers)):
            if index in self._image_columns:
                width += self._image_column_widths.get(index, 1)
            else:
                width += IMAGE_TABLE_LABEL_WIDTH_CELLS
            width += IMAGE_TABLE_CELL_HORIZONTAL_PADDING_CELLS
        return max(1, width)


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
    text_lines: list[str] = []
    lines = body.splitlines(keepends=True)
    index = 0

    while index < len(lines):
        table = _parse_image_table_at(lines, index, base_url=base_url)
        if table is None:
            text_lines.append(lines[index])
            index += 1
            continue

        _append_markdown_image_parts(
            parts,
            "".join(text_lines),
            base_url=base_url,
        )
        text_lines.clear()
        parts.append(MarkdownImagePart(table=table.table))
        index = table.next_index

    _append_markdown_image_parts(parts, "".join(text_lines), base_url=base_url)

    return parts


def _append_markdown_image_parts(
    parts: list[MarkdownImagePart],
    body: str,
    *,
    base_url: str | None,
) -> None:
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
        if part.table:
            container.mount(MarkdownImageTable(part.table, fetcher=image_fetcher))
        elif part.image:
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


def _parse_image_table_at(
    lines: list[str],
    index: int,
    *,
    base_url: str | None,
) -> _ParsedMarkdownImageTable | None:
    if index + 1 >= len(lines):
        return None

    if not _is_table_row(lines[index]):
        return None

    headers = tuple(_split_table_cells(lines[index]))
    separator_cells = _split_table_cells(lines[index + 1])
    if len(headers) < 2 or not _is_table_separator_row(separator_cells):
        return None

    rows: list[MarkdownImageTableRow] = []
    has_image = False
    row_index = index + 2

    while row_index < len(lines) and _is_table_row(lines[row_index]):
        cells = tuple(
            _parse_image_table_cell(cell, base_url=base_url)
            for cell in _split_table_cells(lines[row_index])
        )
        if any(cell.content or cell.image is not None for cell in cells):
            rows.append(MarkdownImageTableRow(cells=cells))
        if any(cell.image is not None for cell in cells):
            has_image = True
        row_index += 1

    if not has_image or not rows:
        return None

    return _ParsedMarkdownImageTable(
        table=MarkdownImageTableData(headers=headers, rows=tuple(rows)),
        next_index=row_index,
    )


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("|")
        and stripped.endswith("|")
        and stripped.count("|") >= 2
    )


def _split_table_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]

    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current).replace(r"\|", "|").strip())
            current.clear()
            continue
        current.append(char)

    cells.append("".join(current).replace(r"\|", "|").strip())
    return cells


def _is_table_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", cell.strip()) is not None for cell in cells
    )


def _parse_image_table_cell(
    cell: str,
    *,
    base_url: str | None,
) -> MarkdownImageTableCell:
    image = _image_only_table_cell_ref(cell)
    if image is not None:
        return MarkdownImageTableCell(image=image.resolve(base_url))
    return MarkdownImageTableCell(content=cell.strip())


def _image_only_table_cell_ref(cell: str) -> MarkdownImageRef | None:
    matches = list(_iter_raw_image_matches(cell))
    if len(matches) != 1:
        return None

    start, end, image = matches[0]
    if cell[:start].strip() or cell[end:].strip():
        return None
    return image


def _iter_raw_image_matches(body: str) -> Iterator[tuple[int, int, MarkdownImageRef]]:
    matches: list[tuple[int, int, MarkdownImageRef]] = []

    for match in _MARKDOWN_IMAGE_RE.finditer(body):
        image = _parse_markdown_image_match(match)
        if image is not None:
            matches.append((match.start(), match.end(), image))

    for match in _HTML_IMAGE_RE.finditer(body):
        image = _parse_html_image_match(match.group(0))
        if image is not None:
            matches.append((match.start(), match.end(), image))

    matches.sort(key=lambda item: item[0])
    last_end = -1
    for start, end, image in matches:
        if start < last_end:
            continue
        last_end = end
        yield start, end, image


def _iter_image_matches(body: str) -> Iterator[tuple[int, int, MarkdownImageRef]]:
    for start, end, image in _iter_raw_image_matches(body):
        if _is_renderable_image_match(body, start, end):
            yield start, end, image


def _is_renderable_image_match(body: str, start: int, end: int) -> bool:
    return _is_standalone_image_match(body, start, end) or _is_table_cell_image_match(
        body,
        start,
        end,
    )


def _is_standalone_image_match(body: str, start: int, end: int) -> bool:
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    if line_end == -1:
        line_end = len(body)

    return body[line_start:start].strip() == "" and body[end:line_end].strip() == ""


def _is_table_cell_image_match(body: str, start: int, end: int) -> bool:
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    if line_end == -1:
        line_end = len(body)

    line = body[line_start:line_end].strip()
    if not line.startswith("|") or not line.endswith("|"):
        return False

    before = body[line_start:start]
    after = body[end:line_end]
    if "|" not in before or "|" not in after:
        return False

    return (
        before.rsplit("|", maxsplit=1)[1].strip() == ""
        and after.split("|", maxsplit=1)[0].strip() == ""
    )


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
    parser.feed(tag)

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


_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[(?P<alt>(?:\\.|[^\\\]])*)\]\((?P<dest>(?:\\.|[^\\)])*)\)",
    re.DOTALL,
)
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE | re.DOTALL)
