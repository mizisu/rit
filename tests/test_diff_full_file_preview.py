from rit.core.diff import parse_patch
from rit.core.types import DiffLine
import rit.ui.widgets.diff_full_file_preview as full_preview_module
from rit.ui.widgets.diff_full_file_preview import (
    FullFilePreviewAction,
    FullFileRestorePosition,
    build_full_file_diff,
    choose_full_file_preview_action,
    full_file_restore_line_index,
    full_file_anchor_line_index,
    full_file_preview_target,
    nearest_full_file_anchor_for_deleted_line,
    selected_full_file_anchor,
)


def _content(line_count: int) -> str:
    return "\n".join(f"line {line}" for line in range(1, line_count + 1))


def test_selected_full_file_anchor_uses_current_new_line() -> None:
    diff = parse_patch(
        """@@ -6,3 +6,3 @@
 line 6
-line 7 old
+line 7
 line 8""",
        "preview.py",
    )
    selected_line = diff.hunks[0].lines[1]

    assert selected_full_file_anchor("preview.py", selected_line, diff) == 7


def test_selected_full_file_anchor_ignores_other_combined_file_sections() -> None:
    diff = parse_patch(
        """@@ -1,2 +1,2 @@
 line 1
-old
+new""",
        "combined.diff",
    )
    selected_line = diff.hunks[0].lines[1]
    selected_line.file_path = "src/other.py"

    assert selected_full_file_anchor("src/current.py", selected_line, diff) is None


def test_deleted_line_anchor_prefers_next_then_previous_new_line() -> None:
    middle_delete = parse_patch(
        """@@ -6,3 +6,2 @@
 line 6
-line 7 removed
 line 8""",
        "preview.py",
    )
    trailing_delete = parse_patch(
        """@@ -6,2 +6,1 @@
 line 6
-line 7 removed""",
        "preview.py",
    )

    assert nearest_full_file_anchor_for_deleted_line(7, middle_delete) == 7
    assert nearest_full_file_anchor_for_deleted_line(7, trailing_delete) == 7


def test_deleted_line_anchor_scans_neighbors_without_copying_slices() -> None:
    diff = parse_patch(
        """@@ -1,5 +1,4 @@
 line 1
-line 2 removed
-line 3 removed
 line 4
 line 5""",
        "preview.py",
    )

    class NoSliceLines(list):
        def __getitem__(self, index):
            if isinstance(index, slice):
                raise AssertionError("anchor lookup should not copy hunk slices")
            return super().__getitem__(index)

    diff.hunks[0].lines = NoSliceLines(diff.hunks[0].lines)

    assert nearest_full_file_anchor_for_deleted_line(3, diff) == 2


def test_build_full_file_diff_reuses_single_pass_change_counts() -> None:
    class SinglePassDiff(type(parse_patch("@@ -1 +1 @@\n-old\n+new", "preview.py"))):
        @property
        def total_additions(self) -> int:
            raise AssertionError("full-file preview should not count additions alone")

        @property
        def total_deletions(self) -> int:
            raise AssertionError("full-file preview should not count deletions alone")

    source = parse_patch(
        """@@ -1,2 +1,2 @@
 line 1
-old
+new""",
        "preview.py",
    )
    source_diff = SinglePassDiff(
        filename=source.filename,
        hunks=source.hunks,
        old_filename=source.old_filename,
        is_new=source.is_new,
        is_deleted=source.is_deleted,
        is_binary=source.is_binary,
        is_fully_refined=source.is_fully_refined,
        show_hunk_headers=source.show_hunk_headers,
    )

    full_diff = build_full_file_diff(
        "preview.py",
        "line 1\nnew",
        source_diff=source_diff,
    )

    assert full_diff.hunks[0].file_additions == 1
    assert full_diff.hunks[0].file_deletions == 1


def test_choose_full_file_preview_action_requires_file_and_store() -> None:
    assert choose_full_file_preview_action(
        current_file=None,
        selected_file="preview.py",
        showing_full_file=False,
        has_store=True,
    ) == FullFilePreviewAction(kind="ignore")
    assert choose_full_file_preview_action(
        current_file="preview.py",
        selected_file="preview.py",
        showing_full_file=False,
        has_store=False,
    ) == FullFilePreviewAction(kind="ignore")


def test_choose_full_file_preview_action_restores_when_preview_is_showing() -> None:
    assert choose_full_file_preview_action(
        current_file="preview.py",
        selected_file="other.py",
        showing_full_file=True,
        has_store=True,
    ) == FullFilePreviewAction(kind="restore")


def test_choose_full_file_preview_action_requests_or_loads_target_file() -> None:
    assert choose_full_file_preview_action(
        current_file="preview.py",
        selected_file="other.py",
        showing_full_file=False,
        has_store=True,
    ) == FullFilePreviewAction(kind="request_file", filename="other.py")
    assert choose_full_file_preview_action(
        current_file="preview.py",
        selected_file="preview.py",
        showing_full_file=False,
        has_store=True,
    ) == FullFilePreviewAction(kind="load_current", filename="preview.py")


def test_full_file_restore_line_index_ignores_missing_or_empty_state() -> None:
    position = FullFileRestorePosition(
        line=2,
        column=4,
        cursor_pane="new",
        active_pane="old",
        viewport_offset=None,
    )

    assert full_file_restore_line_index(None, line_count=3) is None
    assert full_file_restore_line_index(position, line_count=0) is None


def test_full_file_restore_line_index_clamps_saved_line() -> None:
    position = FullFileRestorePosition(
        line=8,
        column=4,
        cursor_pane="new",
        active_pane="old",
        viewport_offset=2,
    )

    assert full_file_restore_line_index(position, line_count=3) == 2


def test_full_file_preview_target_prefers_selected_combined_file_path() -> None:
    diff = parse_patch(
        """@@ -1,2 +1,2 @@
 line 1
-old
+new""",
        "All files",
    )
    selected_line = diff.hunks[0].lines[1]
    selected_line.file_path = "src/selected.py"

    assert full_file_preview_target("All files", selected_line) == "src/selected.py"


def test_full_file_preview_target_falls_back_to_current_file() -> None:
    diff = parse_patch(
        """@@ -1,2 +1,2 @@
 line 1
-old
+new""",
        "preview.py",
    )

    assert full_file_preview_target("preview.py", diff.hunks[0].lines[1]) == "preview.py"
    assert full_file_preview_target(None, diff.hunks[0].lines[1]) is None


def test_full_file_anchor_line_index_ignores_missing_or_empty_anchor() -> None:
    assert full_file_anchor_line_index(None, {10: 3}) is None
    assert full_file_anchor_line_index(10, {}) is None


def test_full_file_anchor_line_index_clamps_to_available_lines() -> None:
    line_index_by_new_number = {10: 0, 11: 1, 12: 2}

    assert full_file_anchor_line_index(10, line_index_by_new_number) == 0
    assert full_file_anchor_line_index(3, line_index_by_new_number) == 0
    assert full_file_anchor_line_index(99, line_index_by_new_number) == 2


def test_full_file_anchor_line_index_reuses_available_line_bounds() -> None:
    class NoIterLineIndex(dict):
        def __iter__(self):
            raise AssertionError("full-file anchor should reuse planned line bounds")

    line_index_by_new_number = NoIterLineIndex({10: 0, 11: 1, 12: 2})

    assert (
        full_file_anchor_line_index(
            99,
            line_index_by_new_number,
            available_line_bounds=(10, 12),
        )
        == 2
    )


def test_build_full_file_diff_handles_empty_file_preview() -> None:
    diff = build_full_file_diff("empty.py", "")

    assert diff.filename == "empty.py"
    assert diff.show_hunk_headers is False
    assert len(diff.hunks) == 1
    assert diff.hunks[0].header == "empty file"
    assert diff.hunks[0].starts_file is True
    assert diff.hunks[0].file_path == "empty.py"
    assert diff.hunks[0].lines == []


def test_build_full_file_diff_splits_context_around_source_hunks() -> None:
    source_diff = parse_patch(
        """@@ -2,2 +2,2 @@ first section
 line 2
-old 3
+new 3
@@ -6,2 +6,3 @@ second section
 line 6
+new 7
 line 8""",
        "preview.py",
    )

    diff = build_full_file_diff(
        "preview.py",
        _content(9),
        source_diff=source_diff,
    )

    assert [hunk.header for hunk in diff.hunks] == [
        "context before hunk 1",
        "change hunk 1/2  first section",
        "context between hunks 1-2",
        "change hunk 2/2  second section",
        "context after hunk 2",
    ]
    assert [(hunk.new_start, hunk.new_count) for hunk in diff.hunks] == [
        (1, 1),
        (2, 2),
        (4, 2),
        (6, 3),
        (9, 1),
    ]


def test_build_full_file_diff_reuses_clean_source_hunk_header_without_strip() -> None:
    class CleanHeader(str):
        def strip(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("clean full-preview hunk headers should not strip")

    source_diff = parse_patch(
        """@@ -2,2 +2,2 @@ first section
 line 2
-old 3
+new 3""",
        "preview.py",
    )
    source_diff.hunks[0].header = CleanHeader("first section")

    diff = build_full_file_diff(
        "preview.py",
        _content(4),
        source_diff=source_diff,
    )

    assert diff.hunks[1].header == "change hunk 1/1  first section"


def test_full_preview_hunk_reuses_full_line_range_without_slice() -> None:
    class NoSliceLines(list[DiffLine]):
        def __getitem__(self, index):
            if isinstance(index, slice):
                raise AssertionError("full-range preview hunk should reuse lines")
            return super().__getitem__(index)

    lines = NoSliceLines(
        [
            DiffLine(None, 1, new_content="one"),
            DiffLine(None, 2, new_content="two"),
        ]
    )

    hunk = full_preview_module._make_full_preview_hunk(
        "preview.py",
        lines,
        start=1,
        end=2,
        header="full file",
        starts_file=True,
        file_change_counts=(0, 0),
    )

    assert hunk.lines is lines
