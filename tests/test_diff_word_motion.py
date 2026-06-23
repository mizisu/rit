from rit.ui.widgets.diff_word_motion import (
    first_word_start,
    last_word_start,
    next_word_end,
    next_word_start,
    previous_word_start,
)


def test_first_word_start_skips_leading_whitespace() -> None:
    assert first_word_start("   alpha") == 3


def test_first_word_start_returns_length_for_whitespace_only_text() -> None:
    assert first_word_start("   ") == 3


def test_next_word_start_moves_between_word_tokens() -> None:
    assert next_word_start("alpha beta", 0) == 6


def test_next_word_start_treats_punctuation_as_a_token() -> None:
    assert next_word_start("alpha += beta", 0) == 6
    assert next_word_start("alpha += beta", 6) == 9


def test_previous_word_start_moves_to_previous_token_start() -> None:
    assert previous_word_start("alpha beta", 10) == 6


def test_previous_word_start_treats_punctuation_as_a_token() -> None:
    assert previous_word_start("alpha += beta", 9) == 6


def test_next_word_end_returns_current_or_next_token_end() -> None:
    assert next_word_end("alpha beta", 0) == 4
    assert next_word_end("alpha beta", 4) == 9


def test_last_word_start_returns_last_token_start() -> None:
    assert last_word_start("alpha beta   ") == 6


def test_last_word_start_returns_none_for_empty_or_whitespace_text() -> None:
    assert last_word_start("") is None
    assert last_word_start("   ") is None
