from collections.abc import Iterable, Sequence

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


def test_file_from_diff_reads_change_counts_in_one_pass() -> None:
    file_projection = _file_projection_module()

    class SinglePassDiff(FileDiff):
        @property
        def total_additions(self) -> int:
            raise AssertionError("file projection should not count additions alone")

        @property
        def total_deletions(self) -> int:
            raise AssertionError("file projection should not count deletions alone")

    diff = SinglePassDiff(
        filename="src/app.py",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[
                    DiffLine(
                        old_line_no=None,
                        new_line_no=1,
                        is_added=True,
                        new_content="new",
                    ),
                    DiffLine(
                        old_line_no=1,
                        new_line_no=None,
                        is_deleted=True,
                        old_content="old",
                    ),
                    DiffLine(
                        old_line_no=2,
                        new_line_no=2,
                        is_modified=True,
                        old_content="a",
                        new_content="b",
                    ),
                ],
            )
        ],
    )

    file = file_projection.file_from_diff(diff)

    assert (file.additions, file.deletions, file.changes) == (2, 2, 4)


def test_file_from_diff_reads_old_filename_once() -> None:
    file_projection = _file_projection_module()

    class Diff(FileDiff):
        def __init__(self) -> None:
            super().__init__(filename="new.py")
            self.old_filename_reads = 0

        @property
        def old_filename(self) -> str:
            self.old_filename_reads += 1
            if self.old_filename_reads > 1:
                raise AssertionError("diff old filename should be reused")
            return "old.py"

        @old_filename.setter
        def old_filename(self, value: str | None) -> None:
            self.__dict__["old_filename"] = value

    diff = Diff()

    file = file_projection.file_from_diff(diff)

    assert file.status == "renamed"
    assert file.previous_filename == "old.py"


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


def test_file_from_summary_reads_metadata_fields_once() -> None:
    file_projection = _file_projection_module()

    class Summary:
        filename = "new.py"
        patch = "diff --git a/old.py b/new.py"
        is_new = False
        is_deleted = False

        def __init__(self) -> None:
            self.additions_reads = 0
            self.deletions_reads = 0
            self.old_filename_reads = 0

        @property
        def additions(self) -> int:
            self.additions_reads += 1
            if self.additions_reads > 1:
                raise AssertionError("summary additions should be reused")
            return 3

        @property
        def deletions(self) -> int:
            self.deletions_reads += 1
            if self.deletions_reads > 1:
                raise AssertionError("summary deletions should be reused")
            return 2

        @property
        def old_filename(self) -> str:
            self.old_filename_reads += 1
            if self.old_filename_reads > 1:
                raise AssertionError("summary old filename should be reused")
            return "old.py"

    summary = Summary()

    file = file_projection.file_from_summary(summary)  # type: ignore[arg-type]

    assert file.filename == "new.py"
    assert file.status == "renamed"
    assert file.changes == 5
    assert file.previous_filename == "old.py"


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


def test_diff_from_file_patch_reads_status_once(monkeypatch) -> None:
    file_projection = _file_projection_module()

    class File:
        filename = "src/app.py"
        patch = "@@ -1 +1 @@\n-old\n+new"
        previous_filename = "src/old.py"

        def __init__(self) -> None:
            self.status_reads = 0

        @property
        def status(self) -> str:
            self.status_reads += 1
            if self.status_reads > 1:
                raise AssertionError("file status should be reused")
            return "added"

    parsed = FileDiff(filename="src/app.py")
    monkeypatch.setattr(
        file_projection,
        "parse_patch",
        lambda _patch, _filename: parsed,
    )

    diff = file_projection.diff_from_file_patch(File())  # type: ignore[arg-type]

    assert diff is parsed
    assert diff.old_filename == "src/old.py"
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


def test_parse_file_patch_summaries_skips_empty_sequence_iteration() -> None:
    file_projection = _file_projection_module()

    class EmptySections(Sequence[str]):
        def __len__(self) -> int:
            return 0

        def __getitem__(self, index: int) -> str:
            raise IndexError(index)

        def __iter__(self):
            raise AssertionError("empty diff section sequence should not be iterated")

    assert file_projection.parse_file_patch_summaries(EmptySections()) == []
