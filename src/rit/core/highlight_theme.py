"""Syntax highlighting themes for dark and light UI themes."""

from textual.highlight import HighlightTheme, TokenType
from pygments.token import Token


def _build_theme_styles(
    *,
    mauve: str,
    green: str,
    peach: str,
    yellow: str,
    blue: str,
    sky: str,
    pink: str,
    red: str,
    rosewater: str,
    overlay2: str,
    text: str,
) -> dict[TokenType, str]:
    return {
        Token.Keyword: mauve,
        Token.Keyword.Declaration: mauve,
        Token.Keyword.Namespace: mauve,
        Token.Keyword.Reserved: mauve,
        Token.Keyword.Type: yellow,
        Token.Keyword.Pseudo: pink,
        Token.Keyword.Constant: peach,
        Token.String: green,
        Token.Literal.String: green,
        Token.Literal.String.Single: green,
        Token.Literal.String.Double: green,
        Token.Literal.String.Doc: f"{green} italic",
        Token.Literal.String.Heredoc: green,
        Token.Literal.String.Interpol: green,
        Token.Literal.String.Other: green,
        Token.Literal.String.Affix: green,
        Token.Literal.String.Char: green,
        Token.Literal.String.Delimiter: green,
        Token.Literal.String.Symbol: green,
        Token.Literal.String.Backtick: green,
        Token.Literal.String.Escape: pink,
        Token.Literal.String.Regex: pink,
        Token.Number: peach,
        Token.Literal.Number: peach,
        Token.Literal.Number.Integer: peach,
        Token.Literal.Number.Float: peach,
        Token.Literal.Number.Hex: peach,
        Token.Literal.Number.Bin: peach,
        Token.Literal.Number.Oct: peach,
        Token.Operator: sky,
        Token.Operator.Word: mauve,
        Token.Name.Function: blue,
        Token.Name.Function.Magic: blue,
        Token.Name.Class: yellow,
        Token.Name.Variable: text,
        Token.Name.Variable.Class: text,
        Token.Name.Variable.Global: text,
        Token.Name.Variable.Instance: text,
        Token.Name.Variable.Magic: text,
        Token.Name.Builtin: f"{red} italic",
        Token.Name.Builtin.Pseudo: f"{red} italic",
        Token.Name.Attribute: blue,
        Token.Name.Tag: blue,
        Token.Name.Decorator: rosewater,
        Token.Name.Constant: peach,
        Token.Name.Exception: red,
        Token.Name.Label: mauve,
        Token.Name.Namespace: text,
        Token.Name.Entity: text,
        Token.Name: text,
        Token.Comment: overlay2,
        Token.Comment.Single: overlay2,
        Token.Comment.Multiline: overlay2,
        Token.Comment.Special: overlay2,
        Token.Comment.Hashbang: overlay2,
        Token.Comment.Preproc: pink,
        Token.Comment.PreprocFile: pink,
        Token.Punctuation: overlay2,
        Token.Error: red,
        Token.Generic.Error: red,
        Token.Generic.Strong: "bold",
        Token.Generic.Emph: "italic",
        Token.Generic.Heading: f"{text} underline",
        Token.Generic.Subheading: text,
        Token.Whitespace: "",
    }


class RitHighlightTheme(HighlightTheme):
    """Dark syntax highlighting theme based on Catppuccin Macchiato."""

    STYLES: dict[TokenType, str] = _build_theme_styles(
        mauve="#c6a0f6",
        green="#a6da95",
        peach="#f5a97f",
        yellow="#eed49f",
        blue="#8aadf4",
        sky="#91d7e3",
        pink="#f5bde6",
        red="#ed8796",
        rosewater="#f4dbd6",
        overlay2="#939ab7",
        text="#cad3f5",
    )


class RitLightHighlightTheme(HighlightTheme):
    """Light syntax highlighting theme based on Catppuccin Latte."""

    STYLES: dict[TokenType, str] = _build_theme_styles(
        mauve="#8839ef",
        green="#40a02b",
        peach="#fe640b",
        yellow="#df8e1d",
        blue="#1e66f5",
        sky="#04a5e5",
        pink="#ea76cb",
        red="#d20f39",
        rosewater="#dc8a78",
        overlay2="#7c7f93",
        text="#4c4f69",
    )
