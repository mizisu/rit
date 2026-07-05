from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.ui.widgets.diff_folding import (
    FOLDED_VIEWED_FILE_MESSAGE,
    build_viewed_file_fold_diff,
    is_folded_placeholder_line,
)


def test_viewed_file_fold_diff_replaces_collapsed_file_with_placeholder() -> None:
    one = parse_patch("@@ -1,1 +1,1 @@\n-old\n+new", "one.py")
    two = parse_patch("@@ -1,1 +1,1 @@\n-before\n+after", "two.py")
    source = FileDiff(
        filename="All files",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=one.hunks[0].lines,
                starts_file=True,
                file_path="one.py",
            ),
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=two.hunks[0].lines,
                starts_file=True,
                file_path="two.py",
            ),
        ],
        show_hunk_headers=False,
    )

    folded, folded_files = build_viewed_file_fold_diff(
        source,
        is_collapsed=lambda path: path == "one.py",
    )

    assert folded is not source
    assert folded_files == frozenset({"one.py"})
    assert len(folded.hunks) == 2
    assert folded.hunks[0].file_path == "one.py"
    assert folded.hunks[0].starts_file
    assert folded.hunks[0].lines == [
        DiffLine(
            old_line_no=None,
            new_line_no=1,
            new_content=FOLDED_VIEWED_FILE_MESSAGE,
            file_path="one.py",
        )
    ]
    assert is_folded_placeholder_line(folded.hunks[0].lines[0])
    assert folded.hunks[1] is source.hunks[1]


def test_viewed_file_fold_diff_returns_source_when_no_files_are_collapsed() -> None:
    source = parse_patch("@@ -1,1 +1,1 @@\n-old\n+new", "one.py")

    folded, folded_files = build_viewed_file_fold_diff(
        source,
        is_collapsed=lambda _path: False,
    )

    assert folded is source
    assert folded_files == frozenset()
