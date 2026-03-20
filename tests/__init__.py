"""Tests package for rit.

This module patches Textual 7.x CSS bug before any tests run.
"""


def _patch_textual_markdown_css() -> None:
    """Patch Textual 7.x MarkdownTableContent CSS bug."""
    try:
        from textual.widgets._markdown import MarkdownTableContent

        if "keyline: thin $foreground 20%" in MarkdownTableContent.DEFAULT_CSS:
            MarkdownTableContent.DEFAULT_CSS = MarkdownTableContent.DEFAULT_CSS.replace(
                "keyline: thin $foreground 20%;",
                "keyline: thin $foreground;",
            )
    except ImportError:
        pass


_patch_textual_markdown_css()
