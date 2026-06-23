"""Settings schema: defaults, validation, and UI generation."""

from typing import Literal

# Type for schema field types
FieldType = Literal["boolean", "string", "integer", "float", "choices", "object"]


__all__ = (
    "FieldType",
    "SCHEMA",
    "get_default_settings",
    "get_flat_defaults",
)


# Schema definition using TypedDict for type hints
SCHEMA: list[dict] = [
    {
        "key": "ui",
        "title": "User Interface",
        "type": "object",
        "fields": [
            {
                "key": "theme",
                "title": "Theme",
                "type": "choices",
                "default": "catppuccin-macchiato",
                "choices": [
                    ("Catppuccin Macchiato", "catppuccin-macchiato"),
                    ("Catppuccin Mocha", "catppuccin-mocha"),
                    ("Catppuccin Latte", "catppuccin-latte"),
                    ("Dracula", "dracula"),
                    ("Monokai", "monokai"),
                    ("Nord", "nord"),
                    ("Gruvbox", "gruvbox"),
                    ("Tokyo Night", "tokyo-night"),
                    ("Solarized Light", "solarized-light"),
                    ("Solarized Dark", "solarized-dark"),
                    ("Textual Dark", "textual-dark"),
                    ("Textual Light", "textual-light"),
                ],
                "description": "Color theme for the application",
            },
            {
                "key": "diff_mode",
                "title": "Diff View Mode",
                "type": "choices",
                "default": "auto",
                "choices": [
                    ("Side by side", "split"),
                    ("Unified", "unified"),
                    ("Auto (based on width)", "auto"),
                ],
                "description": "How to display file diffs",
            },
            {
                "key": "show_line_numbers",
                "title": "Show line numbers?",
                "type": "boolean",
                "default": True,
                "description": "Display line numbers in diff view",
            },
            {
                "key": "word_diff",
                "title": "Word-level diff?",
                "type": "boolean",
                "default": True,
                "description": "Highlight word-level changes within lines",
            },
            {
                "key": "sidebar_width",
                "title": "Sidebar width",
                "type": "integer",
                "default": 35,
                "min": 20,
                "max": 80,
                "description": "Width of the file tree sidebar",
            },
        ],
    },
    {
        "key": "keybindings",
        "title": "Keybindings",
        "type": "object",
        "fields": [
            {
                "key": "vim_mode",
                "title": "Enable Vim keybindings?",
                "type": "boolean",
                "default": True,
                "description": "Use Vim-style j/k navigation",
            },
        ],
    },
    {
        "key": "github",
        "title": "GitHub",
        "type": "object",
        "fields": [
            {
                "key": "auto_resolve",
                "title": "Auto-resolve threads?",
                "type": "boolean",
                "default": False,
                "description": "Automatically resolve threads when navigating away",
            },
        ],
    },
]


def get_default_settings() -> dict:
    result: dict = {}

    def process_field(field: dict) -> object:
        if field["type"] == "object":
            obj = {}
            for subfield in field.get("fields", []):
                obj[subfield["key"]] = process_field(subfield)
            return obj
        return field.get("default")

    for section in SCHEMA:
        result[section["key"]] = process_field(section)

    return result


def get_flat_defaults() -> dict[str, object]:
    result: dict[str, object] = {}

    def process_field(field: dict, prefix: str = "") -> None:
        key = f"{prefix}.{field['key']}" if prefix else field["key"]
        if field["type"] == "object":
            for subfield in field.get("fields", []):
                process_field(subfield, key)
        else:
            result[key] = field.get("default")

    for section in SCHEMA:
        process_field(section)

    return result
