"""Semantic syntax-highlighting styles for dark and light UI themes."""

from collections.abc import Mapping

__all__ = ("styles_for_mode",)


def _build_styles(
    *,
    mauve: str,
    green: str,
    peach: str,
    yellow: str,
    blue: str,
    sky: str,
    pink: str,
    red: str,
    maroon: str,
    teal: str,
    lavender: str,
    overlay2: str,
    text: str,
) -> dict[str, str]:
    return {
        "variable": f"{text} not italic",
        "variable.builtin": f"{red} not italic",
        "variable.member": f"{lavender} not italic",
        "variable.parameter": f"{maroon} not italic",
        "constant": f"{peach} not italic",
        "constant.builtin": f"{peach} not italic",
        "boolean": f"{peach} not italic",
        "module": f"{yellow} italic",
        "namespace": f"{yellow} italic",
        "string": f"{green} not italic",
        "string.documentation": f"{teal} not italic",
        "string.escape": f"{pink} not italic",
        "string.regexp": f"{pink} not italic",
        "string.special": f"{pink} not italic",
        "character": f"{green} not italic",
        "character.special": f"{pink} not italic",
        "number": f"{peach} not italic",
        "type": f"{yellow} not italic",
        "type.builtin": f"{mauve} not italic",
        "attribute": f"{lavender} not italic",
        "tag.attribute": f"{lavender} not italic",
        "property": f"{lavender} not italic",
        "tag": f"{blue} not italic",
        "tag.error": f"{red} not italic",
        "function": f"{blue} not italic",
        "function.builtin": f"{peach} not italic",
        "function.macro": f"{pink} not italic",
        "constructor": f"{yellow} not italic",
        "label": f"{mauve} not italic",
        "operator": f"{sky} not italic",
        "keyword": f"{mauve} not italic",
        "keyword.directive": f"{pink} not italic",
        "punctuation": overlay2,
        "punctuation.delimiter": overlay2,
        "punctuation.bracket": overlay2,
        "punctuation.special": pink,
        "comment": overlay2,
        "comment.documentation": f"{teal} not italic",
        "diff.plus": green,
        "diff.minus": red,
        "markup.heading": f"{blue} bold",
        "markup.italic": f"{text} italic",
        "markup.strong": f"{text} bold",
        "markup.strikethrough": f"{text} strike",
        "markup.link": f"{blue} underline",
        "markup.link.label": lavender,
        "markup.link.url": f"{blue} underline",
        "markup.list": mauve,
        "markup.list.checked": green,
        "markup.list.unchecked": overlay2,
        "markup.math": peach,
        "markup.quote": f"{overlay2} italic",
        "markup.raw": green,
        "markup.raw.block": green,
        "error": red,
    }


_DARK_STYLES = _build_styles(
    mauve="#c6a0f6",
    green="#a6da95",
    peach="#f5a97f",
    yellow="#eed49f",
    blue="#8aadf4",
    sky="#91d7e3",
    pink="#f5bde6",
    red="#ed8796",
    maroon="#ee99a0",
    teal="#8bd5ca",
    lavender="#b7bdf8",
    overlay2="#939ab7",
    text="#cad3f5",
)

_LIGHT_STYLES = _build_styles(
    mauve="#8839ef",
    green="#40a02b",
    peach="#fe640b",
    yellow="#df8e1d",
    blue="#1e66f5",
    sky="#04a5e5",
    pink="#ea76cb",
    red="#d20f39",
    maroon="#e64553",
    teal="#179299",
    lavender="#7287fd",
    overlay2="#7c7f93",
    text="#4c4f69",
)


def styles_for_mode(dark_mode: bool) -> Mapping[str, str]:
    """Return the semantic style palette for the active UI mode."""
    return _DARK_STYLES if dark_mode else _LIGHT_STYLES
