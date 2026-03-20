"""Catppuccin Macchiato syntax highlighting theme.

This module provides a HighlightTheme using exact Catppuccin Macchiato hex colors
following the official style guide.

Based on: https://github.com/catppuccin/catppuccin/blob/main/docs/style-guide.md
"""

from textual.highlight import HighlightTheme, TokenType
from pygments.token import Token


class RitHighlightTheme(HighlightTheme):
    """Syntax highlighting theme using official Catppuccin Macchiato colors.

    Uses exact hex color values from the Catppuccin Macchiato palette,
    ensuring perfect color accuracy matching the official Catppuccin style guide.

    Color Palette:
    - Mauve (#c6a0f6): Keywords
    - Green (#a6da95): Strings
    - Peach (#f5a97f): Numbers, Constants
    - Yellow (#eed49f): Classes, Types
    - Blue (#8aadf4): Functions, Methods
    - Sky (#91d7e3): Operators
    - Pink (#f5bde6): Escape sequences, Regex
    - Red (#ed8796): Errors, Builtins
    - Rosewater (#f4dbd6): Decorators
    - Overlay2 (#939ab7): Comments, Punctuation
    - Text (#cad3f5): Default text
    """

    STYLES: dict[TokenType, str] = {
        Token.Keyword: "#c6a0f6",
        Token.Keyword.Declaration: "#c6a0f6",
        Token.Keyword.Namespace: "#c6a0f6",
        Token.Keyword.Reserved: "#c6a0f6",
        Token.Keyword.Type: "#eed49f",
        Token.Keyword.Pseudo: "#f5bde6",
        Token.Keyword.Constant: "#f5a97f",
        Token.String: "#a6da95",
        Token.Literal.String: "#a6da95",
        Token.Literal.String.Single: "#a6da95",
        Token.Literal.String.Double: "#a6da95",
        Token.Literal.String.Doc: "#a6da95 italic",
        Token.Literal.String.Heredoc: "#a6da95",
        Token.Literal.String.Interpol: "#a6da95",
        Token.Literal.String.Other: "#a6da95",
        Token.Literal.String.Affix: "#a6da95",
        Token.Literal.String.Char: "#a6da95",
        Token.Literal.String.Delimiter: "#a6da95",
        Token.Literal.String.Symbol: "#a6da95",
        Token.Literal.String.Backtick: "#a6da95",
        Token.Literal.String.Escape: "#f5bde6",
        Token.Literal.String.Regex: "#f5bde6",
        Token.Number: "#f5a97f",
        Token.Literal.Number: "#f5a97f",
        Token.Literal.Number.Integer: "#f5a97f",
        Token.Literal.Number.Float: "#f5a97f",
        Token.Literal.Number.Hex: "#f5a97f",
        Token.Literal.Number.Bin: "#f5a97f",
        Token.Literal.Number.Oct: "#f5a97f",
        Token.Operator: "#91d7e3",
        Token.Operator.Word: "#c6a0f6",
        Token.Name.Function: "#8aadf4",
        Token.Name.Function.Magic: "#8aadf4",
        Token.Name.Class: "#eed49f",
        Token.Name.Variable: "#cad3f5",
        Token.Name.Variable.Class: "#cad3f5",
        Token.Name.Variable.Global: "#cad3f5",
        Token.Name.Variable.Instance: "#cad3f5",
        Token.Name.Variable.Magic: "#cad3f5",
        # Red (#ed8796) + italic - Builtins
        Token.Name.Builtin: "#ed8796 italic",
        Token.Name.Builtin.Pseudo: "#ed8796 italic",
        Token.Name.Attribute: "#8aadf4",
        Token.Name.Tag: "#8aadf4",
        Token.Name.Decorator: "#f4dbd6",
        Token.Name.Constant: "#f5a97f",
        Token.Name.Exception: "#ed8796",
        Token.Name.Label: "#b7bdf8",
        Token.Name.Namespace: "#cad3f5",
        Token.Name.Entity: "#cad3f5",
        Token.Name: "#cad3f5",
        Token.Comment: "#939ab7",
        Token.Comment.Single: "#939ab7",
        Token.Comment.Multiline: "#939ab7",
        Token.Comment.Special: "#939ab7",
        Token.Comment.Hashbang: "#939ab7",
        Token.Comment.Preproc: "#f5bde6",
        Token.Comment.PreprocFile: "#f5bde6",
        Token.Punctuation: "#939ab7",
        Token.Error: "#ed8796",
        Token.Generic.Error: "#ed8796",
        Token.Generic.Strong: "bold",
        Token.Generic.Emph: "italic",
        Token.Generic.Heading: "#cad3f5 underline",
        Token.Generic.Subheading: "#cad3f5",
        Token.Whitespace: "",
    }
