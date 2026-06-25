from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.ui.widgets.diff_location import (
    full_preview_location_label,
    line_index_for_location,
    row_for_line_and_pane,
)
from rit.ui.widgets.diff_types import RenderedRow


def test_line_index_for_location_uses_same_file_line_indexes() -> None:
    diff = FileDiff(filename="src/app.py")

    assert (
        line_index_for_location(
            diff,
            "src/app.py",
            42,
            "RIGHT",
            old_line_index={},
            new_line_index={42: 7},
        )
        == 7
    )


def test_line_index_for_location_finds_line_in_combined_file_section() -> None:
    diff = FileDiff(
        filename="All files",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                starts_file=True,
                file_path="one.py",
                lines=[DiffLine(old_line_no=1, new_line_no=1, line_index=0)],
            ),
            DiffHunk(
                old_start=9,
                old_count=1,
                new_start=9,
                new_count=1,
                starts_file=True,
                file_path="two.py",
                lines=[DiffLine(old_line_no=9, new_line_no=9, line_index=1)],
            ),
        ],
    )

    assert (
        line_index_for_location(
            diff,
            "two.py",
            9,
            "LEFT",
            old_line_index={},
            new_line_index={},
        )
        == 1
    )


def test_line_index_for_location_uses_file_specific_index_without_scanning() -> None:
    class ExplodingLines(list[DiffLine]):
        def __iter__(self):
            raise AssertionError("line lookup should use file-specific index")

    diff = FileDiff(
        filename="All files",
        hunks=[
            DiffHunk(
                old_start=9,
                old_count=1,
                new_start=9,
                new_count=1,
                starts_file=True,
                file_path="two.py",
                lines=ExplodingLines(
                    [DiffLine(old_line_no=9, new_line_no=9, line_index=1)]
                ),
            ),
        ],
    )

    assert (
        line_index_for_location(
            diff,
            "two.py",
            9,
            "RIGHT",
            old_line_index={},
            new_line_index={},
            old_file_line_index={},
            new_file_line_index={("two.py", 9): 77},
        )
        == 77
    )


def test_line_index_for_location_stops_at_next_file_section() -> None:
    diff = FileDiff(
        filename="All files",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                starts_file=True,
                file_path="one.py",
                lines=[DiffLine(old_line_no=1, new_line_no=1, line_index=0)],
            ),
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                starts_file=True,
                file_path="two.py",
                lines=[DiffLine(old_line_no=1, new_line_no=1, line_index=1)],
            ),
        ],
    )

    assert (
        line_index_for_location(
            diff,
            "one.py",
            1,
            "RIGHT",
            old_line_index={},
            new_line_index={},
        )
        == 0
    )


def test_row_for_line_and_pane_prefers_exact_or_auto_side() -> None:
    fallback = RenderedRow(
        mode="unified",
        row_index=0,
        line_index=5,
        hunk_index=0,
        kind="modified-old",
        side="old",
        anchor_id="old",
        old_line_no=1,
        new_line_no=None,
    )
    exact = RenderedRow(
        mode="unified",
        row_index=1,
        line_index=5,
        hunk_index=0,
        kind="modified-new",
        side="new",
        anchor_id="new",
        old_line_no=None,
        new_line_no=1,
    )
    auto = RenderedRow(
        mode="unified",
        row_index=2,
        line_index=6,
        hunk_index=0,
        kind="context",
        side="auto",
        anchor_id="auto",
        old_line_no=2,
        new_line_no=2,
    )

    assert row_for_line_and_pane([fallback, exact], 5, "new") is exact
    assert row_for_line_and_pane([auto], 6, "old") is auto


def test_row_for_line_and_pane_falls_back_to_first_matching_line() -> None:
    fallback = RenderedRow(
        mode="split",
        row_index=0,
        line_index=5,
        hunk_index=0,
        kind="modified-old",
        side="old",
        anchor_id="old",
        old_line_no=1,
        new_line_no=None,
    )

    assert row_for_line_and_pane([fallback], 5, "new") is fallback
    assert row_for_line_and_pane([fallback], 9, "new") is None


def test_full_preview_location_label_includes_trimmed_hunk_header() -> None:
    diff = FileDiff(
        filename="src/app.py",
        hunks=[
            DiffHunk(old_start=1, old_count=1, new_start=1, new_count=1),
            DiffHunk(
                old_start=4,
                old_count=1,
                new_start=4,
                new_count=1,
                header="  def render()  ",
            ),
        ],
    )
    line = DiffLine(old_line_no=4, new_line_no=4, line_index=1)

    label = full_preview_location_label(
        line=line,
        total_lines=9,
        diff=diff,
        hunk_index=1,
    )

    assert label == "line 4/9  def render()"


def test_full_preview_location_label_reuses_clean_hunk_header_without_strip() -> None:
    class CleanHeader(str):
        def strip(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("clean hunk headers should not be stripped")

    diff = FileDiff(
        filename="src/app.py",
        hunks=[
            DiffHunk(
                old_start=4,
                old_count=1,
                new_start=4,
                new_count=1,
                header=CleanHeader("def render()"),
            ),
        ],
    )
    line = DiffLine(old_line_no=4, new_line_no=4, line_index=0)

    label = full_preview_location_label(
        line=line,
        total_lines=9,
        diff=diff,
        hunk_index=0,
    )

    assert label == "line 4/9  def render()"


def test_full_preview_location_label_falls_back_to_section_label() -> None:
    diff = FileDiff(
        filename="src/app.py",
        hunks=[
            DiffHunk(old_start=1, old_count=1, new_start=1, new_count=1),
            DiffHunk(old_start=4, old_count=1, new_start=4, new_count=1),
        ],
    )
    line = DiffLine(old_line_no=4, new_line_no=None, line_index=1)

    label = full_preview_location_label(
        line=line,
        total_lines=9,
        diff=diff,
        hunk_index=1,
    )

    assert label == "line 4/9  section 2/2"


def test_full_preview_location_label_handles_missing_context() -> None:
    line = DiffLine(old_line_no=None, new_line_no=None, line_index=6)

    assert (
        full_preview_location_label(
            line=line,
            total_lines=9,
            diff=None,
            hunk_index=None,
        )
        == "line 7/9"
    )
    assert (
        full_preview_location_label(
            line=line,
            total_lines=9,
            diff=FileDiff(filename="src/app.py"),
            hunk_index=0,
        )
        == "line 7/9"
    )
    assert (
        full_preview_location_label(
            line=None,
            total_lines=9,
            diff=None,
            hunk_index=None,
        )
        == ""
    )
