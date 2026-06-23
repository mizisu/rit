from rit.ui.widgets.diff_selection_text import (
    normal_yank_for_line,
    selected_text_for_visual_range,
    visual_yank_for_range,
)


def test_selected_text_for_visual_range_copies_line_mode_with_trailing_newline() -> None:
    assert (
        selected_text_for_visual_range(
            ["line1", "line2", "line3"],
            visual_anchor_line=0,
            visual_anchor_column=3,
            cursor_line=1,
            cursor_column=1,
            visual_type="line",
        )
        == "line1\nline2\n"
    )


def test_selected_text_for_visual_range_copies_same_line_char_range() -> None:
    assert (
        selected_text_for_visual_range(
            ["abcdef"],
            visual_anchor_line=0,
            visual_anchor_column=4,
            cursor_line=0,
            cursor_column=1,
            visual_type="char",
        )
        == "bcde"
    )


def test_selected_text_for_visual_range_copies_forward_multiline_char_range() -> None:
    assert (
        selected_text_for_visual_range(
            ["abcdef", "middle", "uvwxyz"],
            visual_anchor_line=0,
            visual_anchor_column=2,
            cursor_line=2,
            cursor_column=3,
            visual_type="char",
        )
        == "cdef\nmiddle\nuvwx"
    )


def test_selected_text_for_visual_range_copies_backward_multiline_char_range() -> None:
    assert (
        selected_text_for_visual_range(
            ["abcdef", "middle", "uvwxyz"],
            visual_anchor_line=2,
            visual_anchor_column=4,
            cursor_line=0,
            cursor_column=1,
            visual_type="char",
        )
        == "bcdef\nmiddle\nuvwxy"
    )


def test_selected_text_for_visual_range_handles_empty_line_mode_selection() -> None:
    assert (
        selected_text_for_visual_range(
            [""],
            visual_anchor_line=0,
            visual_anchor_column=0,
            cursor_line=0,
            cursor_column=0,
            visual_type="line",
        )
        == ""
    )


def test_normal_yank_for_line_returns_line_text_and_message() -> None:
    yank = normal_yank_for_line("line2")

    assert yank.text == "line2\n"
    assert yank.success_message == "Copied 1 line"


def test_normal_yank_for_line_preserves_empty_line_newline() -> None:
    yank = normal_yank_for_line("")

    assert yank.text == "\n"
    assert yank.success_message == "Copied 1 line"


def test_visual_yank_for_range_returns_line_mode_text_and_message() -> None:
    yank = visual_yank_for_range(
        ["line1", "line2", "line3"],
        visual_anchor_line=0,
        visual_anchor_column=3,
        cursor_line=1,
        cursor_column=1,
        visual_type="line",
    )

    assert yank.text == "line1\nline2\n"
    assert yank.success_message == "Copied 2 lines"


def test_visual_yank_for_range_returns_singular_character_message() -> None:
    yank = visual_yank_for_range(
        ["abc"],
        visual_anchor_line=0,
        visual_anchor_column=1,
        cursor_line=0,
        cursor_column=1,
        visual_type="char",
    )

    assert yank.text == "b"
    assert yank.success_message == "Copied 1 character"


def test_visual_yank_for_range_counts_copied_characters_in_multiline_text() -> None:
    yank = visual_yank_for_range(
        ["abc", "de"],
        visual_anchor_line=0,
        visual_anchor_column=1,
        cursor_line=1,
        cursor_column=1,
        visual_type="char",
    )

    assert yank.text == "bc\nde"
    assert yank.success_message == "Copied 5 characters"
