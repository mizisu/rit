from typing import Literal
from unittest.mock import Mock

import pytest
from textual import events
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.widgets import Input, Static, TextArea

from rit.core.diff import parse_patch
from rit.core.types import FileDiff
from rit.state.models import FileViewedState, PRFile
from rit.state.store import PRStore
from rit.ui.components.combined_diff import build_combined_diff_document
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


def _document() -> tuple[PRStore, FileDiff]:
    patch = "@@ -1,80 +1,80 @@\n-value = old\n+value = new\n" + "\n".join(
        f" line {index} {'x' * 160}" for index in range(2, 81)
    )
    store = PRStore()
    store.state.files = [
        PRFile(filename=path, status="modified", additions=1, deletions=1)
        for path in ("one.py", "two.py", "three.py")
    ]
    store.state.file_diffs = {
        file.filename: parse_patch(patch, file.filename) for file in store.state.files
    }
    document = build_combined_diff_document(store.state.files, store.state.file_diffs)
    assert document is not None
    return store, document.diff


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
@pytest.mark.parametrize("virtual", [False, True])
async def test_sticky_header_follows_viewport_not_cursor(
    mode: Literal["unified", "split"],
    virtual: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, diff = _document()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store, mode=mode)

    app = TestApp()
    async with app.run_test(size=(100, 10)) as pilot:
        view = app.query_one(DiffView)
        monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", 1 if virtual else 800)
        monkeypatch.setattr(view, "VIRTUAL_WINDOW_RADIUS", 3)
        monkeypatch.setattr(view, "VIRTUAL_WINDOW_SHIFT_MARGIN", 1)
        monkeypatch.setattr(view, "UNIFIED_BLOCK_CHUNK_SIZE", 8)
        await view.show_diff(diff.filename, diff)
        await pilot.pause()
        header = view.query_one("#diff-sticky-header", Static)
        geometry = (view._virtual_content_height, view.virtual_size, view.max_scroll_y)
        boundary = view._hunk_header_top_offsets[1]
        assert view._virt.active is virtual

        for offset in (
            40, boundary - 1, boundary - 0.25, boundary, boundary + 1, boundary + 40, 0
        ):
            view.scroll_to(y=offset, animate=False, immediate=True)
            await wait_until(
                lambda offset=offset: (
                    not view._virt.render_pending and view.scroll_y == offset
                ),
                timeout=5,
            )
            await pilot.pause()
            expected = "one.py" if round(offset) < boundary else "two.py"
            assert expected in str(header.content)
            assert header.region.y == view.scrollable_content_region.y
            assert header.region.width == view.scrollable_content_region.width
            assert (
                app.screen._compositor.render_strips()[header.region.y].text.count(
                    expected
                )
                == 1
            )
            assert view.query_one("#diff-sticky-header") is header
            assert view.cursor_line == 0
            assert geometry == (
                view._virtual_content_height,
                view.virtual_size,
                view.max_scroll_y,
            )
            if virtual and offset == boundary + 40:
                assert not view.query("#file-header-1")

        update = Mock(wraps=header.update)
        monkeypatch.setattr(header, "update", update)
        view.scroll_to(y=2, animate=False, immediate=True)
        await pilot.pause()
        view.scroll_to(y=3, animate=False, immediate=True)
        if mode == "split":
            view._sync_split_horizontal_scroll(30)
        else:
            assert view._content_widget is not None
            view._content_widget.scroll_x = 30
        await pilot.pause()
        update.assert_not_called()
        assert "one.py" in app.screen._compositor.render_strips()[header.region.y].text
        assert header.region.x == view.scrollable_content_region.x
        app.post_message(
            events.MouseScrollDown(
                None, header.region.x + 3, header.region.y,
                0, 0, 0, False, False, False,
            )
        )
        await wait_until(
            lambda: view.scroll_y > 3 and not view._virt.render_pending, timeout=5
        )
        assert view.cursor_line == 0


@pytest.mark.asyncio
async def test_sticky_header_refreshes_metadata_folds_resize_and_preview() -> None:
    store, diff = _document()
    renamed = store.state.files[1]
    renamed.previous_filename = "src/previous/location/with/a/long/path/two.py"
    diff.hunks[1].file_old_path = renamed.previous_filename

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store, mode="auto")

    app = TestApp()
    async with app.run_test(size=(140, 24)) as pilot:
        view = app.query_one(DiffView)
        await view.show_diff(diff.filename, diff)
        view.scroll_to(y=view._hunk_header_top_offsets[1] + 30, animate=False)
        await pilot.pause()
        header = view.query_one("#diff-sticky-header", Static)
        assert renamed.previous_filename in str(header.content)
        renamed.viewer_viewed_state = FileViewedState.VIEWED
        view.refresh_header()
        assert str(header.content).endswith("Viewed")

        await pilot.resize_terminal(60, 24)
        await wait_until(lambda: not view.split)
        await pilot.pause()
        assert "..." in str(header.content)
        assert "two.py" in str(header.content)
        assert "Viewed" in app.screen._compositor.render_strips()[header.region.y].text
        renamed.viewer_viewed_state = FileViewedState.UNVIEWED
        view.refresh_header()

        assert view.cursor_line == 0
        await pilot.click("#diff-sticky-header", offset=(3, 0))
        await wait_until(lambda: "two.py" in view._folded_file_paths, timeout=5)
        assert "one.py" not in view._folded_file_paths
        view.scroll_to(y=view._hunk_header_top_offsets[1], animate=False)
        await pilot.pause()
        assert str(header.content).startswith("▸")
        await pilot.click("#diff-sticky-header", offset=(3, 0))
        await wait_until(lambda: "two.py" not in view._folded_file_paths, timeout=5)
        await pilot.pause()
        assert str(header.content).startswith("▾")

        assert await view.show_full_file_preview(
            "two.py",
            "\n".join(f"line {index}" for index in range(100)),
            source_diff=store.state.file_diffs["two.py"],
        )
        view.scroll_to(y=40, animate=False)
        await pilot.pause()
        assert "two.py" in str(header.content)
        view._restore_diff_view()
        await wait_until(lambda: view.current_file == "All files", timeout=5)
        await pilot.pause()
        assert view.query_one("#diff-sticky-header") is header

        await view.show_diff("empty.py", FileDiff(filename="empty.py"))
        await pilot.pause()
        assert not header.visible
        assert view._sticky_header_inset == 0


@pytest.mark.asyncio
async def test_sticky_header_does_not_cover_search_cursor_or_editor() -> None:
    store, diff = _document()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store, mode="unified")

    app = TestApp()
    async with app.run_test(size=(100, 24)) as pilot:
        view = app.query_one(DiffView)
        await view.show_diff(diff.filename, diff)
        await pilot.pause()
        view.jump_to_line_index(100, side="RIGHT", viewport_offset=0)
        await pilot.pause()
        row = view._current_row()
        assert row is not None and view._row_is_visible(row)
        bounds = view._row_vertical_bounds(row)
        assert bounds is not None
        assert bounds[0] - int(view.scroll_y) == 1

        view.scroll_to(y=bounds[0], animate=False, immediate=True)
        assert not view._row_is_visible(row)
        view.action_start_search()
        view.query_one("#diff-search-input", Input).value = "line 21 "
        await wait_until(lambda: bool(view._search.matches), timeout=5)
        await pilot.press("enter", "n")
        await pilot.pause()
        row = view._current_row()
        assert row is not None and view._row_is_visible(row)
        cursor_offset = view._current_cursor_viewport_offset()
        assert cursor_offset is not None and cursor_offset >= 1

        # Native focus/reveal also needs the inset, independently of cursor motion.
        top = int(view.scroll_y)
        view.scroll_to_region(
            Region(0, top, 1, 1), animate=False, immediate=True, x_axis=False
        )
        assert view.scroll_y == top - 1
        assert await view.open_inline_comment_editor()
        await wait_until(lambda: isinstance(app.focused, TextArea), timeout=5)
        await pilot.pause()
        assert app.focused is not None
        assert app.focused.region.y > view._sticky_header.region.y
        await view.close_inline_comment_editor()
        await pilot.pause()
        view.scroll_to(y=view._hunk_header_top_offsets[1], animate=False)
        await pilot.pause()
        assert "two.py" in str(view._sticky_header.content)
