"""Backward-compatible re-export for collapsible markdown helpers."""

from rit.ui.collapsible_markdown import (
    LAZY_LOAD_THRESHOLD,
    DetailsBlock,
    LazyCollapsible,
    MarkdownPart,
    mount_markdown_with_details,
    parse_details_blocks,
)

__all__ = [
    "LAZY_LOAD_THRESHOLD",
    "DetailsBlock",
    "LazyCollapsible",
    "MarkdownPart",
    "mount_markdown_with_details",
    "parse_details_blocks",
]
