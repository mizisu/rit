"""Tests for diff algorithm."""

from rit.core.diff import (
    compute_line_diff,
    compute_word_diff,
    parse_multi_file_patch,
    parse_patch,
)
from textual.content import Content

from rit.core.highlighting import (
    apply_word_diff_spans,
    highlight_lines_for_diff,
    highlight_lines_for_diff_range,
)
from rit.core.types import SegmentType


class TestParsePatch:
    """Tests for parse_patch function."""

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


class TestComputeWordDiff:
    """Tests for word-level diff."""

    def test_identical_strings(self):
        """Test identical strings."""
        old_segments, new_segments = compute_word_diff("hello world", "hello world")

        assert len(old_segments) == 1
        assert old_segments[0].type == SegmentType.UNCHANGED
        assert old_segments[0].text == "hello world"

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
