from textual.content import Content

from rit.ui.widgets.diff_selection_content import apply_selection_to_code_content


def _spans(content: Content) -> list[tuple[int, int, str]]:
    return [(span.start, span.end, str(span.style)) for span in content.spans]


def test_apply_selection_to_code_content_leaves_empty_lines_unchanged() -> None:
    content = Content(" ").stylize("$success", 0, 1)

    result = apply_selection_to_code_content(
        content,
        line_text="",
        selection_start=0,
        selection_end=4,
        has_cursor=True,
        cursor_col=0,
    )

    assert result.plain == " "
    assert _spans(result) == [(0, 1, "$success")]


def test_apply_selection_to_code_content_reverses_selected_range() -> None:
    result = apply_selection_to_code_content(
        Content("abcdef"),
        line_text="abcdef",
        selection_start=1,
        selection_end=3,
        has_cursor=False,
        cursor_col=None,
    )

    assert result.plain == "abcdef"
    assert _spans(result) == [(1, 4, "reverse dim")]


def test_apply_selection_to_code_content_clamps_and_normalizes_range() -> None:
    result = apply_selection_to_code_content(
        Content("abcdef"),
        line_text="abcdef",
        selection_start=8,
        selection_end=-2,
        has_cursor=False,
        cursor_col=None,
    )

    assert result.plain == "abcdef"
    assert _spans(result) == [(0, 6, "reverse dim")]


def test_apply_selection_to_code_content_overlays_cursor_cell() -> None:
    result = apply_selection_to_code_content(
        Content("abcdef"),
        line_text="abcdef",
        selection_start=1,
        selection_end=4,
        has_cursor=True,
        cursor_col=2,
    )

    assert result.plain == "abcdef"
    assert _spans(result) == [(1, 5, "reverse dim"), (2, 3, "reverse bold")]


def test_apply_selection_to_code_content_ignores_cursor_past_line_end() -> None:
    result = apply_selection_to_code_content(
        Content("abcdef"),
        line_text="abcdef",
        selection_start=1,
        selection_end=4,
        has_cursor=True,
        cursor_col=6,
    )

    assert result.plain == "abcdef"
    assert _spans(result) == [(1, 5, "reverse dim")]
