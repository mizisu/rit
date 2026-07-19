"""Tests for Tree-sitter syntax highlighting."""

import pytest
from textual.content import Content
from textual.style import Style

from rit.core import tree_sitter_highlighting as tree_sitter_module
from rit.core.highlighting import _highlight_code
from rit.core.tree_sitter_highlighting import detect_tree_sitter_language


def _style_for_occurrence(
    content: Content,
    fragment: str,
    occurrence: int = 1,
) -> Style:
    start = -1
    for _ in range(occurrence):
        start = content.plain.index(fragment, start + 1)

    rendered_offset = 0
    for text, style in content.render():
        if rendered_offset <= start < rendered_offset + len(text):
            return style
        rendered_offset += len(text)
    raise AssertionError(f"No rendered style for {fragment!r}")


def _foreground(style: Style) -> str | None:
    return style.foreground.hex.lower() if style.foreground else None


def test_python_semantic_captures_match_catppuccin_roles() -> None:
    code = '''from lemonbase.storage.infra.s3_client import S3Client

TEXT_FILES = ('.txt',)

class S3StorageService:
    def upload_object(
        self,
        key: str,
        object: FileobjTypeDef,
        metadata: dict[str, str] | None = None,
    ) -> bool:
        """한글 docstring keeps later offsets accurate."""
        suffix = self.s3_client.upload_object(key, object, metadata=metadata)
        return suffix
'''

    content = _highlight_code(
        code,
        language="python",
        path="s3_storage_service.py",
        dark_mode=True,
    )

    module = _style_for_occurrence(content, "lemonbase")
    assert _foreground(module) == "#eed49f"
    assert module.italic
    assert _foreground(_style_for_occurrence(content, "TEXT_FILES")) == "#f5a97f"
    assert _foreground(_style_for_occurrence(content, "S3Client")) == "#eed49f"
    assert _foreground(_style_for_occurrence(content, "S3StorageService")) == "#eed49f"
    assert _foreground(_style_for_occurrence(content, "self", 2)) == "#ed8796"
    assert _foreground(_style_for_occurrence(content, "key", 2)) == "#ee99a0"
    assert _foreground(_style_for_occurrence(content, "dict")) == "#eed49f"
    assert _foreground(_style_for_occurrence(content, "FileobjTypeDef")) == "#eed49f"
    assert _foreground(_style_for_occurrence(content, "s3_client", 2)) == "#b7bdf8"
    assert _foreground(_style_for_occurrence(content, "upload_object", 2)) == "#8aadf4"
    assert _foreground(_style_for_occurrence(content, "metadata", 3)) == "#ee99a0"
    assert _foreground(_style_for_occurrence(content, "suffix")) == "#cad3f5"


def test_python_semantic_captures_use_light_palette() -> None:
    content = _highlight_code(
        "def load(key: dict[str, str]) -> bool:\n    return key\n",
        language="python",
        path="service.py",
        dark_mode=False,
    )

    assert _foreground(_style_for_occurrence(content, "key", 2)) == "#e64553"
    assert _foreground(_style_for_occurrence(content, "dict")) == "#df8e1d"
    assert _foreground(_style_for_occurrence(content, "load")) == "#1e66f5"


def test_python_partial_code_still_gets_structural_captures() -> None:
    content = _highlight_code(
        "        return self.client.load(value)",
        language="python",
        path="service.py",
        dark_mode=True,
    )

    assert _foreground(_style_for_occurrence(content, "self")) == "#ed8796"
    assert _foreground(_style_for_occurrence(content, "client")) == "#b7bdf8"
    assert _foreground(_style_for_occurrence(content, "load")) == "#8aadf4"
    assert _foreground(_style_for_occurrence(content, "value")) == "#cad3f5"


def test_unbundled_language_has_no_lexer_fallback() -> None:
    code = 'defmodule Example do\n  IO.puts("hello")\nend'

    content = _highlight_code(code, path="example.ex", dark_mode=True)

    assert content.plain == code
    assert [(span.start, span.end, str(span.style)) for span in content.spans] == [
        (0, len(code), "$text")
    ]


def test_python_large_capture_set_uses_stable_point_offsets() -> None:
    snippet = (
        "from pathlib import Path\n\n"
        "def process(value: str) -> str:\n"
        "    return value.strip()\n\n"
    )
    content = _highlight_code(
        snippet * 100,
        language="python",
        path="service.py",
        dark_mode=True,
    )

    assert _foreground(_style_for_occurrence(content, "value", 200)) == "#ee99a0"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("script.sh", "bash"),
        ("main.c", "c"),
        ("main.C", "cpp"),
        ("main.cpp", "cpp"),
        ("styles.css", "css"),
        ("main.go", "go"),
        ("index.html", "html"),
        ("Main.java", "java"),
        ("app.mjs", "javascript"),
        ("data.json", "json"),
        ("index.php", "php"),
        ("service.py", "python"),
        ("Gemfile", "ruby"),
        ("main.rs", "rust"),
        ("Cargo.lock", "toml"),
        ("types.d.ts", "typescript"),
        ("component.tsx", "tsx"),
        ("workflow.yml", "yaml"),
    ],
)
def test_detects_bundled_languages_from_paths(path: str, expected: str) -> None:
    assert detect_tree_sitter_language("", path) == expected


@pytest.mark.parametrize(
    ("language", "code"),
    [
        ("bash", 'echo "hello"'),
        ("c", "int main(void) { return 0; }"),
        ("cpp", "#include <vector>\nint main() { return 0; }"),
        ("css", ".button { color: red; }"),
        ("go", 'package main\nfunc main() { println("hi") }'),
        ("html", '<main class="app">hello</main>'),
        ("java", "class Main { int value = 1; }"),
        ("javascript", 'const value = () => "ok";'),
        ("json", '{"name": true}'),
        ("php", "function run(): void { return; }"),
        ("ruby", "def run(value)\n  value\nend"),
        ("rust", "fn main() { let value = true; }"),
        ("toml", 'name = "rit"'),
        ("tsx", 'const app = <Button label="ok" />;'),
        ("typescript", "interface User { name: string }"),
        ("yaml", "name: rit"),
    ],
)
def test_bundled_language_queries_produce_syntax_spans(
    language: str,
    code: str,
) -> None:
    content = _highlight_code(
        code,
        language=language,
        path="source",
        dark_mode=True,
    )

    assert content.plain == code
    assert any(str(span.style) != "$text" for span in content.spans)


def test_javascript_typescript_and_tsx_compose_their_inherited_queries() -> None:
    javascript = _highlight_code(
        'const app = <Button label="ok" />;',
        language="jsx",
        path="component.jsx",
        dark_mode=True,
    )
    typescript = _highlight_code(
        "interface User { name: string }",
        language="ts",
        path="types.ts",
        dark_mode=True,
    )
    tsx = _highlight_code(
        'const app = <Button label="ok" />;',
        language="tsx",
        path="component.tsx",
        dark_mode=True,
    )

    assert _foreground(_style_for_occurrence(javascript, "Button")) == "#8aadf4"
    assert _foreground(_style_for_occurrence(typescript, "User")) == "#eed49f"
    assert _foreground(_style_for_occurrence(tsx, "Button")) == "#8aadf4"
    assert _foreground(_style_for_occurrence(tsx, "label")) == "#b7bdf8"


def test_detects_common_shebangs_without_content_guessing() -> None:
    assert (
        detect_tree_sitter_language("#!/usr/bin/env -S python3 -u\n", "tool")
        == "python"
    )
    assert detect_tree_sitter_language("#!/bin/bash\n", "tool") == "bash"
    assert detect_tree_sitter_language("#!/usr/bin/env node\n", "tool") == "javascript"
    assert (
        detect_tree_sitter_language("#!/usr/bin/env deno run\n", "tool") == "typescript"
    )
    assert detect_tree_sitter_language("plain text", "README") is None


def test_highlighters_and_thread_local_parsers_are_reused() -> None:
    highlighter = tree_sitter_module._load_highlighter("go")
    language, _query = highlighter

    assert tree_sitter_module._load_highlighter("go") is highlighter
    assert tree_sitter_module._parser_for(
        "go", language
    ) is tree_sitter_module._parser_for("go", language)


def test_pathological_sources_fall_back_to_plain_text(monkeypatch) -> None:
    monkeypatch.setattr(tree_sitter_module, "_MAX_HIGHLIGHT_BYTES", 16)
    content = _highlight_code(
        "const generatedValue = 123456789;",
        language="javascript",
        path="generated.js",
        dark_mode=True,
    )

    assert [(span.start, span.end, str(span.style)) for span in content.spans] == [
        (0, len(content.plain), "$text")
    ]


def test_pathological_single_lines_fall_back_without_disabling_small_files(
    monkeypatch,
) -> None:
    monkeypatch.setattr(tree_sitter_module, "_MAX_HIGHLIGHT_BYTES", 1_000)
    monkeypatch.setattr(tree_sitter_module, "_MAX_HIGHLIGHT_LINE_BYTES", 16)

    generated = _highlight_code(
        'const value = "this line is intentionally long";',
        language="javascript",
        path="generated.js",
        dark_mode=True,
    )
    normal = _highlight_code(
        "const value = 1;\nconst next = 2;",
        language="javascript",
        path="normal.js",
        dark_mode=True,
    )

    assert len(generated.spans) == 1
    assert any(str(span.style) != "$text" for span in normal.spans)
