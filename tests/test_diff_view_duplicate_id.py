"""Test for DiffView duplicate ID fix."""

import pytest
from textual.app import App, ComposeResult

from rit.ui.widgets.diff_view import DiffView
from rit.core.diff import parse_patch
from rit.ui.widgets.diff_render import _build_full_file_diff


@pytest.fixture
def sample_patch():
    """Sample patch for testing."""
    return """@@ -1,3 +1,4 @@
 line1
 line2
+new line
 line3"""


class TestDiffViewDuplicateId:
    """Test that DiffView handles multiple renders without duplicate ID errors."""

    @pytest.mark.asyncio
    async def test_show_same_file_twice(self, sample_patch):
        """Test showing the same file twice doesn't cause duplicate ID error."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            # Show the file first time
            await diff_view.show_diff("test.py", diff)
            await pilot.pause()

            # Show the same file again - this should not raise DuplicateIds error
            await diff_view.show_diff("test.py", diff)
            await pilot.pause()

            # Verify the diff is still displayed
            assert diff_view.current_file == "test.py"

    @pytest.mark.asyncio
    async def test_show_different_files_multiple_times(self, sample_patch):
        """Test showing different files multiple times."""

        patch2 = """@@ -1,2 +1,3 @@
 foo
+bar
 baz"""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff1 = parse_patch(sample_patch, "test1.py")
            diff2 = parse_patch(patch2, "test2.py")

            # Show file 1
            await diff_view.show_diff("test1.py", diff1)
            await pilot.pause()

            # Show file 2
            await diff_view.show_diff("test2.py", diff2)
            await pilot.pause()

            # Show file 1 again
            await diff_view.show_diff("test1.py", diff1)
            await pilot.pause()

            # Show file 2 again
            await diff_view.show_diff("test2.py", diff2)
            await pilot.pause()

            # Verify the last file is displayed
            assert diff_view.current_file == "test2.py"

    @pytest.mark.asyncio
    async def test_content_container_has_unique_ids(self, sample_patch):
        """Test that content containers have unique IDs."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(sample_patch, "test.py")

            # Show the file multiple times
            for _ in range(3):
                await diff_view.show_diff("test.py", diff)
                await pilot.pause()

            # Verify there are no duplicate IDs after repeated renders
            ids = [node.id for node in diff_view.walk_children() if node.id]
            assert ids.count("diff-content") == 1
            assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_auto_mode_switching_force_unified_and_split_files_keeps_unique_hunk_ids(
        self,
    ):
        """show_diff should not race a split-state rerender worker in auto mode."""

        added_only_patch = "@@ -0,0 +1,3 @@\n+line1\n+line2\n+line3"
        regular_patch = (
            "@@ -1,4 +1,4 @@\n line1\n line2\n-line3\n+line3_v2\n line4"
        )

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(mode="auto", id="diff-view")

        app = TestApp()
        async with app.run_test(size=(160, 40)) as pilot:
            diff_view = app.query_one(DiffView)
            added_diff = parse_patch(added_only_patch, "added.py")
            regular_diff = parse_patch(regular_patch, "regular.py")

            await diff_view.show_diff("added.py", added_diff)
            await pilot.pause()
            assert diff_view.split is False

            await diff_view.show_diff("regular.py", regular_diff)
            await pilot.pause()
            await pilot.pause()

            assert diff_view.split is True
            hunk_headers = [node.id for node in diff_view.query(".hunk-header")]
            assert hunk_headers.count("hunk-0") == 1

    @pytest.mark.asyncio
    async def test_show_diff_while_virtual_window_shift_is_pending(self):
        """A stale virtual-window worker must not mount duplicate hunk headers."""

        patch = "@@ -1,1200 +1,1200 @@\n" + "\n".join(
            f" line{i}" for i in range(1, 1201)
        )

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(mode="unified", id="diff-view")

        app = TestApp()
        async with app.run_test(size=(100, 20)) as pilot:
            diff_view = app.query_one(DiffView)
            diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
            diff_view.VIRTUAL_WINDOW_RADIUS = 3
            diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1
            diff = parse_patch(patch, "big.py")

            await diff_view.show_diff("big.py", diff)
            await pilot.pause()

            diff_view.cursor_line = 50
            await diff_view.show_diff("big.py", diff)
            await pilot.pause()
            await pilot.pause()

            hunk_headers = [node.id for node in diff_view.query(".hunk-header")]
            assert hunk_headers.count("hunk-0") == 1

    @pytest.mark.asyncio
    async def test_full_file_preview_uses_unified_blocks_without_missing_preview_width(
        self,
    ):
        """Full-file preview should render unified blocks without attribute errors."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DiffView(mode="unified", id="diff-view")

        app = TestApp()
        async with app.run_test() as pilot:
            diff_view = app.query_one(DiffView)
            content = "\n".join(f"line {i}" for i in range(1, 151))
            diff = _build_full_file_diff("preview.py", content)

            diff_view._showing_full_file = True
            await diff_view.show_diff("preview.py", diff)
            await pilot.pause()

            assert len(diff_view.query(".diff-block")) >= 1
