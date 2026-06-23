from collections.abc import Iterable

from rit.core.diff import ParsedFilePatchSummary
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile


def _file_projection_module():
    import rit.state.file_projection as file_projection

    return file_projection


def _diff(
    *,
    filename: str = "src/app.py",
    old_filename: str | None = None,
    is_new: bool = False,
    is_deleted: bool = False,
) -> FileDiff:
    return FileDiff(
        filename=filename,
        old_filename=old_filename,
        is_new=is_new,
        is_deleted=is_deleted,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[
                    DiffLine(
                        old_line_no=1,
                        new_line_no=None,
                        old_content="old",
                        is_deleted=True,
                    ),
                    DiffLine(
                        old_line_no=None,
                        new_line_no=1,
                        new_content="new",
                        is_added=True,
                    ),
                ],
            )
        ],
    )


def test_file_from_diff_maps_status_and_change_counts() -> None:
    file_projection = _file_projection_module()

    added = file_projection.file_from_diff(_diff(filename="new.py", is_new=True))
    removed = file_projection.file_from_diff(
        _diff(filename="old.py", is_deleted=True)
    )
    renamed = file_projection.file_from_diff(
        _diff(filename="new.py", old_filename="old.py")
    )

    assert (added.status, added.additions, added.deletions, added.changes) == (
        "added",
        1,
        1,
        2,
    )
    assert removed.status == "removed"
    assert renamed.status == "renamed"
    assert renamed.previous_filename == "old.py"


def test_file_from_summary_preserves_patch_and_counts() -> None:
    file_projection = _file_projection_module()
    summary = ParsedFilePatchSummary(
        filename="new.py",
        old_filename="old.py",
        patch="diff --git a/old.py b/new.py",
        additions=3,
        deletions=2,
    )

    file = file_projection.file_from_summary(summary)

    assert file == PRFile(
        filename="new.py",
        status="renamed",
        additions=3,
        deletions=2,
        changes=5,
        patch="diff --git a/old.py b/new.py",
        previousFilename="old.py",
    )


def test_diff_from_file_patch_restores_rest_metadata() -> None:
    file_projection = _file_projection_module()
    file = PRFile(
        filename="new.py",
        status="added",
        patch="@@ -0,0 +1 @@\n+new",
        previousFilename="old.py",
    )

    diff = file_projection.diff_from_file_patch(file)

    assert diff.filename == "new.py"
    assert diff.old_filename == "old.py"
    assert diff.is_new is True
    assert diff.is_deleted is False


def test_parse_file_patch_summaries_skips_non_file_sections() -> None:
    file_projection = _file_projection_module()
    sections: Iterable[str] = [
        "not a diff",
        "diff --git a/one.py b/one.py\n--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n-old\n+new",
    ]

    summaries = file_projection.parse_file_patch_summaries(sections)

    assert [summary.filename for summary in summaries] == ["one.py"]
