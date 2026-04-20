"""Tests for DiffView settings-driven behavior."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.ui.widgets.diff_view import DiffView


def _as_plain(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


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
