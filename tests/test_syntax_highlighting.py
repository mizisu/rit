"""Contract tests for the syntax-highlighting module."""

import pytest
from textual.content import Content
from textual.style import Style

from rit.core import syntax_highlighting as syntax_module
from rit.core.syntax_highlighting import highlight_code


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


def test_python_scopes_map_to_catppuccin_roles() -> None:
    code = '''from package import Client

DEFAULT_NAME = "rit"

class Service:
    def load(self, key: str) -> bool:
        value = self.client.load(key)
        return value
'''

    content = highlight_code(
        code,
        path="service.py",
        dark_mode=True,
    )

    assert _foreground(_style_for_occurrence(content, "from")) == "#c6a0f6"
    assert _foreground(_style_for_occurrence(content, "Client")) == "#eed49f"
    assert _foreground(_style_for_occurrence(content, "DEFAULT_NAME")) == "#f5a97f"
    assert _foreground(_style_for_occurrence(content, '"rit"')) == "#a6da95"
    assert _foreground(_style_for_occurrence(content, "Service")) == "#eed49f"
    assert _foreground(_style_for_occurrence(content, "load")) == "#8aadf4"
    assert _foreground(_style_for_occurrence(content, "client")) == "#b7bdf8"
    assert _foreground(_style_for_occurrence(content, "str")) == "#eed49f"


def test_python_scopes_use_light_palette() -> None:
    content = highlight_code(
        "def load(key: str) -> bool:\n    return key\n",
        path="service.py",
        dark_mode=False,
    )

    assert _foreground(_style_for_occurrence(content, "def")) == "#8839ef"
    assert _foreground(_style_for_occurrence(content, "load")) == "#1e66f5"
    assert _foreground(_style_for_occurrence(content, "str")) == "#df8e1d"


def test_partial_code_still_gets_structural_scopes() -> None:
    content = highlight_code(
        "        return self.client.load(value)",
        path="service.py",
        dark_mode=True,
    )

    assert _foreground(_style_for_occurrence(content, "return")) == "#c6a0f6"
    assert _foreground(_style_for_occurrence(content, "self")) == "#cad3f5"
    assert _foreground(_style_for_occurrence(content, "client")) == "#b7bdf8"
    assert _foreground(_style_for_occurrence(content, "load")) == "#8aadf4"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("script.sh", 'echo "hello"'),
        ("main.c", "int main(void) { return 0; }"),
        ("main.cpp", "#include <vector>\nint main() { return 0; }"),
        ("styles.css", ".button { color: red; }"),
        ("main.go", 'package main\nfunc main() { println("hi") }'),
        ("index.html", '<main class="app">hello</main>'),
        ("Main.java", "class Main { int value = 1; }"),
        ("app.js", 'const value = () => "ok";'),
        ("data.json", '{"name": true}'),
        ("index.php", "<?php function run(): void { return; }"),
        ("service.py", "def run(value):\n    return value"),
        ("main.rb", "def run(value)\n  value\nend"),
        ("main.rs", "fn main() { let value = true; }"),
        ("config.toml", 'name = "rit"'),
        ("component.tsx", 'const app = <Button label="ok" />;'),
        ("types.ts", "interface User { name: string }"),
        ("workflow.yml", "name: rit"),
        ("example.ex", 'defmodule Example do\n  IO.puts("hello")\nend'),
        ("Main.kt", 'fun main() { println("hello") }'),
        ("main.lua", 'local value = "hello"'),
        ("main.swift", 'let value = "hello"'),
        ("main.scala", 'val value = "hello"'),
        ("main.hs", 'main = putStrLn "hello"'),
        ("main.clj", '(def value "hello")'),
        ("main.dart", 'final value = "hello";'),
        ("component.vue", '<template><main>Hello</main></template>'),
        ("component.svelte", '<script>let value = "hello";</script>'),
        ("main.zig", 'const value = "hello";'),
        ("query.sql", "SELECT name FROM users;"),
        ("message.proto", 'message User { string name = 1; }'),
        ("main.tf", 'resource "example" "main" {}'),
    ],
)
def test_many_languages_produce_syntax_spans(path: str, code: str) -> None:
    content = highlight_code(code, path=path, dark_mode=True)

    assert content.plain == code
    assert any(str(span.style) != "$text" for span in content.spans)


def test_tsx_uses_tag_and_attribute_scopes() -> None:
    content = highlight_code(
        'const app = <Button label="ok" />;',
        path="component.tsx",
        dark_mode=True,
    )

    assert _foreground(_style_for_occurrence(content, "Button")) == "#8aadf4"
    assert _foreground(_style_for_occurrence(content, "label")) == "#b7bdf8"


def test_markdown_fence_highlights_injected_python() -> None:
    code = '# Demo\n\n```python\ndef greet(name: str) -> str:\n    return name\n```\n'

    content = highlight_code(code, path="README.md", dark_mode=True)

    assert _foreground(_style_for_occurrence(content, "def")) == "#c6a0f6"
    assert _foreground(_style_for_occurrence(content, "greet")) == "#8aadf4"
    assert _foreground(_style_for_occurrence(content, "str")) == "#eed49f"


@pytest.mark.parametrize(
    "code",
    [
        "#!/usr/bin/env -S python3 -u\nprint('ok')",
        "#!/bin/bash\necho ok",
        "#!/usr/bin/env node\nconsole.log('ok')",
    ],
)
def test_common_shebangs_are_highlighted(code: str) -> None:
    content = highlight_code(code, path="tool", dark_mode=True)

    assert any(str(span.style) != "$text" for span in content.spans)


def test_unknown_plain_text_stays_plain() -> None:
    code = "ordinary prose without source syntax"

    content = highlight_code(code, path="notes.unknown", dark_mode=True)

    assert [(span.start, span.end, str(span.style)) for span in content.spans] == [
        (0, len(code), "$text")
    ]


def test_unicode_byte_ranges_become_character_offsets() -> None:
    code = 'name = "한글"\nvalue = true'

    content = highlight_code(code, path="data.json", dark_mode=True)

    assert _foreground(_style_for_occurrence(content, "true")) == "#f5a97f"


def test_large_span_set_keeps_stable_offsets() -> None:
    snippet = "def process(value: str) -> str:\n    return value.strip()\n"
    content = highlight_code(
        snippet * 100,
        path="service.py",
        dark_mode=True,
    )

    assert _foreground(_style_for_occurrence(content, "return", 100)) == "#c6a0f6"


def test_native_failure_falls_back_to_plain_text(monkeypatch) -> None:
    def fail(_source: str, _language_hint: str) -> list[tuple[int, int, str]]:
        raise RuntimeError("native failure")

    monkeypatch.setattr(syntax_module._lumis, "highlight_spans", fail)
    code = "def run():\n    return 1"

    content = highlight_code(code, path="service.py", dark_mode=True)

    assert [(span.start, span.end, str(span.style)) for span in content.spans] == [
        (0, len(code), "$text")
    ]


def test_oversized_sources_fall_back_to_plain_text(monkeypatch) -> None:
    monkeypatch.setattr(syntax_module, "_MAX_HIGHLIGHT_BYTES", 16)
    code = "const generatedValue = 123456789;"

    content = highlight_code(code, path="generated.js", dark_mode=True)

    assert [(span.start, span.end, str(span.style)) for span in content.spans] == [
        (0, len(code), "$text")
    ]


def test_oversized_lines_do_not_disable_small_files(monkeypatch) -> None:
    monkeypatch.setattr(syntax_module, "_MAX_HIGHLIGHT_BYTES", 1_000)
    monkeypatch.setattr(syntax_module, "_MAX_HIGHLIGHT_LINE_BYTES", 16)

    generated = highlight_code(
        'const value = "this line is intentionally long";',
        path="generated.js",
        dark_mode=True,
    )
    normal = highlight_code(
        "const value = 1;\nconst next = 2;",
        path="normal.js",
        dark_mode=True,
    )

    assert len(generated.spans) == 1
    assert any(str(span.style) != "$text" for span in normal.spans)
