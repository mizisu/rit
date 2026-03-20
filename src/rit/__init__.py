"""rit - Terminal GitHub PR Review CLI."""

__version__ = "0.1.0"


def _patch_textual_markdown_css() -> None:
    """Patch Textual 7.x MarkdownTableContent CSS bug.

    Textual 7.x has a bug where MarkdownTableContent's DEFAULT_CSS contains
    an invalid keyline value: `keyline: thin $foreground 20%;`
    The keyline property only accepts 2 values: <type> <color>
    """
    try:
        from textual.widgets._markdown import MarkdownTableContent

        # Check if the bug exists
        if "keyline: thin $foreground 20%" in MarkdownTableContent.DEFAULT_CSS:
            # Fix the CSS by removing the invalid 20% value
            MarkdownTableContent.DEFAULT_CSS = MarkdownTableContent.DEFAULT_CSS.replace(
                "keyline: thin $foreground 20%;",
                "keyline: thin $foreground;",
            )
    except ImportError:
        pass  # Textual not installed or different version


# Apply patch on import
_patch_textual_markdown_css()
