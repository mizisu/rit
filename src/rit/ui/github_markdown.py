"""GitHub-compatible Markdown parser configuration."""

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore


def github_markdown_parser() -> MarkdownIt:
    """Build a GFM parser that preserves source line breaks."""
    parser = MarkdownIt("gfm-like")
    parser.core.ruler.after("inline", "github_line_breaks", _preserve_line_breaks)
    return parser


def _preserve_line_breaks(state: StateCore) -> None:
    for token in state.tokens:
        if token.children is None:
            continue
        for child in token.children:
            if child.type == "softbreak":
                child.type = "hardbreak"
