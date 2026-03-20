"""Pytest configuration and fixtures.

This file patches Textual 7.x CSS bug before any tests run.
"""


def pytest_configure(config):
    """Pytest hook called before test collection.

    This is the earliest point where we can apply the patch.
    """
    _patch_textual_markdown_css()


def _patch_textual_markdown_css() -> None:
    """Patch Textual 7.x MarkdownTableContent CSS bug.

    Textual 7.x has a bug where MarkdownTableContent's DEFAULT_CSS contains
    an invalid keyline value: `keyline: thin $foreground 20%;`
    The keyline property only accepts 2 values: <type> <color>
    """
    try:
        from textual.widgets._markdown import MarkdownTableContent

        if "keyline: thin $foreground 20%" in MarkdownTableContent.DEFAULT_CSS:
            MarkdownTableContent.DEFAULT_CSS = MarkdownTableContent.DEFAULT_CSS.replace(
                "keyline: thin $foreground 20%;",
                "keyline: thin $foreground;",
            )
    except ImportError:
        pass
