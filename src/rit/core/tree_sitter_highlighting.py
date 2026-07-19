"""Fast, lazy Tree-sitter syntax highlighting for bundled languages."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from importlib import import_module
from pathlib import PurePath
from threading import local

from textual.content import Content, Span
from tree_sitter import Language, Node, Parser, Query, QueryCursor

_PYTHON_SUPPLEMENTAL_QUERY = r"""
((identifier) @variable.builtin
 (#match? @variable.builtin "^(self|cls)$"))

(import_statement
  name: (dotted_name (identifier) @module))
(import_statement
  name: (aliased_import
    name: (dotted_name (identifier) @module)))
(import_from_statement
  module_name: (dotted_name (identifier) @module))

(generic_type
  (identifier) @type)

(keyword_argument
  name: (identifier) @variable.parameter)

[
  "("
  ")"
  "["
  "]"
  "{"
  "}"
] @punctuation.bracket

[
  ","
  "."
  ":"
  ";"
] @punctuation.delimiter
"""

_JSX_SUPPLEMENTAL_QUERY = r"""
(jsx_opening_element name: (_) @tag)
(jsx_closing_element name: (_) @tag)
(jsx_self_closing_element name: (_) @tag)
(jsx_attribute (property_identifier) @attribute)
["<" ">" "</" "/>" ] @punctuation.bracket
"""

_MAX_HIGHLIGHT_BYTES = 4 * 1024 * 1024
_MAX_HIGHLIGHT_LINE_BYTES = 256 * 1024
_MAX_CAPTURE_NODES = 250_000


@dataclass(frozen=True)
class _LanguageSpec:
    grammar_module: str
    language_factory: str = "language"
    query_modules: tuple[str, ...] = ()
    supplemental_query: str = ""


_LANGUAGE_SPECS = {
    "bash": _LanguageSpec("tree_sitter_bash"),
    "c": _LanguageSpec("tree_sitter_c"),
    "cpp": _LanguageSpec(
        "tree_sitter_cpp",
        query_modules=("tree_sitter_c", "tree_sitter_cpp"),
    ),
    "css": _LanguageSpec("tree_sitter_css"),
    "go": _LanguageSpec("tree_sitter_go"),
    "html": _LanguageSpec("tree_sitter_html"),
    "java": _LanguageSpec("tree_sitter_java"),
    "javascript": _LanguageSpec(
        "tree_sitter_javascript",
        supplemental_query=_JSX_SUPPLEMENTAL_QUERY,
    ),
    "json": _LanguageSpec("tree_sitter_json"),
    "php": _LanguageSpec("tree_sitter_php", language_factory="language_php_only"),
    "python": _LanguageSpec(
        "tree_sitter_python",
        supplemental_query=_PYTHON_SUPPLEMENTAL_QUERY,
    ),
    "ruby": _LanguageSpec("tree_sitter_ruby"),
    "rust": _LanguageSpec("tree_sitter_rust"),
    "toml": _LanguageSpec("tree_sitter_toml"),
    "tsx": _LanguageSpec(
        "tree_sitter_typescript",
        language_factory="language_tsx",
        query_modules=("tree_sitter_javascript", "tree_sitter_typescript"),
        supplemental_query=_JSX_SUPPLEMENTAL_QUERY,
    ),
    "typescript": _LanguageSpec(
        "tree_sitter_typescript",
        language_factory="language_typescript",
        query_modules=("tree_sitter_javascript", "tree_sitter_typescript"),
    ),
    "yaml": _LanguageSpec("tree_sitter_yaml"),
}

_LANGUAGE_ALIASES = {
    "bash": "bash",
    "c": "c",
    "c++": "cpp",
    "cc": "cpp",
    "cpp": "cpp",
    "css": "css",
    "go": "go",
    "golang": "go",
    "html": "html",
    "java": "java",
    "javascript": "javascript",
    "js": "javascript",
    "jsx": "javascript",
    "json": "json",
    "php": "php",
    "py": "python",
    "python": "python",
    "python3": "python",
    "rb": "ruby",
    "ruby": "ruby",
    "rs": "rust",
    "rust": "rust",
    "sh": "bash",
    "shell": "bash",
    "toml": "toml",
    "ts": "typescript",
    "tsx": "tsx",
    "typescript": "typescript",
    "yaml": "yaml",
    "yml": "yaml",
}

_EXTENSION_LANGUAGES = {
    ".bash": "bash",
    ".bats": "bash",
    ".c": "c",
    ".c++": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".h++": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".htm": "html",
    ".html": "html",
    ".hxx": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsonl": "json",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".php": "php",
    ".phtml": "php",
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "bash",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_FILENAME_LANGUAGES = {
    ".bash_login": "bash",
    ".bash_profile": "bash",
    ".bashrc": "bash",
    ".profile": "bash",
    ".ruby-version": "ruby",
    "cargo.lock": "toml",
    "gemfile": "ruby",
    "guardfile": "ruby",
    "rakefile": "ruby",
    "vagrantfile": "ruby",
}

_CAPTURE_ALIASES = {
    "boolean": "constant.builtin",
    "conditional": "keyword.conditional",
    "delimiter": "punctuation.delimiter",
    "escape": "string.escape",
    "exception": "keyword.exception",
    "field": "variable.member",
    "float": "number.float",
    "include": "keyword.import",
    "method": "function.method",
    "namespace": "module",
    "parameter": "variable.parameter",
    "preproc": "keyword.directive",
    "repeat": "keyword.repeat",
    "storageclass": "keyword.modifier",
    "string.special.regex": "string.regexp",
    "symbol": "string.special.symbol",
    "text.emphasis": "markup.italic",
    "text.literal": "markup.raw",
    "text.reference": "markup.link",
    "text.strike": "markup.strikethrough",
    "text.strong": "markup.strong",
    "text.title": "markup.heading",
    "text.underline": "markup.underline",
    "text.uri": "markup.link.url",
}

_CAPTURE_PRIORITY = {
    "variable": 0,
    "module": 20,
    "property": 30,
    "variable.member": 30,
    "attribute": 35,
    "constructor": 40,
    "constant": 40,
    "tag": 40,
    "type": 40,
    "variable.parameter": 50,
    "variable.builtin": 55,
    "function": 60,
    "function.method": 65,
    "function.builtin": 70,
}

_SCOPE_TYPES = frozenset({"function_definition", "lambda"})
_NESTED_SCOPE_TYPES = _SCOPE_TYPES | {"class_definition"}
_PARSER_STATE = local()


@dataclass(frozen=True)
class _Capture:
    node: Node
    name: str


@dataclass(frozen=True)
class _ByteSpan:
    start: int
    end: int
    style: str


@cache
def _load_highlighter(language_name: str) -> tuple[Language, Query]:
    spec = _LANGUAGE_SPECS[language_name]
    grammar_module = import_module(spec.grammar_module)
    language = Language(getattr(grammar_module, spec.language_factory)())

    query_modules = spec.query_modules or (spec.grammar_module,)
    query_parts: list[str] = []
    for module_name in query_modules:
        query_source = getattr(import_module(module_name), "HIGHLIGHTS_QUERY")
        if not isinstance(query_source, str):
            raise TypeError(f"{module_name}.HIGHLIGHTS_QUERY is not text")
        query_parts.append(query_source)
    if spec.supplemental_query:
        query_parts.append(spec.supplemental_query)

    return language, Query(language, "\n".join(query_parts))


def _parser_for(language_name: str, language: Language) -> Parser:
    parsers = getattr(_PARSER_STATE, "parsers", None)
    if parsers is None:
        parsers = {}
        _PARSER_STATE.parsers = parsers
    parser = parsers.get(language_name)
    if parser is None:
        parser = Parser(language)
        parsers[language_name] = parser
    return parser


@cache
def _canonical_capture_name(name: str) -> str:
    return _CAPTURE_ALIASES.get(name, name)


def _style_for_capture(name: str, styles: Mapping[str, str]) -> str | None:
    candidate = _canonical_capture_name(name)
    while candidate:
        if style := styles.get(candidate):
            return style
        candidate, _, _ = candidate.rpartition(".")
    return None


@cache
def _capture_priority(name: str) -> tuple[int, int]:
    canonical_name = _canonical_capture_name(name)
    candidate = canonical_name
    while candidate:
        if candidate in _CAPTURE_PRIORITY:
            return _CAPTURE_PRIORITY[candidate], canonical_name.count(".")
        candidate, _, _ = candidate.rpartition(".")
    return 10, canonical_name.count(".")


def _parameter_binding_nodes(parameters: Node) -> Iterator[Node]:
    for child in parameters.named_children:
        yield from _binding_identifiers(child)


def _binding_identifiers(node: Node) -> Iterator[Node]:
    if node.type == "identifier":
        yield node
        return

    if node.type in {"default_parameter", "typed_default_parameter"}:
        name = node.child_by_field_name("name")
        if name is not None:
            yield from _binding_identifiers(name)
        return

    if node.type == "typed_parameter":
        type_node = node.child_by_field_name("type")
        for child in node.named_children:
            if child != type_node:
                yield from _binding_identifiers(child)
        return

    if node.type in {
        "dictionary_splat_pattern",
        "list_pattern",
        "list_splat_pattern",
        "pattern_list",
        "tuple_pattern",
    }:
        for child in node.named_children:
            yield from _binding_identifiers(child)


def _node_bytes(node: Node, source: bytes) -> bytes:
    return source[node.start_byte : node.end_byte]


def _is_reference_identifier(node: Node) -> bool:
    parent = node.parent
    if parent is None:
        return True
    if parent.type == "attribute" and parent.child_by_field_name("attribute") == node:
        return False
    if parent.type == "keyword_argument" and parent.child_by_field_name("name") == node:
        return False
    return True


def _parameter_reference_nodes(
    node: Node,
    parameter_names: frozenset[bytes],
    source: bytes,
) -> Iterator[Node]:
    if node.type in _NESTED_SCOPE_TYPES:
        return
    if (
        node.type == "identifier"
        and _is_reference_identifier(node)
        and _node_bytes(node, source) in parameter_names
    ):
        yield node
        return
    for child in node.named_children:
        yield from _parameter_reference_nodes(child, parameter_names, source)


def _parameter_captures(root: Node, source: bytes) -> Iterator[_Capture]:
    if root.type in _SCOPE_TYPES:
        parameters = root.child_by_field_name("parameters")
        body = root.child_by_field_name("body")
        if parameters is not None:
            bindings = tuple(_parameter_binding_nodes(parameters))
            parameter_names = frozenset(_node_bytes(node, source) for node in bindings)
            for node in bindings:
                name = _node_bytes(node, source)
                capture_name = (
                    "variable.builtin"
                    if name in {b"self", b"cls"}
                    else "variable.parameter"
                )
                yield _Capture(node, capture_name)
            if body is not None and parameter_names:
                for node in _parameter_reference_nodes(body, parameter_names, source):
                    name = _node_bytes(node, source)
                    capture_name = (
                        "variable.builtin"
                        if name in {b"self", b"cls"}
                        else "variable.parameter"
                    )
                    yield _Capture(node, capture_name)

        if body is not None:
            for child in body.named_children:
                yield from _parameter_captures(child, source)
        return

    for child in root.named_children:
        yield from _parameter_captures(child, source)


def _has_oversized_line(source: bytes) -> bool:
    if len(source) <= _MAX_HIGHLIGHT_LINE_BYTES:
        return False

    line_start = 0
    while True:
        line_end = source.find(b"\n", line_start)
        if line_end < 0:
            return len(source) - line_start > _MAX_HIGHLIGHT_LINE_BYTES
        if line_end - line_start > _MAX_HIGHLIGHT_LINE_BYTES:
            return True
        line_start = line_end + 1


def _capture_groups(
    root: Node,
    query: Query,
    source: bytes,
    language_name: str,
) -> dict[str, list[Node]] | None:
    cursor = QueryCursor(query, match_limit=_MAX_CAPTURE_NODES)
    groups = cursor.captures(root)
    if cursor.did_exceed_match_limit:
        return None

    capture_count = sum(len(nodes) for nodes in groups.values())
    if capture_count > _MAX_CAPTURE_NODES:
        return None
    if language_name == "python":
        for capture in _parameter_captures(root, source):
            groups.setdefault(capture.name, []).append(capture.node)
            capture_count += 1
            if capture_count > _MAX_CAPTURE_NODES:
                return None

    return groups


def _nodes_in_source_order(nodes: list[Node]) -> list[Node]:
    previous = (-1, -1)
    for node in nodes:
        position = (node.start_byte, node.end_byte)
        if position < previous:
            return sorted(nodes, key=lambda item: (item.start_byte, item.end_byte))
        previous = position
    return nodes


def _capture_byte_spans(
    groups: Mapping[str, list[Node]],
    styles: Mapping[str, str],
) -> list[_ByteSpan]:
    spans: dict[tuple[int, int], _ByteSpan] = {}
    for name, nodes in sorted(
        groups.items(), key=lambda item: _capture_priority(item[0])
    ):
        style = _style_for_capture(name, styles)
        if style is None:
            continue
        for node in _nodes_in_source_order(nodes):
            start = node.start_byte
            end = node.end_byte
            if start >= end:
                continue
            key = (start, end)
            previous = spans.get(key)
            if previous is not None:
                if previous.style == style:
                    continue
                del spans[key]
            spans[key] = _ByteSpan(start, end, style)
    return list(spans.values())


def _character_offsets(source: bytes, spans: list[_ByteSpan]) -> dict[int, int]:
    boundaries = sorted({offset for span in spans for offset in (span.start, span.end)})
    offsets: dict[int, int] = {}
    previous_byte = 0
    previous_character = 0
    for boundary in boundaries:
        previous_character += len(source[previous_byte:boundary].decode("utf-8"))
        offsets[boundary] = previous_character
        previous_byte = boundary
    return offsets


def _normalize_language_name(language: str | None) -> str | None:
    if language is None:
        return None
    return _LANGUAGE_ALIASES.get(language.casefold().strip())


def _language_from_shebang(code: str) -> str | None:
    first_line = code.partition("\n")[0].strip().casefold()
    if not first_line.startswith("#!"):
        return None

    commands = []
    for token in first_line[2:].replace("\\", "/").split():
        command = token.rsplit("/", 1)[-1]
        if command.startswith("-") or "=" in command:
            continue
        commands.append(command)

    for command in commands:
        if command.startswith("python"):
            return "python"
        if command in {"bash", "dash", "ksh", "sh", "zsh"}:
            return "bash"
        if command in {"node", "nodejs"}:
            return "javascript"
        if command in {"bun", "deno"}:
            return "typescript"
        if command == "tsx":
            return "tsx"
        if command == "ruby":
            return "ruby"
        if command.startswith("php"):
            return "php"
    return None


def detect_tree_sitter_language(code: str, path: str) -> str | None:
    """Detect a bundled grammar from a source path or shebang."""
    filename = PurePath(path.replace("\\", "/")).name
    if language := _FILENAME_LANGUAGES.get(filename.casefold()):
        return language
    suffix = PurePath(filename).suffix
    if suffix == ".C":
        return "cpp"
    if language := _EXTENSION_LANGUAGES.get(suffix.casefold()):
        return language
    return _language_from_shebang(code)


def highlight_with_tree_sitter(
    code: str,
    *,
    language: str | None,
    capture_styles: Mapping[str, str],
) -> Content:
    """Highlight source code with a lazily loaded bundled grammar."""
    content = Content(code).stylize_before("$text")
    language_name = _normalize_language_name(language)
    if language_name is None or not code:
        return content

    try:
        source = code.encode("utf-8")
        if len(source) > _MAX_HIGHLIGHT_BYTES or _has_oversized_line(source):
            return content

        tree_language, query = _load_highlighter(language_name)
        tree = _parser_for(language_name, tree_language).parse(source)
        groups = _capture_groups(tree.root_node, query, source, language_name)
        if groups is None:
            return content
        byte_spans = _capture_byte_spans(groups, capture_styles)
        if code.isascii():
            spans = [Span(span.start, span.end, span.style) for span in byte_spans]
        else:
            offsets = _character_offsets(source, byte_spans)
            spans = [
                Span(offsets[span.start], offsets[span.end], span.style)
                for span in byte_spans
            ]
    except Exception:
        return content

    return content.add_spans(spans)
