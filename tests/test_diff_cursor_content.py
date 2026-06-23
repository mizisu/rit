from textual.content import Content

from rit.ui.widgets.diff_cursor_content import apply_cursor_to_code_content


def _spans(content: Content) -> list[tuple[int, int, str]]:
    return [(span.start, span.end, str(span.style)) for span in content.spans]


def test_apply_cursor_to_code_content_leaves_content_when_cursor_is_inactive() -> None:
    content = Content("abc").stylize("$success", 0, 1)

    result = apply_cursor_to_code_content(
        content,
        line_text="abc",
        has_cursor=False,
        cursor_col=1,
    )

    assert result.plain == "abc"
    assert _spans(result) == [(0, 1, "$success")]


def test_apply_cursor_to_code_content_reverses_cursor_cell() -> None:
    result = apply_cursor_to_code_content(
        Content("abc"),
        line_text="abc",
        has_cursor=True,
        cursor_col=1,
    )

    assert result.plain == "abc"
    assert _spans(result) == [(1, 2, "reverse")]


def test_apply_cursor_to_code_content_reverses_empty_fallback_cell() -> None:
    result = apply_cursor_to_code_content(
        Content(" "),
        line_text="",
        has_cursor=True,
        cursor_col=0,
    )

    assert result.plain == " "
    assert _spans(result) == [(0, 1, "reverse")]


def test_apply_cursor_to_code_content_ignores_cursor_past_line_end() -> None:
    result = apply_cursor_to_code_content(
        Content("abc"),
        line_text="abc",
        has_cursor=True,
        cursor_col=3,
    )

    assert result.plain == "abc"
    assert _spans(result) == []
