from __future__ import annotations

from collections.abc import Iterable

from textual import highlight
from textual.content import Content, Span
from textual.highlight import HighlightTheme

from rit.core.highlight_theme import RitHighlightTheme, RitLightHighlightTheme
from rit.core.types import DiffLine, FileDiff


_HIGHLIGHTER_PREWARMED = False


def _syntax_theme_class(dark_mode: bool) -> type[HighlightTheme]:
    return RitHighlightTheme if dark_mode else RitLightHighlightTheme


def prewarm_highlighter() -> None:
    """Warm up Textual / Pygments highlight machinery once per process (expensive cold start)."""
    global _HIGHLIGHTER_PREWARMED
    if _HIGHLIGHTER_PREWARMED:
        return

    sample = "def warmup():\n    return 1\n"
    language = highlight.guess_language(sample, "warmup.py")
    for dark_mode in (True, False):
        highlight.highlight(
            sample,
            language=language,
            path="warmup.py",
            theme=_syntax_theme_class(dark_mode),
        )
    _HIGHLIGHTER_PREWARMED = True


def _iter_diff_lines(diff: FileDiff) -> Iterable[DiffLine]:
    for hunk in diff.hunks:
        yield from hunk.lines


def _collect_line_text(lines: Iterable[DiffLine]) -> tuple[list[str], list[str]]:
    old_lines_text: list[str] = []
    new_lines_text: list[str] = []

    for line in lines:
        if line.has_old_side:
            old_lines_text.append(line.old_content)
        if line.has_new_side:
            new_lines_text.append(line.new_content)

    return old_lines_text, new_lines_text


def _highlight_text_lines(
    *,
    filename: str,
    old_lines_text: list[str],
    new_lines_text: list[str],
    dark_mode: bool = True,
) -> tuple[list[Content], list[Content]]:
    language = highlight.guess_language(
        "\n".join(old_lines_text or new_lines_text), filename
    )
    theme = _syntax_theme_class(dark_mode)

    old_highlighted = Content.empty()
    if old_lines_text:
        old_highlighted = highlight.highlight(
            "\n".join(old_lines_text),
            language=language,
            path=filename,
            theme=theme,
        )

    new_highlighted = Content.empty()
    if new_lines_text:
        new_highlighted = highlight.highlight(
            "\n".join(new_lines_text),
            language=language,
            path=filename,
            theme=theme,
        )

    old_content_lines = old_highlighted.split("\n") if old_lines_text else []
    new_content_lines = new_highlighted.split("\n") if new_lines_text else []

    return old_content_lines, new_content_lines


def highlight_diff(
    diff: FileDiff, *, dark_mode: bool = True
) -> tuple[list[Content], list[Content]]:
    old_lines_text, new_lines_text = _collect_line_text(_iter_diff_lines(diff))
    return _highlight_text_lines(
        filename=diff.filename,
        old_lines_text=old_lines_text,
        new_lines_text=new_lines_text,
        dark_mode=dark_mode,
    )


def apply_word_diff_spans(
    content: Content,
    word_diff_segments: list[tuple[str, str]],  # (text, type: "+", "-", " ")
) -> Content:
    spans: list[Span] = []
    pos = 0

    for text, change_type in word_diff_segments:
        length = len(text)

        if change_type == "+":
            spans.append(Span(pos, pos + length, "on $success 40%"))
        elif change_type == "-":
            spans.append(Span(pos, pos + length, "on $error 40%"))

        pos += length

    return content.add_spans(spans)


def _apply_highlighted_content_to_lines(
    lines: list[DiffLine],
    *,
    old_lines: list[Content],
    new_lines: list[Content],
    include_word_diff: bool,
) -> None:
    old_idx = 0
    new_idx = 0

    for line in lines:
        if line.has_old_side and old_idx < len(old_lines):
            line.highlighted_old_content = old_lines[old_idx]
            if include_word_diff and line.is_modified and line.old_segments:
                line.highlighted_old_content = _apply_word_diff_to_content(
                    line.highlighted_old_content,
                    line.old_segments,
                )
            old_idx += 1

        if line.has_new_side and new_idx < len(new_lines):
            line.highlighted_new_content = new_lines[new_idx]
            if include_word_diff and line.is_modified and line.new_segments:
                line.highlighted_new_content = _apply_word_diff_to_content(
                    line.highlighted_new_content,
                    line.new_segments,
                )
            new_idx += 1


def highlight_lines_for_diff(
    diff: FileDiff,
    *,
    include_word_diff: bool = True,
    dark_mode: bool = True,
) -> None:
    """Apply syntax highlighting to all lines in a FileDiff in-place."""
    lines = list(_iter_diff_lines(diff))
    old_lines_text, new_lines_text = _collect_line_text(lines)
    old_lines, new_lines = _highlight_text_lines(
        filename=diff.filename,
        old_lines_text=old_lines_text,
        new_lines_text=new_lines_text,
        dark_mode=dark_mode,
    )
    _apply_highlighted_content_to_lines(
        lines,
        old_lines=old_lines,
        new_lines=new_lines,
        include_word_diff=include_word_diff,
    )


def highlight_lines_for_diff_range(
    diff: FileDiff,
    start_line: int,
    end_line: int,
    *,
    include_word_diff: bool = True,
    dark_mode: bool = True,
) -> None:
    """Apply syntax highlighting to a contiguous diff-line window in-place."""
    if start_line > end_line:
        return

    selected_lines = [
        line
        for line in _iter_diff_lines(diff)
        if start_line <= line.line_index <= end_line
    ]
    if not selected_lines:
        return

    old_lines_text, new_lines_text = _collect_line_text(selected_lines)
    old_lines, new_lines = _highlight_text_lines(
        filename=diff.filename,
        old_lines_text=old_lines_text,
        new_lines_text=new_lines_text,
        dark_mode=dark_mode,
    )
    _apply_highlighted_content_to_lines(
        selected_lines,
        old_lines=old_lines,
        new_lines=new_lines,
        include_word_diff=include_word_diff,
    )


def _apply_word_diff_to_content(content: Content, segments: list) -> Content:
    from rit.core.types import SegmentType

    spans: list[Span] = []
    pos = 0

    for segment in segments:
        length = len(segment.text)

        if segment.type == SegmentType.ADDED:
            spans.append(Span(pos, pos + length, "on $success 40%"))
        elif segment.type == SegmentType.DELETED:
            spans.append(Span(pos, pos + length, "on $error 40%"))

        pos += length

    return content.add_spans(spans)
