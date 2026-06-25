from rit.core.diff import ParsedFilePatch, ParsedFilePatchSummary
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import FileViewedState, PRComment, PRFile


def _file_collection_module():
    import rit.state.file_collection as file_collection

    return file_collection


def _parsed_file(
    *,
    filename: str = "src/app.py",
    patch: str = "@@ -1 +1 @@\n-old\n+new",
    old_filename: str | None = None,
    is_new: bool = False,
    is_deleted: bool = False,
) -> ParsedFilePatch:
    diff = FileDiff(
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
    return ParsedFilePatch(diff=diff, patch=patch)


def test_append_file_indexes_comments_and_count_floor() -> None:
    file_collection = _file_collection_module()
    file = PRFile(filename="src/app.py")
    comment = PRComment(id=101, body="note", path="src/app.py")
    files: list[PRFile] = []
    files_by_filename: dict[str, PRFile] = {}

    result = file_collection.append_file(
        files,
        files_by_filename,
        {"src/app.py": [comment]},
        file,
        total_count=0,
    )

    assert result.added is True
    assert result.loaded_count == 1
    assert result.total_count == 1
    assert files == [file]
    assert files_by_filename["src/app.py"] is file
    assert file.comments == [comment]


def test_append_file_reads_filename_once_while_indexing_comments() -> None:
    file_collection = _file_collection_module()

    class File:
        comments: list[PRComment] = []

        def __init__(self, filename: str) -> None:
            self._filename = filename
            self.filename_reads = 0

        @property
        def filename(self) -> str:
            self.filename_reads += 1
            if self.filename_reads > 1:
                raise AssertionError("append_file should reuse the filename")
            return self._filename

    file = File("src/app.py")
    comment = PRComment(id=101, body="note", path="src/app.py")
    files: list[File] = []
    files_by_filename: dict[str, File] = {}

    result = file_collection.append_file(
        files,  # type: ignore[arg-type]
        files_by_filename,  # type: ignore[arg-type]
        {"src/app.py": [comment]},
        file,  # type: ignore[arg-type]
        total_count=0,
    )

    assert result.added is True
    assert files_by_filename["src/app.py"] is file
    assert file.comments == [comment]


def test_append_file_ignores_duplicate_filenames() -> None:
    file_collection = _file_collection_module()
    original = PRFile(filename="src/app.py", additions=1)
    duplicate = PRFile(filename="src/app.py", additions=99)
    files = [original]
    files_by_filename = {"src/app.py": original}

    result = file_collection.append_file(
        files,
        files_by_filename,
        {},
        duplicate,
        total_count=3,
    )

    assert result.added is False
    assert result.loaded_count == 1
    assert result.total_count == 3
    assert files == [original]
    assert files_by_filename["src/app.py"] is original


def test_append_file_preserves_known_total_without_max(monkeypatch) -> None:
    file_collection = _file_collection_module()
    file = PRFile(filename="src/app.py")

    monkeypatch.setattr(
        file_collection,
        "max",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("known file total should not call max")
        ),
        raising=False,
    )

    result = file_collection.append_file(
        [],
        {},
        {},
        file,
        total_count=5,
    )

    assert result.total_count == 5


def test_find_file_backfills_missing_filename_index() -> None:
    file_collection = _file_collection_module()
    file = PRFile(filename="src/app.py")
    files_by_filename: dict[str, PRFile] = {}

    found = file_collection.find_file("src/app.py", [file], files_by_filename)

    assert found is file
    assert files_by_filename["src/app.py"] is file


def test_find_file_single_file_skips_iteration() -> None:
    file_collection = _file_collection_module()

    class SingleFileList(list):
        def __iter__(self):
            raise AssertionError("single loaded file lookup should not iterate")

    file = PRFile(filename="src/app.py")
    files_by_filename: dict[str, PRFile] = {}

    found = file_collection.find_file(
        "src/app.py",
        SingleFileList([file]),
        files_by_filename,
    )

    assert found is file
    assert files_by_filename["src/app.py"] is file


def test_find_file_backfills_scanned_prefix() -> None:
    file_collection = _file_collection_module()
    first = PRFile(filename="src/first.py")
    second = PRFile(filename="src/second.py")
    files_by_filename: dict[str, PRFile] = {}

    found = file_collection.find_file(
        "src/second.py",
        [first, second],
        files_by_filename,
    )

    assert found is second
    assert files_by_filename["src/first.py"] is first
    assert files_by_filename["src/second.py"] is second


def test_select_file_result_backfills_index_and_returns_cached_diff() -> None:
    file_collection = _file_collection_module()
    file = PRFile(filename="src/app.py")
    diff = FileDiff(filename="src/app.py")
    files_by_filename: dict[str, PRFile] = {}

    result = file_collection.select_file(
        "src/app.py",
        files=[file],
        files_by_filename=files_by_filename,
        file_diffs={"src/app.py": diff},
    )

    assert result is not None
    assert result.filename == "src/app.py"
    assert result.diff == diff
    assert files_by_filename["src/app.py"] is file


def test_select_file_result_allows_cached_diff_without_loaded_file() -> None:
    file_collection = _file_collection_module()
    diff = FileDiff(filename="src/app.py")

    result = file_collection.select_file(
        "src/app.py",
        files=[],
        files_by_filename={},
        file_diffs={"src/app.py": diff},
    )

    assert result is not None
    assert result.filename == "src/app.py"
    assert result.diff == diff


def test_select_file_result_rejects_unknown_file() -> None:
    file_collection = _file_collection_module()

    result = file_collection.select_file(
        "missing.py",
        files=[],
        files_by_filename={},
        file_diffs={},
    )

    assert result is None


def test_load_file_diff_returns_cached_without_parsing() -> None:
    file_collection = _file_collection_module()
    cached = FileDiff(filename="src/app.py")
    calls = 0

    def parse(file: PRFile) -> FileDiff:
        nonlocal calls
        calls += 1
        return FileDiff(filename=file.filename)

    diff = file_collection.load_file_diff(
        "src/app.py",
        files=[PRFile(filename="src/app.py")],
        files_by_filename={},
        file_diffs={"src/app.py": cached},
        parse=parse,
    )

    assert diff is cached
    assert calls == 0


def test_load_file_diff_parses_file_and_caches_result() -> None:
    file_collection = _file_collection_module()
    file = PRFile(filename="src/app.py")
    files_by_filename: dict[str, PRFile] = {}
    file_diffs: dict[str, FileDiff] = {}

    diff = file_collection.load_file_diff(
        "src/app.py",
        files=[file],
        files_by_filename=files_by_filename,
        file_diffs=file_diffs,
        parse=lambda parsed_file: FileDiff(filename=parsed_file.filename),
    )

    assert diff is not None
    assert diff.filename == "src/app.py"
    assert file_diffs["src/app.py"] is diff
    assert files_by_filename["src/app.py"] is file


def test_load_file_diff_returns_none_for_unknown_file() -> None:
    file_collection = _file_collection_module()

    diff = file_collection.load_file_diff(
        "missing.py",
        files=[],
        files_by_filename={},
        file_diffs={},
        parse=lambda file: FileDiff(filename=file.filename),
    )

    assert diff is None


def test_cache_file_diff_keeps_existing_cache_entry() -> None:
    file_collection = _file_collection_module()
    existing = FileDiff(filename="src/app.py")
    replacement = FileDiff(filename="src/app.py")
    file_diffs = {"src/app.py": existing}

    cached = file_collection.cache_file_diff(
        "src/app.py",
        file_diffs,
        replacement,
    )

    assert cached is existing
    assert file_diffs["src/app.py"] is existing


def test_sync_file_comments_updates_existing_files() -> None:
    file_collection = _file_collection_module()
    first = PRFile(filename="src/app.py")
    second = PRFile(filename="src/lib.py")
    comment = PRComment(id=101, body="note", path="src/app.py")

    file_collection.sync_file_comments(
        [first, second],
        {"src/app.py": [comment]},
    )

    assert first.comments == [comment]
    assert second.comments == []


def test_apply_parsed_file_appends_new_file_and_caches_diff() -> None:
    file_collection = _file_collection_module()
    comment = PRComment(id=101, body="note", path="src/app.py")
    files: list[PRFile] = []
    files_by_filename: dict[str, PRFile] = {}
    file_diffs: dict[str, FileDiff] = {}

    result = file_collection.apply_parsed_file(
        files,
        files_by_filename,
        file_diffs,
        {"src/app.py": [comment]},
        _parsed_file(),
        total_count=0,
    )

    assert result.added is True
    assert result.loaded_count == 1
    assert result.total_count == 1
    assert files[0].filename == "src/app.py"
    assert files[0].patch == "@@ -1 +1 @@\n-old\n+new"
    assert files[0].comments == [comment]
    assert files_by_filename["src/app.py"] is files[0]
    assert file_diffs["src/app.py"].filename == "src/app.py"


def test_apply_parsed_file_reads_diff_filename_once(monkeypatch) -> None:
    file_collection = _file_collection_module()

    class Diff(FileDiff):
        def __init__(self, filename: str) -> None:
            super().__init__(filename=filename)
            self.filename_reads = 0

        @property
        def filename(self) -> str:
            self.filename_reads += 1
            if self.filename_reads > 1:
                raise AssertionError("parsed file apply should reuse diff filename")
            return self.__dict__["filename"]

        @filename.setter
        def filename(self, value: str) -> None:
            self.__dict__["filename"] = value

    diff = Diff("src/app.py")
    parsed = ParsedFilePatch(diff=diff, patch="@@ -1 +1 @@\n-old\n+new")
    files: list[PRFile] = []
    files_by_filename: dict[str, PRFile] = {}
    file_diffs: dict[str, FileDiff] = {}

    monkeypatch.setattr(
        file_collection,
        "file_from_diff",
        lambda _diff: PRFile(filename="src/app.py"),
    )

    result = file_collection.apply_parsed_file(
        files,
        files_by_filename,
        file_diffs,
        {},
        parsed,
        total_count=0,
    )

    assert result.added is True
    assert file_diffs["src/app.py"] is diff


def test_apply_parsed_file_updates_existing_summary_file() -> None:
    file_collection = _file_collection_module()
    existing = PRFile(filename="src/app.py", additions=0, patch="summary")
    files = [existing]
    files_by_filename = {"src/app.py": existing}
    file_diffs: dict[str, FileDiff] = {}

    result = file_collection.apply_parsed_file(
        files,
        files_by_filename,
        file_diffs,
        {},
        _parsed_file(old_filename="src/old_app.py", patch="full patch"),
        total_count=5,
    )

    assert result.added is False
    assert result.loaded_count == 1
    assert result.total_count == 5
    assert files == [existing]
    assert existing.status == "renamed"
    assert existing.additions == 1
    assert existing.deletions == 1
    assert existing.changes == 2
    assert existing.patch == "full patch"
    assert existing.previous_filename == "src/old_app.py"
    assert file_diffs["src/app.py"].old_filename == "src/old_app.py"


def test_apply_parsed_file_skips_when_diff_is_already_cached() -> None:
    file_collection = _file_collection_module()
    cached = FileDiff(filename="src/app.py")
    existing = PRFile(filename="src/app.py", additions=0, patch="cached")
    files = [existing]
    files_by_filename = {"src/app.py": existing}
    file_diffs = {"src/app.py": cached}

    result = file_collection.apply_parsed_file(
        files,
        files_by_filename,
        file_diffs,
        {},
        _parsed_file(patch="new patch"),
        total_count=1,
    )

    assert result.added is False
    assert result.loaded_count == 1
    assert result.total_count == 1
    assert existing.patch == "cached"
    assert file_diffs["src/app.py"] is cached


def test_apply_file_summary_appends_new_summary_file() -> None:
    file_collection = _file_collection_module()
    comment = PRComment(id=101, body="note", path="src/app.py")
    files: list[PRFile] = []
    files_by_filename: dict[str, PRFile] = {}
    summary = ParsedFilePatchSummary(
        filename="src/app.py",
        patch="diff --git a/src/app.py b/src/app.py",
        additions=3,
        deletions=2,
    )

    result = file_collection.apply_file_summary(
        files,
        files_by_filename,
        {"src/app.py": [comment]},
        summary,
        total_count=0,
    )

    assert result.added is True
    assert result.loaded_count == 1
    assert result.total_count == 1
    assert files[0].filename == "src/app.py"
    assert files[0].patch == "diff --git a/src/app.py b/src/app.py"
    assert files[0].additions == 3
    assert files[0].deletions == 2
    assert files[0].comments == [comment]
    assert files_by_filename["src/app.py"] is files[0]


def test_apply_file_summary_skips_existing_filename() -> None:
    file_collection = _file_collection_module()
    existing = PRFile(filename="src/app.py", additions=1, patch="existing")
    files = [existing]
    files_by_filename = {"src/app.py": existing}
    summary = ParsedFilePatchSummary(
        filename="src/app.py",
        patch="new summary",
        additions=9,
        deletions=9,
    )

    result = file_collection.apply_file_summary(
        files,
        files_by_filename,
        {},
        summary,
        total_count=5,
    )

    assert result.added is False
    assert result.loaded_count == 1
    assert result.total_count == 5
    assert files == [existing]
    assert existing.patch == "existing"
    assert existing.additions == 1


def test_apply_file_view_states_updates_valid_states() -> None:
    file_collection = _file_collection_module()
    viewed = PRFile(filename="src/app.py")
    dismissed = PRFile(filename="src/lib.py")

    file_collection.apply_file_view_states(
        [viewed, dismissed],
        {
            "src/app.py": "VIEWED",
            "src/lib.py": "DISMISSED",
        },
    )

    assert viewed.viewer_viewed_state == FileViewedState.VIEWED
    assert dismissed.viewer_viewed_state == FileViewedState.DISMISSED


def test_apply_file_view_states_skips_files_when_states_are_empty() -> None:
    file_collection = _file_collection_module()

    class Files(list[PRFile]):
        def __iter__(self):
            raise AssertionError("empty viewed states should not scan files")

    file_collection.apply_file_view_states(
        Files([PRFile(filename="src/app.py")]),
        {},
    )


def test_apply_file_view_states_ignores_missing_and_unknown_states() -> None:
    file_collection = _file_collection_module()
    missing = PRFile(filename="missing.py", viewer_viewed_state=FileViewedState.VIEWED)
    unknown = PRFile(filename="unknown.py")

    file_collection.apply_file_view_states(
        [missing, unknown],
        {
            "unknown.py": "NOT_A_STATE",
        },
    )

    assert missing.viewer_viewed_state == FileViewedState.VIEWED
    assert unknown.viewer_viewed_state == FileViewedState.UNVIEWED
