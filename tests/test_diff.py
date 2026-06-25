"""Tests for diff algorithm."""

from rit.core import diff as diff_module
from rit.core import highlighting as highlighting_module
from rit.core import types as types_module
from rit.core.diff import (
    compute_line_diff,
    compute_word_diff,
    parse_file_patch_summary,
    parse_multi_file_patch,
    parse_patch,
)
from textual.content import Content

from rit.core.highlighting import (
    apply_word_diff_spans,
    highlight_lines_for_diff,
    highlight_lines_for_diff_range,
)
from rit.core.types import DiffHunk, DiffLine, FileDiff, InlineSegment, SegmentType


class TestParsePatch:
    """Tests for parse_patch function."""

    def test_parse_patch_does_not_materialize_line_list(self):
        class NoSplitLines(str):
            def splitlines(self, *_args, **_kwargs):
                raise AssertionError("parse_patch should stream patch lines")

        patch = NoSplitLines("""@@ -1 +1 @@
-old
+new""")

        diff = parse_patch(patch, "test.py")

        assert len(diff.hunks) == 1
        assert diff.total_additions == 1
        assert diff.total_deletions == 1

    def test_empty_file_diff_additions_skip_sum(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "sum",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("empty diff additions should not build a sum iterator")
            ),
            raising=False,
        )

        assert FileDiff(filename="image.png").total_additions == 0

    def test_empty_file_diff_deletions_skip_sum(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "sum",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("empty diff deletions should not build a sum iterator")
            ),
            raising=False,
        )

        assert FileDiff(filename="image.png").total_deletions == 0

    def test_single_line_file_diff_additions_skip_sum(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "sum",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("single-line diff additions should not build a sum")
            ),
            raising=False,
        )
        diff = FileDiff(
            filename="test.py",
            hunks=[
                DiffHunk(
                    old_start=1,
                    old_count=0,
                    new_start=1,
                    new_count=1,
                    lines=[DiffLine(old_line_no=None, new_line_no=1, is_added=True)],
                )
            ],
        )

        assert diff.total_additions == 1

    def test_single_line_file_diff_deletions_skip_sum(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "sum",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("single-line diff deletions should not build a sum")
            ),
            raising=False,
        )
        diff = FileDiff(
            filename="test.py",
            hunks=[
                DiffHunk(
                    old_start=1,
                    old_count=1,
                    new_start=1,
                    new_count=0,
                    lines=[
                        DiffLine(old_line_no=1, new_line_no=None, is_deleted=True)
                    ],
                )
            ],
        )

        assert diff.total_deletions == 1

    def test_single_hunk_file_diff_additions_skip_sum(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "sum",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("single-hunk diff additions should not build a sum")
            ),
            raising=False,
        )
        diff = FileDiff(
            filename="test.py",
            hunks=[
                DiffHunk(
                    old_start=1,
                    old_count=1,
                    new_start=1,
                    new_count=2,
                    lines=[
                        DiffLine(old_line_no=1, new_line_no=1, old_content="same"),
                        DiffLine(
                            old_line_no=None,
                            new_line_no=2,
                            new_content="added",
                            is_added=True,
                        ),
                    ],
                )
            ],
        )

        assert diff.total_additions == 1

    def test_single_hunk_file_diff_deletions_skip_sum(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "sum",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("single-hunk diff deletions should not build a sum")
            ),
            raising=False,
        )
        diff = FileDiff(
            filename="test.py",
            hunks=[
                DiffHunk(
                    old_start=1,
                    old_count=2,
                    new_start=1,
                    new_count=1,
                    lines=[
                        DiffLine(old_line_no=1, new_line_no=1, old_content="same"),
                        DiffLine(
                            old_line_no=2,
                            new_line_no=None,
                            old_content="deleted",
                            is_deleted=True,
                        ),
                    ],
                )
            ],
        )

        assert diff.total_deletions == 1

    def test_empty_hunk_has_changes_skips_any(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "any",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("empty hunk change check should not build an any iterator")
            ),
            raising=False,
        )

        assert (
            DiffHunk(old_start=1, old_count=0, new_start=1, new_count=0).has_changes
            is False
        )

    def test_single_line_hunk_has_changes_skips_any(self, monkeypatch):
        monkeypatch.setattr(
            types_module,
            "any",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError(
                    "single-line hunk change check should not build an any iterator"
                )
            ),
            raising=False,
        )

        assert DiffHunk(
            old_start=1,
            old_count=0,
            new_start=1,
            new_count=1,
            lines=[DiffLine(old_line_no=None, new_line_no=1, is_added=True)],
        ).has_changes is True

    def test_parse_file_patch_summary_does_not_materialize_line_list(self):
        class NoSplitLines(str):
            def splitlines(self, *_args, **_kwargs):
                raise AssertionError("file patch summaries should stream patch lines")

        section = NoSplitLines("""diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1 +1 @@
-old
+new""")

        summary = parse_file_patch_summary(section)

        assert summary is not None
        assert summary.filename == "test.py"
        assert summary.additions == 1
        assert summary.deletions == 1

    def test_parse_file_patch_summary_scans_section_once(self, monkeypatch):
        section = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -1 +1 @@
-old
+new"""
        real_iter_patch_lines = diff_module._iter_patch_lines
        calls = 0

        def counting_iter_patch_lines(text: str):
            nonlocal calls
            calls += 1
            return real_iter_patch_lines(text)

        monkeypatch.setattr(diff_module, "_iter_patch_lines", counting_iter_patch_lines)

        summary = parse_file_patch_summary(section)

        assert summary is not None
        assert summary.additions == 1
        assert summary.deletions == 1
        assert calls == 1

    def test_parse_multi_file_patch_preserves_file_metadata(self):
        patch = """diff --git a/old.py b/new.py
similarity index 88%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1 +1 @@
-old
+new
diff --git a/added.py b/added.py
new file mode 100644
--- /dev/null
+++ b/added.py
@@ -0,0 +1 @@
+added
"""

        files = parse_multi_file_patch(patch)

        assert [file.diff.filename for file in files] == ["new.py", "added.py"]
        assert files[0].diff.old_filename == "old.py"
        assert files[0].diff.total_additions == 1
        assert files[0].diff.total_deletions == 1
        assert files[1].diff.is_new is True
        assert files[1].diff.total_additions == 1

    def test_parse_multi_file_patch_streams_sections_without_split_list(
        self,
        monkeypatch,
    ):
        patch = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1 +1 @@
-old
+new
diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@ -1 +1 @@
-before
+after"""

        monkeypatch.setattr(
            diff_module,
            "_split_multi_file_patch",
            lambda _patch: (_ for _ in ()).throw(
                AssertionError("multi-file parsing should stream sections")
            ),
        )

        files = parse_multi_file_patch(patch)

        assert [file.diff.filename for file in files] == ["one.py", "two.py"]
        assert [file.diff.total_additions for file in files] == [1, 1]

    def test_simple_add(self):
        """Test parsing a simple addition."""
        patch = """@@ -1,3 +1,4 @@
 line1
 line2
+new line
 line3"""
        diff = parse_patch(patch, "test.py")

        assert diff.filename == "test.py"
        assert len(diff.hunks) == 1

        hunk = diff.hunks[0]
        assert hunk.old_start == 1
        assert hunk.old_count == 3
        assert hunk.new_start == 1
        assert hunk.new_count == 4

        # Check lines
        assert len(hunk.lines) == 4
        assert hunk.lines[0].is_context
        assert hunk.lines[1].is_context
        assert hunk.lines[2].is_added
        assert hunk.lines[2].new_content == "new line"
        assert hunk.lines[3].is_context

    def test_simple_delete(self):
        """Test parsing a simple deletion."""
        patch = """@@ -1,4 +1,3 @@
 line1
-deleted line
 line2
 line3"""
        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert hunk.lines[1].is_deleted
        assert hunk.lines[1].old_content == "deleted line"

    def test_modified_line_with_word_diff(self):
        """Test that similar lines are detected as modified with word diff."""
        patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""
        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        # The delete+add pair should be merged into a modified line
        modified_lines = [line for line in hunk.lines if line.is_modified]

        assert len(modified_lines) == 1
        modified = modified_lines[0]
        assert modified.old_content == "old content here"
        assert modified.new_content == "new content here"
        assert modified.has_word_diff
        assert len(modified.old_segments) > 0
        assert len(modified.new_segments) > 0

    def test_multiple_hunks(self):
        """Test parsing multiple hunks."""
        patch = """@@ -1,3 +1,3 @@
 line1
-old
+new
 line3
@@ -10,3 +10,4 @@
 line10
+added
 line11
 line12"""
        diff = parse_patch(patch, "test.py")

        assert len(diff.hunks) == 2
        assert diff.hunks[0].old_start == 1
        assert diff.hunks[1].old_start == 10

    def test_empty_patch(self):
        """Test parsing empty patch."""
        diff = parse_patch("", "test.py")
        assert diff.filename == "test.py"
        assert len(diff.hunks) == 0

    def test_hunk_with_context_header(self):
        """Test hunk with function/class context."""
        patch = """@@ -10,3 +10,4 @@ def my_function():
 line1
+added
 line2
 line3"""
        diff = parse_patch(patch, "test.py")

        assert diff.hunks[0].header == "def my_function():"

    def test_long_modified_line_skips_word_diff_segments(self):
        """Very long modified lines should avoid expensive word-level segments."""
        old_line = "a" * 1200
        new_line = "a" * 1199 + "b"
        patch = f"@@ -1,1 +1,1 @@\n-{old_line}\n+{new_line}"

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert len(hunk.lines) == 1
        modified = hunk.lines[0]
        assert modified.is_modified
        assert modified.old_segments == []
        assert modified.new_segments == []

    def test_multi_line_replace_aligns_matching_pairs_before_extra_adds(self):
        """Replace blocks should pair matching old/new lines before trailing adds."""
        patch = """@@ -1,4 +1,5 @@
 line1
-old alpha
-old beta
+new alpha
+new beta
+new gamma
 line2"""

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert len(hunk.lines) == 5

        first_change = hunk.lines[1]
        second_change = hunk.lines[2]
        trailing_add = hunk.lines[3]

        assert first_change.is_modified
        assert first_change.old_line_no == 2
        assert first_change.new_line_no == 2
        assert first_change.old_content == "old alpha"
        assert first_change.new_content == "new alpha"

        assert second_change.is_modified
        assert second_change.old_line_no == 3
        assert second_change.new_line_no == 3
        assert second_change.old_content == "old beta"
        assert second_change.new_content == "new beta"

        assert trailing_add.is_added
        assert trailing_add.old_line_no is None
        assert trailing_add.new_line_no == 4
        assert trailing_add.new_content == "new gamma"

    def test_multi_line_replace_aligns_matching_pairs_before_extra_deletes(self):
        """Replace blocks should pair matching old/new lines before trailing deletes."""
        patch = """@@ -1,5 +1,4 @@
 line1
-old alpha
-old beta
-old gamma
+new alpha
+new beta
 line2"""

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert len(hunk.lines) == 5

        first_change = hunk.lines[1]
        second_change = hunk.lines[2]
        trailing_delete = hunk.lines[3]

        assert first_change.is_modified
        assert first_change.old_line_no == 2
        assert first_change.new_line_no == 2
        assert first_change.old_content == "old alpha"
        assert first_change.new_content == "new alpha"

        assert second_change.is_modified
        assert second_change.old_line_no == 3
        assert second_change.new_line_no == 3
        assert second_change.old_content == "old beta"
        assert second_change.new_content == "new beta"

        assert trailing_delete.is_deleted
        assert trailing_delete.old_line_no == 4
        assert trailing_delete.new_line_no is None
        assert trailing_delete.old_content == "old gamma"

    def test_multi_line_replace_aligns_matching_pairs_after_leading_add(self):
        """Replace blocks should keep similar pairs together after a leading add."""
        patch = """@@ -1,4 +1,5 @@
 line1
-old alpha
-old beta
+new gamma
+new alpha
+new beta
 line2"""

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert len(hunk.lines) == 5

        leading_add = hunk.lines[1]
        first_change = hunk.lines[2]
        second_change = hunk.lines[3]

        assert leading_add.is_added
        assert leading_add.old_line_no is None
        assert leading_add.new_line_no == 2
        assert leading_add.new_content == "new gamma"

        assert first_change.is_modified
        assert first_change.old_line_no == 2
        assert first_change.new_line_no == 3
        assert first_change.old_content == "old alpha"
        assert first_change.new_content == "new alpha"

        assert second_change.is_modified
        assert second_change.old_line_no == 3
        assert second_change.new_line_no == 4
        assert second_change.old_content == "old beta"
        assert second_change.new_content == "new beta"

    def test_multi_line_replace_aligns_matching_pairs_after_leading_delete(self):
        """Replace blocks should keep similar pairs together after a leading delete."""
        patch = """@@ -1,5 +1,4 @@
 line1
-old gamma
-old alpha
-old beta
+new alpha
+new beta
 line2"""

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert len(hunk.lines) == 5

        leading_delete = hunk.lines[1]
        first_change = hunk.lines[2]
        second_change = hunk.lines[3]

        assert leading_delete.is_deleted
        assert leading_delete.old_line_no == 2
        assert leading_delete.new_line_no is None
        assert leading_delete.old_content == "old gamma"

        assert first_change.is_modified
        assert first_change.old_line_no == 3
        assert first_change.new_line_no == 2
        assert first_change.old_content == "old alpha"
        assert first_change.new_content == "new alpha"

        assert second_change.is_modified
        assert second_change.old_line_no == 4
        assert second_change.new_line_no == 3
        assert second_change.old_content == "old beta"
        assert second_change.new_content == "new beta"

    def test_multi_line_replace_aligns_single_old_line_after_leading_adds(self):
        """One-to-many replace blocks should still find the matching old/new line."""
        patch = """@@ -1,1 +1,3 @@
-def method(self):
+@classmethod
+
+def method(cls):"""

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert len(hunk.lines) == 3

        assert hunk.lines[0].is_added
        assert hunk.lines[0].new_line_no == 1
        assert hunk.lines[0].new_content == "@classmethod"

        assert hunk.lines[1].is_added
        assert hunk.lines[1].new_line_no == 2
        assert hunk.lines[1].new_content == ""

        assert hunk.lines[2].is_modified
        assert hunk.lines[2].old_line_no == 1
        assert hunk.lines[2].new_line_no == 3
        assert hunk.lines[2].old_content == "def method(self):"
        assert hunk.lines[2].new_content == "def method(cls):"

    def test_multi_line_replace_keeps_unrelated_deletes_before_adds(self):
        """Unpaired low-similarity replace lines should keep git's delete/add order."""
        patch = """@@ -1,2 +1,2 @@
-old aaa
-old bbb
+new xxx
+new yyy"""

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert [(line.old_content, line.new_content) for line in hunk.lines] == [
            ("old aaa", ""),
            ("old bbb", ""),
            ("", "new xxx"),
            ("", "new yyy"),
        ]
        assert [line.is_deleted for line in hunk.lines] == [True, True, False, False]
        assert [line.is_added for line in hunk.lines] == [False, False, True, True]

    def test_large_replace_blocks_skip_eager_modified_line_refinement(self):
        deleted = "\n".join(f"-old value {i}" for i in range(24))
        added = "\n".join(f"+new value {i}" for i in range(24))
        patch = f"@@ -1,24 +1,24 @@\n{deleted}\n{added}"

        diff = parse_patch(patch, "generated.txt")

        hunk = diff.hunks[0]
        assert diff.is_fully_refined is False
        assert len(hunk.lines) == 48
        assert not any(line.is_modified for line in hunk.lines)
        assert sum(line.is_deleted for line in hunk.lines) == 24
        assert sum(line.is_added for line in hunk.lines) == 24

    def test_eager_refinement_can_override_large_replace_budget(self):
        deleted = "\n".join(f"-old value {i}" for i in range(24))
        added = "\n".join(f"+new value {i}" for i in range(24))
        patch = f"@@ -1,24 +1,24 @@\n{deleted}\n{added}"

        diff = parse_patch(patch, "generated.txt", refine="eager")

        assert diff.is_fully_refined is True
        assert any(line.is_modified for line in diff.hunks[0].lines)

    def test_modified_line_identification_does_not_slice_hunk_lines(self):
        class NoSliceLines(list):
            def __getitem__(self, index):
                if isinstance(index, slice):
                    raise AssertionError("modified-line detection should not copy hunk slices")
                return super().__getitem__(index)

        hunk = DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            lines=NoSliceLines(
                [
                    DiffLine(1, None, old_content="old value", is_deleted=True),
                    DiffLine(None, 1, new_content="new value", is_added=True),
                ]
            ),
        )

        assert diff_module._identify_modified_lines(hunk, block_cell_budget=16) is True
        assert len(hunk.lines) == 1
        assert hunk.lines[0].is_modified

    def test_modified_line_realignment_passes_lazy_content_windows(self, monkeypatch):
        seen: dict[str, str] = {}

        def inspected_compute_line_diff(old_lines, new_lines):
            assert not isinstance(old_lines, list)
            assert not isinstance(new_lines, list)
            seen["old"] = old_lines[0]
            seen["new"] = new_lines[0]
            return compute_line_diff(old_lines, new_lines)

        monkeypatch.setattr(
            diff_module, "compute_line_diff", inspected_compute_line_diff
        )
        hunk = DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            lines=[
                DiffLine(1, None, old_content="old value", is_deleted=True),
                DiffLine(None, 1, new_content="new value", is_added=True),
            ],
        )

        assert diff_module._identify_modified_lines(hunk, block_cell_budget=16) is True
        assert seen == {"old": "old value", "new": "new value"}
        assert hunk.lines[0].is_modified


class TestComputeWordDiff:
    """Tests for word-level diff."""

    def test_identical_strings(self):
        """Test identical strings."""
        old_segments, new_segments = compute_word_diff("hello world", "hello world")

        assert len(old_segments) == 1
        assert old_segments[0].type == SegmentType.UNCHANGED
        assert old_segments[0].text == "hello world"

    def test_empty_old_text_word_diff_skips_tokenization(self, monkeypatch):
        def tokenize(_text: str):
            raise AssertionError("empty old text word diff should not tokenize")

        monkeypatch.setattr(diff_module, "_tokenize", tokenize)

        old_segments, new_segments = compute_word_diff("", "added")

        assert old_segments == []
        assert new_segments == [InlineSegment(text="added", type=SegmentType.ADDED)]

    def test_single_word_change(self):
        """Test changing a single word."""
        old_segments, new_segments = compute_word_diff("hello world", "hello universe")

        # Should have: "hello " unchanged, then different parts
        assert any(s.type == SegmentType.UNCHANGED for s in old_segments)
        assert any(s.type == SegmentType.DELETED for s in old_segments)
        assert any(s.type == SegmentType.ADDED for s in new_segments)

    def test_single_token_replace_preserves_shared_substrings(self):
        """Single-token replacements should keep shared prefix/suffix unhighlighted."""
        old_segments, new_segments = compute_word_diff("old_value", "new_value")

        assert any(
            s.type == SegmentType.DELETED and s.text == "old" for s in old_segments
        )
        assert any(
            s.type == SegmentType.ADDED and s.text == "new" for s in new_segments
        )
        assert any(
            s.type == SegmentType.UNCHANGED and s.text == "_value" for s in old_segments
        )
        assert any(
            s.type == SegmentType.UNCHANGED and s.text == "_value" for s in new_segments
        )

    def test_single_token_replace_avoids_weird_middle_substring_matches(self):
        """Single-token replacements should prefer clean boundary matches."""
        old_segments, new_segments = compute_word_diff("@classmethod", "@staticmethod")

        assert [(segment.text, segment.type) for segment in old_segments] == [
            ("@", SegmentType.UNCHANGED),
            ("class", SegmentType.DELETED),
            ("method", SegmentType.UNCHANGED),
        ]
        assert [(segment.text, segment.type) for segment in new_segments] == [
            ("@", SegmentType.UNCHANGED),
            ("static", SegmentType.ADDED),
            ("method", SegmentType.UNCHANGED),
        ]

    def test_multi_token_replace_keeps_code_changes_grouped_naturally(self):
        """Code-oriented replacements should avoid single-character carryover."""
        old_segments, new_segments = compute_word_diff(
            "def get_cache_key(cls, company_id: int) -> str:",
            "def get_cache_key(company_id: int, version_id: int | None) -> str:",
        )

        assert [(segment.text, segment.type) for segment in old_segments] == [
            ("def get_cache_key(", SegmentType.UNCHANGED),
            ("cls, company_id: int)", SegmentType.DELETED),
            (" -> str:", SegmentType.UNCHANGED),
        ]
        assert [(segment.text, segment.type) for segment in new_segments] == [
            ("def get_cache_key(", SegmentType.UNCHANGED),
            ("company_id: int, version_id: int | None)", SegmentType.ADDED),
            (" -> str:", SegmentType.UNCHANGED),
        ]

    def test_compute_word_diff_joins_token_ranges_without_slicing(
        self,
        monkeypatch,
    ):
        class NoSliceTokens(list):
            def __getitem__(self, index):
                if isinstance(index, slice):
                    raise AssertionError("word diff should not copy token slices")
                return super().__getitem__(index)

        tokenized = {
            "old": NoSliceTokens(["alpha", " ", "beta"]),
            "new": NoSliceTokens(["alpha", " ", "gamma"]),
        }
        monkeypatch.setattr(diff_module, "_tokenize", lambda text: tokenized[text])

        old_segments, new_segments = compute_word_diff("old", "new")

        assert any(
            segment.type == SegmentType.DELETED and segment.text
            for segment in old_segments
        )
        assert any(
            segment.type == SegmentType.ADDED and segment.text
            for segment in new_segments
        )

    def test_join_range_single_token_skips_range_iteration(self, monkeypatch):
        def fail_range(*_args, **_kwargs):
            raise AssertionError("single-token join should not build a range")

        monkeypatch.setattr(diff_module, "range", fail_range, raising=False)

        assert diff_module._join_range(["alpha"], 0, 1) == "alpha"

    def test_merge_segments_absorbs_whitespace_without_copying_input(self):
        class NoIterSegments(list):
            def __iter__(self):
                raise AssertionError("segment merge should not copy segment lists")

            def __getitem__(self, index):
                if isinstance(index, slice):
                    raise AssertionError("segment merge should not slice segment lists")
                return super().__getitem__(index)

        segments = NoIterSegments(
            [
                InlineSegment("old", SegmentType.DELETED),
                InlineSegment(" ", SegmentType.UNCHANGED),
                InlineSegment("new", SegmentType.DELETED),
            ]
        )

        merged = diff_module._merge_segments(segments)

        assert [(segment.text, segment.type) for segment in merged] == [
            ("old new", SegmentType.DELETED)
        ]

    def test_single_segment_merge_skips_range_iteration(self, monkeypatch):
        def fail_range(*_args, **_kwargs):
            raise AssertionError("single segment merge should not build a range")

        monkeypatch.setattr(diff_module, "range", fail_range, raising=False)
        segment = InlineSegment("old", SegmentType.DELETED)

        assert diff_module._merge_segments([segment]) == [segment]

    def test_insertion(self):
        """Test insertion."""
        old_segments, new_segments = compute_word_diff("abc", "abXc")

        # old should have 'abc' split into unchanged parts
        # new should have 'X' as added
        added = [s for s in new_segments if s.type == SegmentType.ADDED]
        assert any("X" in s.text for s in added)

    def test_deletion(self):
        """Test deletion."""
        old_segments, new_segments = compute_word_diff("abXc", "abc")

        deleted = [s for s in old_segments if s.type == SegmentType.DELETED]
        assert any("X" in s.text for s in deleted)


class TestComputeLineDiff:
    """Tests for line-level diff."""

    def test_identical_files(self):
        """Test identical files."""
        lines = ["line1", "line2", "line3"]
        result = compute_line_diff(lines, lines)

        assert all(line.is_context for line in result)
        assert len(result) == 3

    def test_same_line_sequence_skips_sequence_matcher(self, monkeypatch):
        def sequence_matcher(*_args, **_kwargs):
            raise AssertionError("same line sequence should not build SequenceMatcher")

        monkeypatch.setattr(diff_module, "SequenceMatcher", sequence_matcher)
        lines = ["line1", "line2"]

        result = compute_line_diff(lines, lines)

        assert [(line.old_content, line.new_content) for line in result] == [
            ("line1", "line1"),
            ("line2", "line2"),
        ]
        assert all(line.is_context for line in result)

    def test_added_line(self):
        """Test adding a line."""
        old = ["line1", "line2"]
        new = ["line1", "inserted", "line2"]
        result = compute_line_diff(old, new)

        added = [line for line in result if line.is_added]
        assert len(added) == 1
        assert added[0].new_content == "inserted"

    def test_deleted_line(self):
        """Test deleting a line."""
        old = ["line1", "deleted", "line2"]
        new = ["line1", "line2"]
        result = compute_line_diff(old, new)

        deleted = [line for line in result if line.is_deleted]
        assert len(deleted) == 1
        assert deleted[0].old_content == "deleted"

    def test_modified_line(self):
        """Test modifying a line (similar content)."""
        old = ["hello world"]
        new = ["hello universe"]
        result = compute_line_diff(old, new)

        # Should be detected as modified with word diff
        modified = [line for line in result if line.is_modified]
        assert len(modified) == 1
        assert modified[0].old_content == "hello world"
        assert modified[0].new_content == "hello universe"

    def test_single_line_replace_alignment_skips_matrix_setup(self, monkeypatch):
        def fail_range(*_args, **_kwargs):
            raise AssertionError("single-line replace alignment should not build a matrix")

        monkeypatch.setattr(diff_module, "range", fail_range, raising=False)

        result = diff_module._align_replace_lines(["hello world"], ["hello universe"])

        assert result == [("hello world", "hello universe")]

    def test_compute_line_diff_realigns_replace_chunks_without_slicing_inputs(self):
        class NoSliceLines(list):
            def __getitem__(self, index):
                if isinstance(index, slice):
                    raise AssertionError("line diff replace chunks should not copy slices")
                return super().__getitem__(index)

        result = compute_line_diff(
            NoSliceLines(["old one", "shared"]),
            NoSliceLines(["new one", "shared"]),
        )

        assert [(line.old_content, line.new_content) for line in result] == [
            ("old one", "new one"),
            ("shared", "shared"),
        ]

    def test_long_modified_line_in_compute_line_diff_skips_word_segments(self):
        """compute_line_diff should skip word-segment calculation for long lines."""
        old = ["a" * 1300]
        new = ["a" * 1299 + "b"]

        result = compute_line_diff(old, new)

        modified = [line for line in result if line.is_modified]
        assert len(modified) == 1
        assert modified[0].old_segments == []
        assert modified[0].new_segments == []

    def test_compute_line_diff_distinguishes_blank_line_from_missing_line(self):
        """Real blank lines in replace blocks should not be treated as missing lines."""
        old = ["@classmethod", ""]
        new = ["def method(self):"]

        result = compute_line_diff(old, new)

        assert len(result) == 2
        assert result[0].is_modified
        assert result[0].old_content == "@classmethod"
        assert result[0].new_content == "def method(self):"
        assert result[1].is_deleted
        assert result[1].old_content == ""
        assert all(not (line.is_added and line.new_content == "") for line in result)

    def test_parse_patch_handles_replace_block_with_deleted_blank_line(self):
        """Patch parsing should not crash when a shorter replace run ends with a blank deleted line."""
        patch = """@@ -1,3 +1,1 @@
-@classmethod
-def method(cls):
-
+def method(self):"""

        diff = parse_patch(patch, "test.py")

        hunk = diff.hunks[0]
        assert len(hunk.lines) == 3
        assert hunk.lines[0].is_deleted
        assert hunk.lines[0].old_content == "@classmethod"
        assert hunk.lines[1].is_modified
        assert hunk.lines[1].old_content == "def method(cls):"
        assert hunk.lines[1].new_content == "def method(self):"
        assert hunk.lines[2].is_deleted
        assert hunk.lines[2].old_content == ""
        assert all(
            not (line.is_added and line.new_content == "") for line in hunk.lines
        )


class TestDiffHighlighting:
    """Tests for range-based diff highlighting."""

    def _assign_line_indexes(self, diff):
        line_index = 0
        for hunk in diff.hunks:
            for line in hunk.lines:
                line.line_index = line_index
                line_index += 1

    def _style_signature(self, content):
        assert content is not None
        return [(span.start, span.end, str(span.style)) for span in content.spans]

    def _style_for_fragment(self, content, fragment):
        assert content is not None
        start = content.plain.index(fragment)
        end = start + len(fragment)
        for span in content.spans:
            if span.start == start and span.end == end:
                return str(span.style)
        return None

    def test_single_line_highlighting_avoids_content_split(self, monkeypatch):
        """Single-line highlight windows should reuse the highlighted content directly."""

        class SingleLineContent(Content):
            def split(self, *_args, **_kwargs):
                raise AssertionError("single-line highlighting should not split content")

        def fake_highlight(text, **_kwargs):
            return SingleLineContent(text)

        monkeypatch.setattr(highlighting_module.highlight, "highlight", fake_highlight)
        monkeypatch.setattr(
            highlighting_module.highlight,
            "guess_language",
            lambda *_args, **_kwargs: "python",
        )

        diff = parse_patch("@@ -0,0 +1 @@\n+value = 1", "test.py")
        self._assign_line_indexes(diff)

        highlight_lines_for_diff(diff, include_word_diff=False)

        assert diff.hunks[0].lines[0].highlighted_new_content == Content("value = 1")

    def test_range_highlighting_starts_at_hunk_head_without_counting_empty_prefix(
        self,
        monkeypatch,
    ):
        """Highlighting from a hunk start should use zero offsets directly."""

        monkeypatch.setattr(
            highlighting_module,
            "_count_side_lines",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("hunk-start highlighting should not count empty prefix")
            ),
        )
        monkeypatch.setattr(
            highlighting_module.highlight,
            "highlight",
            lambda text, **_kwargs: Content(text),
        )
        monkeypatch.setattr(
            highlighting_module.highlight,
            "guess_language",
            lambda *_args, **_kwargs: "python",
        )

        diff = parse_patch("@@ -1 +1 @@\n-line 1\n+line 1", "test.py")
        self._assign_line_indexes(diff)

        highlight_lines_for_diff_range(diff, 0, 0, include_word_diff=False)

        line = diff.hunks[0].lines[0]
        assert line.highlighted_old_content == Content("line 1")
        assert line.highlighted_new_content == Content("line 1")

    def _combined_diff_with_python_class_hunk(self):
        patch = '''@@ -30,3 +30,17 @@
     patch: str
 
+@dataclass(frozen=True)
+class ParsedFilePatchSummary:
+    """Lightweight metadata for a file section from a multi-file unified diff."""
+
+    filename: str
+    patch: str
+    old_filename: str | None = None
+    is_new: bool = False
+    is_deleted: bool = False
+    is_binary: bool = False
+    additions: int = 0
+    deletions: int = 0
+
 def parse_multi_file_patch(
     patch: str,
'''
        file_diff = parse_patch(patch, "src/rit/core/diff.py")
        self._assign_line_indexes(file_diff)
        for hunk in file_diff.hunks:
            for line in hunk.lines:
                line.file_path = file_diff.filename
        return FileDiff(filename="All files", hunks=file_diff.hunks)

    def _highlighted_new_line_containing(self, diff, fragment):
        return next(
            line
            for hunk in diff.hunks
            for line in hunk.lines
            if line.highlighted_new_content
            and fragment in line.highlighted_new_content.plain
        )

    def _assert_combined_python_class_highlighted(self, diff):
        dataclass_line = self._highlighted_new_line_containing(diff, "@dataclass")
        class_line = self._highlighted_new_line_containing(
            diff,
            "ParsedFilePatchSummary",
        )
        docstring_line = self._highlighted_new_line_containing(
            diff,
            "Lightweight metadata",
        )

        assert (
            self._style_for_fragment(
                dataclass_line.highlighted_new_content,
                "@dataclass",
            )
            == "#f4dbd6"
        )
        assert (
            self._style_for_fragment(
                class_line.highlighted_new_content,
                "ParsedFilePatchSummary",
            )
            == "#eed49f"
        )
        assert any(
            str(span.style) == "#a6da95 italic"
            for span in docstring_line.highlighted_new_content.spans
        )

    def test_generic_python_identifiers_use_body_text_style(self):
        """Generic identifiers should stay at the body text color."""
        diff = parse_patch(
            "@@ -0,0 +1,1 @@\n"
            "+reviewer_entity_ids = "
            "ReviewerService.get_all_reviewer_entity_ids_by_review_cycle(review_cycle)",
            "reviewer_app_service.py",
        )
        self._assign_line_indexes(diff)

        highlight_lines_for_diff(diff, include_word_diff=False)

        content = diff.hunks[0].lines[0].highlighted_new_content
        assert self._style_for_fragment(content, "ReviewerService") == "#cad3f5"
        assert (
            self._style_for_fragment(
                content,
                "get_all_reviewer_entity_ids_by_review_cycle",
            )
            == "#cad3f5"
        )

    def test_combined_diff_uses_line_file_path_for_python_highlighting(self):
        """Combined diffs should highlight hunks using each line's real file path."""
        combined = self._combined_diff_with_python_class_hunk()

        highlight_lines_for_diff(combined, include_word_diff=False)

        self._assert_combined_python_class_highlighted(combined)

    def test_combined_diff_range_uses_line_file_path_for_python_highlighting(self):
        """Windowed combined highlighting should use each line's real file path."""
        combined = self._combined_diff_with_python_class_hunk()

        highlight_lines_for_diff_range(
            combined,
            2,
            12,
            include_word_diff=False,
        )

        self._assert_combined_python_class_highlighted(combined)

    def test_word_diff_highlights_use_subtle_backgrounds(self):
        """Inline word-diff marks should not overpower syntax colors."""
        content = apply_word_diff_spans(
            Content("old new"),
            [("old", "-"), (" ", " "), ("new", "+")],
        )

        assert self._style_signature(content) == [
            (0, 3, "on $error 20%"),
            (4, 7, "on $success 20%"),
        ]

        diff = parse_patch(
            "@@ -1,1 +1,1 @@\n"
            "-def make_confirmed(self) -> None:\n"
            "+def make_confirmed(self, with_save: bool = True) -> None:",
            "test.py",
        )
        self._assign_line_indexes(diff)

        highlight_lines_for_diff(diff)

        line = diff.hunks[0].lines[0]
        assert line.is_modified
        assert any(
            style == "on $error 20%"
            for _, _, style in self._style_signature(line.highlighted_old_content)
        )
        assert any(
            style == "on $success 20%"
            for _, _, style in self._style_signature(line.highlighted_new_content)
        )

    def test_highlight_lines_for_diff_range_updates_only_requested_window(self):
        """Range highlighting should leave lines outside the window untouched."""
        patch = """@@ -1,4 +1,4 @@
 line1
-old line
+new line
 line3"""
        diff = parse_patch(patch, "test.py")

        self._assign_line_indexes(diff)

        highlight_lines_for_diff_range(diff, 1, 2)

        first_line = diff.hunks[0].lines[0]
        changed_line = diff.hunks[0].lines[1]
        last_line = diff.hunks[0].lines[2]

        assert first_line.highlighted_old_content is None
        assert first_line.highlighted_new_content is None
        assert changed_line.highlighted_old_content is not None
        assert changed_line.highlighted_new_content is not None
        assert last_line.highlighted_old_content is not None
        assert last_line.highlighted_new_content is not None

    def test_highlight_lines_for_diff_range_does_not_slice_hunk_lines(self):
        class NoSliceLines(list):
            def __getitem__(self, index):
                if isinstance(index, slice):
                    raise AssertionError("range highlighting should not copy hunk slices")
                return super().__getitem__(index)

        patch = """@@ -1,4 +1,4 @@
 line1
-old line
+new line
 line3"""
        diff = parse_patch(patch, "test.py")
        self._assign_line_indexes(diff)
        diff.hunks[0].lines = NoSliceLines(diff.hunks[0].lines)

        highlight_lines_for_diff_range(diff, 1, 2, include_word_diff=False)

        assert diff.hunks[0].lines[1].highlighted_old_content is not None
        assert diff.hunks[0].lines[2].highlighted_new_content is not None

    def test_highlight_lines_for_diff_range_uses_cached_hunk_indices(self):
        """Cached hunk ranges should avoid scanning every hunk for late windows."""
        hunks = []
        for hunk_index in range(200):
            line_no = hunk_index + 1
            hunks.append(f"@@ -{line_no},1 +{line_no},1 @@\n line{line_no}")
        diff = parse_patch("\n".join(hunks), "test.py")
        self._assign_line_indexes(diff)
        hunk_start_line_indices = [hunk.lines[0].line_index for hunk in diff.hunks]
        hunk_end_line_indices = [hunk.lines[-1].line_index for hunk in diff.hunks]
        iterated = {"count": 0}

        class CountingHunks(list):
            def __iter__(self):
                iterated["count"] += len(self)
                return super().__iter__()

        target_line = 180
        diff.hunks = CountingHunks(diff.hunks)

        highlight_lines_for_diff_range(
            diff,
            target_line,
            target_line,
            include_word_diff=False,
            hunk_start_line_indices=hunk_start_line_indices,
            hunk_end_line_indices=hunk_end_line_indices,
        )

        assert iterated["count"] == 0
        assert diff.hunks[target_line].lines[0].highlighted_new_content is not None

    def test_highlight_lines_for_diff_range_uses_hunk_context_for_python_docstrings(
        self,
    ):
        """Windowed highlighting should match full highlighting inside a hunk."""
        patch = '''@@ -0,0 +1,13 @@
+class PeerReviewNominationApprovalService:
+    """
+    동료 선택 방식의 '평가권자의 승인 필요' 관련 도메인 서비스
+    """
+
+    @classmethod
+    @transaction.atomic
+    def enable_approval(cls, review_cycle: ReviewCycle) -> None:
+        """
+        '승인 필요' off -> on 변경 처리
+        """
+        return None
+'''
        filename = "peer_review_nomination_approval_service.py"
        full_diff = parse_patch(patch, filename)
        range_diff = parse_patch(patch, filename)
        self._assign_line_indexes(full_diff)
        self._assign_line_indexes(range_diff)

        highlight_lines_for_diff(full_diff, include_word_diff=False)
        highlight_lines_for_diff_range(
            range_diff,
            3,
            10,
            include_word_diff=False,
        )

        full_lines = full_diff.hunks[0].lines
        range_lines = range_diff.hunks[0].lines
        for line_index in (5, 6, 7, 9):
            assert range_lines[line_index].highlighted_new_content is not None
            assert self._style_signature(
                range_lines[line_index].highlighted_new_content
            ) == self._style_signature(full_lines[line_index].highlighted_new_content)
