"""Tests for DiffView settings-driven behavior."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.ui.widgets.diff_view import DiffView


def _as_plain(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


def _combined_diff_with_single_sided_file(
    changed_line: DiffLine,
    *,
    additions: int,
    deletions: int,
) -> FileDiff:
    return FileDiff(
        filename="All files",
        show_hunk_headers=False,
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=deletions,
                new_start=1,
                new_count=additions,
                starts_file=True,
                file_path="single_sided.py",
                file_status="modified",
                file_additions=additions,
                file_deletions=deletions,
                lines=[changed_line],
            ),
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                starts_file=True,
                file_path="changed.py",
                file_status="modified",
                file_additions=1,
                file_deletions=1,
                lines=[
                    DiffLine(
                        old_line_no=1,
                        new_line_no=1,
                        old_content="old_value",
                        new_content="new_value",
                        is_modified=True,
                    )
                ],
            ),
        ],
    )


@pytest.mark.parametrize(
    ("changed_line", "additions", "deletions", "expected_text"),
    [
        (
            DiffLine(
                old_line_no=None,
                new_line_no=1,
                new_content="added_value",
                is_added=True,
            ),
            1,
            0,
            "added_value",
        ),
        (
            DiffLine(
                old_line_no=1,
                new_line_no=None,
                old_content="deleted_value",
                is_deleted=True,
            ),
            0,
            1,
            "deleted_value",
        ),
    ],
)
@pytest.mark.asyncio
async def test_split_mode_renders_single_sided_combined_file_hunks_as_unified(
    changed_line: DiffLine,
    additions: int,
    deletions: int,
    expected_text: str,
) -> None:
    """Single-sided files in a combined diff should not waste a split pane."""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff(
            "All files",
            _combined_diff_with_single_sided_file(
                changed_line,
                additions=additions,
                deletions=deletions,
            ),
        )
        await pilot.pause()

        assert diff_view.split is True
        assert len(diff_view.query("#line-0-old")) == 0
        assert len(diff_view.query("#line-0-new")) == 0
        assert _as_plain(diff_view.query_one("#line-0 .code-content", Static)) == (
            expected_text
        )
        assert len(diff_view.query("#line-1-old")) == 1
        assert len(diff_view.query("#line-1-new")) == 1


@pytest.mark.asyncio
async def test_show_line_numbers_toggle_rerenders_prefix_content() -> None:
    """Toggling line numbers should update rendered prefixes in place."""

    patch = "@@ -1,1 +1,1 @@\n-old_value\n+new_value"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        old_prefix = diff_view.query_one("#line-0-old .line-prefix", Static)
        width_with_numbers = old_prefix.size.width
        assert "1" in _as_plain(old_prefix)

        diff_view.show_line_numbers = False
        await pilot.pause()
        await pilot.pause()

        old_prefix = diff_view.query_one("#line-0-old .line-prefix", Static)
        assert "1" not in _as_plain(old_prefix)
        assert "-" in _as_plain(old_prefix)
        assert old_prefix.size.width < width_with_numbers


@pytest.mark.asyncio
async def test_split_line_numbers_toggle_rerenders_prefix_width() -> None:
    """Split prefixes should also shrink when line numbers are hidden."""

    patch = "@@ -1,1 +1,1 @@\n-old_value\n+new_value"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        left_prefix = diff_view.query_one("#line-0-old .line-prefix", Static)
        right_prefix = diff_view.query_one("#line-0-new .line-prefix", Static)
        left_width_with_numbers = left_prefix.size.width
        right_width_with_numbers = right_prefix.size.width

        diff_view.show_line_numbers = False
        await pilot.pause()
        await pilot.pause()

        left_prefix = diff_view.query_one("#line-0-old .line-prefix", Static)
        right_prefix = diff_view.query_one("#line-0-new .line-prefix", Static)

        assert left_prefix.size.width < left_width_with_numbers
        assert right_prefix.size.width < right_width_with_numbers
        assert "1" not in _as_plain(left_prefix)
        assert "1" not in _as_plain(right_prefix)


@pytest.mark.asyncio
async def test_word_diff_toggle_invalidates_old_cache_and_rehighlights() -> None:
    """Changing word-diff mode should rebuild highlight state for the current file."""

    patch = "@@ -1,1 +1,1 @@\n-old_value\n+new_value"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert any(
            cache_key[:2] == (id(diff), True) for cache_key in diff_view._hl_state.cache
        )

        diff_view.word_diff_enabled = False
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert not any(
            cache_key[:2] == (id(diff), True) for cache_key in diff_view._hl_state.cache
        )
        assert any(
            cache_key[:2] == (id(diff), False)
            for cache_key in diff_view._hl_state.cache
        )
