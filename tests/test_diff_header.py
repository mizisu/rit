from rich.text import Text
from rich.cells import cell_len

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import FileViewedState, PRFile
from rit.ui.widgets.diff_header import (
    aggregate_file_change_stats,
    append_change_stats,
    build_file_header_text,
    build_diff_header_text,
    change_stats_plain,
    file_header_min_width,
    truncate_middle,
)


def test_build_diff_header_text_escapes_path_markup() -> None:
    file = PRFile(filename="src/[red]app.py", additions=2, deletions=1)

    text = build_diff_header_text(
        current_file="src/[red]app.py",
        file=file,
        showing_full_file=False,
        preview_location="",
    )

    assert "[bold #cad3f5]src/\\[red]app.py[/]" in text
    assert "● Unviewed" in text
    assert "[bold #ed8796]-1[/] [bold #a6da95]+2[/]" in text


def test_build_diff_header_text_includes_preview_location_when_showing_full_file() -> None:
    file = PRFile(
        filename="src/app.py",
        additions=0,
        deletions=0,
        viewer_viewed_state=FileViewedState.VIEWED,
    )

    text = build_diff_header_text(
        current_file="src/app.py",
        file=file,
        showing_full_file=True,
        preview_location="line 4/10  [section]",
    )

    assert "✓ Viewed" in text
    assert "[dim]no textual changes[/]" in text
    assert "[dim italic]preview[/]" in text
    assert "[dim]line 4/10  \\[section][/]" in text


def test_build_diff_header_text_handles_missing_file_selection() -> None:
    assert (
        build_diff_header_text(
            current_file=None,
            file=None,
            showing_full_file=False,
            preview_location="",
        )
        == "Select a file to view diff"
    )


def test_change_stats_plain_matches_header_stats_order() -> None:
    assert change_stats_plain(additions=3, deletions=2) == "-2 +3"
    assert change_stats_plain(additions=3, deletions=0) == "+3"
    assert change_stats_plain(additions=0, deletions=2) == "-2"
    assert change_stats_plain(additions=0, deletions=0) == "no textual changes"


def test_append_change_stats_applies_consistent_text_styles() -> None:
    text = Text()

    append_change_stats(text, additions=3, deletions=2)

    assert text.plain == "-2 +3"
    spans = [(span.start, span.end, span.style) for span in text.spans]
    assert spans == [
        (0, 2, "bold #ed8796"),
        (3, 5, "bold #a6da95"),
    ]


def test_append_change_stats_renders_empty_stats_dim() -> None:
    text = Text()

    append_change_stats(text, additions=0, deletions=0)

    assert text.plain == "no textual changes"
    assert [(span.start, span.end, span.style) for span in text.spans] == [
        (0, len("no textual changes"), "dim")
    ]


def test_build_file_header_text_preserves_rename_styles_when_untruncated() -> None:
    text = build_file_header_text(
        path="new.py",
        old_path="old.py",
        additions=3,
        deletions=2,
        path_budget=40,
    )

    assert text.plain == "▾ old.py -> new.py  -2 +3"
    spans = [(span.start, span.end, span.style) for span in text.spans]
    assert (2, 8, "dim") in spans
    assert (8, 12, "dim") in spans
    assert (12, 18, "bold #cad3f5") in spans


def test_build_file_header_text_truncates_display_path_within_budget() -> None:
    text = build_file_header_text(
        path="src/components/very_long_file.py",
        old_path=None,
        additions=1,
        deletions=0,
        path_budget=14,
    )

    display_path = text.plain.removeprefix("▾ ").removesuffix("  +1")

    assert cell_len(display_path) <= 14
    assert "..." in display_path
    assert text.spans[1].style == "bold #cad3f5"


def test_file_header_min_width_accounts_for_rename_path() -> None:
    width = file_header_min_width(
        path="new.py",
        old_path="old/location.py",
        stats_plain="+1",
    )

    assert width == cell_len("old/location.py -> new.py") + cell_len("+1") + 8


def test_truncate_middle_preserves_ascii_head_tail_within_budget() -> None:
    truncated = truncate_middle("src/components/very_long_file.py", 18)

    assert truncated == "src/com..._file.py"
    assert cell_len(truncated) <= 18


def test_truncate_middle_respects_wide_character_cell_width() -> None:
    truncated = truncate_middle("界界界界界界界界界界", 7)

    assert cell_len(truncated) <= 7
    assert truncated.startswith("界")
    assert truncated.endswith("界")
    assert "..." in truncated


def test_aggregate_file_change_stats_counts_only_matching_file_paths() -> None:
    diff = FileDiff(
        filename="fallback.py",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=2,
                new_start=1,
                new_count=2,
                file_path="one.py",
                lines=[
                    DiffLine(None, 1, is_added=True),
                    DiffLine(2, 2, is_modified=True),
                    DiffLine(3, None, is_deleted=True, file_path="two.py"),
                ],
            ),
            DiffHunk(
                old_start=10,
                old_count=1,
                new_start=10,
                new_count=1,
                lines=[
                    DiffLine(None, 10, is_added=True),
                ],
            ),
        ],
    )

    assert aggregate_file_change_stats(diff, "one.py") == (2, 1)
    assert aggregate_file_change_stats(diff, "two.py") == (0, 1)
    assert aggregate_file_change_stats(diff, "fallback.py") == (1, 0)
    assert aggregate_file_change_stats(None, "one.py") == (0, 0)
