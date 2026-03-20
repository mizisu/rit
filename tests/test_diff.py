"""Tests for diff algorithm."""

import pytest
from rit.core.diff import parse_patch, compute_word_diff, compute_line_diff
from rit.core.highlighting import highlight_lines_for_diff_range
from rit.core.types import SegmentType


class TestParsePatch:
    """Tests for parse_patch function."""

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
        modified_lines = [l for l in hunk.lines if l.is_modified]

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

        added = [l for l in result if l.is_added]
        assert len(added) == 1
        assert added[0].new_content == "inserted"

    def test_deleted_line(self):
        """Test deleting a line."""
        old = ["line1", "deleted", "line2"]
        new = ["line1", "line2"]
        result = compute_line_diff(old, new)

        deleted = [l for l in result if l.is_deleted]
        assert len(deleted) == 1
        assert deleted[0].old_content == "deleted"

    def test_modified_line(self):
        """Test modifying a line (similar content)."""
        old = ["hello world"]
        new = ["hello universe"]
        result = compute_line_diff(old, new)

        # Should be detected as modified with word diff
        modified = [l for l in result if l.is_modified]
        assert len(modified) == 1
        assert modified[0].old_content == "hello world"
        assert modified[0].new_content == "hello universe"

    def test_long_modified_line_in_compute_line_diff_skips_word_segments(self):
        """compute_line_diff should skip word-segment calculation for long lines."""
        old = ["a" * 1300]
        new = ["a" * 1299 + "b"]

        result = compute_line_diff(old, new)

        modified = [l for l in result if l.is_modified]
        assert len(modified) == 1
        assert modified[0].old_segments == []
        assert modified[0].new_segments == []


class TestDiffHighlighting:
    """Tests for range-based diff highlighting."""

    def test_highlight_lines_for_diff_range_updates_only_requested_window(self):
        """Range highlighting should leave lines outside the window untouched."""
        patch = """@@ -1,4 +1,4 @@
 line1
-old line
+new line
 line3"""
        diff = parse_patch(patch, "test.py")

        line_index = 0
        for hunk in diff.hunks:
            for line in hunk.lines:
                line.line_index = line_index
                line_index += 1

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
