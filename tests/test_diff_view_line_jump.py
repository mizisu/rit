"""Tests for source-line navigation with counted g/G motions."""

from typing import Literal

import pytest
from textual.app import App, ComposeResult

from rit.core.diff import parse_patch
from rit.state.models import PRComment, PRFile, ReviewThread
from rit.state.store import PRStore
from rit.ui.components.combined_diff import build_combined_diff_document
from rit.ui.messages import Flash
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


@pytest.mark.asyncio
async def test_counted_g_uses_source_line_numbers_and_cursor_side() -> None:
    patch = """@@ -40,6 +45,6 @@
 context
-deleted
 continued
+added
-modified before
+modified after
 target
 end
@@ -48,3 +53,3 @@
 old target
 next
 last"""
    cases: dict[
        Literal["split", "unified"],
        list[tuple[Literal["old", "new"], int, str, Literal["old", "new"]]],
    ] = {
        "split": [
            ("old", 0, "g", "old"),
            ("new", 0, "G", "new"),
            ("old", 3, "G", "old"),
            ("new", 1, "g", "new"),
        ],
        "unified": [
            ("old", 0, "g", "new"),
            ("new", 1, "G", "old"),
            ("old", 3, "g", "new"),
            ("old", 4, "g", "old"),
            ("new", 4, "G", "new"),
        ],
    }

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView()

    app = TestApp()
    async with app.run_test() as pilot:
        view = app.query_one(DiffView)
        for mode, motions in cases.items():
            view.mode = mode
            await view.show_diff("test.py", parse_patch(patch, "test.py"))
            view.focus()
            for pane, start, key, expected_pane in motions:
                view._move_cursor(line=start, pane=pane, update_active_pane=True)
                await pilot.press("4", "8", key)

                line = view._current_line()
                assert line is not None
                assert view.cursor_pane == expected_pane
                assert (
                    line.old_line_no if expected_pane == "old" else line.new_line_no
                ) == 48
                assert view._cursor_ui.pending_count == ""


@pytest.mark.asyncio
async def test_counted_g_stays_in_current_file_and_reports_missing_lines() -> None:
    patch = "@@ -40,3 +40,3 @@\n first\n second\n third"
    files = [PRFile(filename=name, status="modified") for name in ("one.py", "two.py")]
    diffs = {
        "one.py": parse_patch(patch + "\n@@ -48 +48 @@\n only in one", "one.py"),
        "two.py": parse_patch(patch, "two.py"),
    }
    document = build_combined_diff_document(files, diffs)
    assert document is not None
    flashes: list[Flash] = []

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified")

        def on_flash(self, message: Flash) -> None:
            flashes.append(message)

    app = TestApp()
    async with app.run_test() as pilot:
        view = app.query_one(DiffView)
        await view.show_diff(document.diff.filename, document.diff)
        view.focus()
        await pilot.press("G", "4", "0", "g")

        line = view._current_line()
        assert line is not None
        assert line.file_path == "two.py"
        assert line.new_line_no == 40
        assert view._cursor_ui.pending_count == ""

        position = (view.cursor_line, view.cursor_column, view.cursor_pane)
        await pilot.press("4", "8", "G")
        await wait_until(lambda: flashes)
        assert (view.cursor_line, view.cursor_column, view.cursor_pane) == position
        assert view._cursor_ui.pending_count == ""
        assert flashes[-1].style == "warning"
        assert "48" in str(flashes[-1].content)
        assert "press p" in str(flashes[-1].content)

        await pilot.press("j")
        line = view._current_line()
        assert line is not None and line.new_line_no == 41

        for folded in (True, False):
            if folded:
                view._manually_folded_files.add("one.py")
            else:
                view._manually_folded_files.discard("one.py")
            await view._refresh_viewed_folds(document.diff, document.diff.filename)
            await pilot.press("4", "0", "g")
            line = view._current_line()
            assert line is not None
            assert line.file_path == "two.py"
            assert line.new_line_no == 40
            assert view._is_file_folded("one.py") is folded

        await view.show_full_file_preview(
            "two.py",
            "\n".join(f"line {number}" for number in range(1, 61)),
            source_diff=diffs["two.py"],
        )
        await pilot.press("4", "8", "g")
        line = view._current_line()
        assert line is not None and line.new_line_no == 48


@pytest.mark.asyncio
async def test_counted_g_only_scrolls_for_offscreen_lines() -> None:
    patch = "@@ -40,120 +45,120 @@\n" + "\n".join(
        f" line {number}" for number in range(45, 165)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView()

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        view = app.query_one(DiffView)
        for mode in ("split", "unified"):
            view.mode = mode
            await view.show_diff("test.py", parse_patch(patch, "test.py"))
            view.focus()
            await pilot.press("7", "0", "g")
            scroll_y = view.scroll_y
            visible_rows = [
                row
                for row in view._rows_for_current_mode()
                if view._row_is_visible(row)
            ]

            for row, key in (
                (visible_rows[0], "g"),
                (visible_rows[len(visible_rows) // 2], "G"),
                (visible_rows[-1], "g"),
            ):
                assert row.new_line_no is not None
                await pilot.press(*str(row.new_line_no), key)
                line = view._current_line()
                assert line is not None and line.new_line_no == row.new_line_no
                assert view.scroll_y == scroll_y

            await pilot.press("1", "0", "0", "G")
            assert view.scroll_y > scroll_y
            row = view._current_row()
            assert row is not None and view._row_is_visible(row)

            await pilot.press("4", "8", "g")
            assert view.scroll_y < scroll_y
            row = view._current_row()
            assert row is not None and view._row_is_visible(row)


@pytest.mark.asyncio
async def test_counted_g_uses_actual_row_positions_after_folding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -40,120 +45,120 @@\n" + "\n".join(
        f" code {number:03d}" for number in range(45, 165)
    )
    patches = {
        "one.py": patch,
        "two.py": "@@ -0,0 +45,120 @@\n"
        + "\n".join(f"+code {number:03d}" for number in range(45, 165)),
        "three.py": patch,
    }
    store = PRStore()
    store.state.files = [
        PRFile(filename=name, status="added" if name == "two.py" else "modified")
        for name in patches
    ]
    thread = ReviewThread(path="two.py", line=46, diff_side="RIGHT")
    thread.comments.append(
        PRComment(
            id=1,
            path="two.py",
            line=46,
            side="RIGHT",
            body="\n\n".join("long comment paragraph" for _ in range(12)),
        )
    )
    store.state.review_threads = [thread]
    document = build_combined_diff_document(
        store.state.files,
        {name: parse_patch(source, name) for name, source in patches.items()},
    )
    assert document is not None

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store)

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        view = app.query_one(DiffView)
        view._manually_folded_files.add("one.py")
        for mode in ("unified", "split"):
            for block_threshold in (40, 1000):
                view.mode = mode
                monkeypatch.setattr(
                    view, "BLOCK_RENDER_LINE_THRESHOLD", block_threshold
                )
                await view.show_diff(document.diff.filename, document.diff)
                assert view.split is (mode == "split")
                index = view.line_index_for_location("two.py", 46, "RIGHT")
                assert index is not None
                view.jump_to_line_index(index, side="RIGHT", focus=True)
                await pilot.press("j", "enter")
                await pilot.wait_for_scheduled_animations()

                rendered = "\n".join(
                    strip.text for strip in app.screen._compositor.render_strips()
                )
                visible_numbers = [
                    number
                    for number in range(45, 165)
                    if f"code {number:03d}" in rendered
                ]
                assert visible_numbers
                scroll_y = view.scroll_y
                for number, key in (
                    (visible_numbers[-1], "g"),
                    (visible_numbers[0], "G"),
                ):
                    await pilot.press(*str(number), key)
                    line = view._current_line()
                    assert line is not None and line.new_line_no == number
                    assert line.file_path == "two.py"
                    assert view.scroll_y == scroll_y

                await pilot.press("1", "0", "0", "g")
                await pilot.pause()
                target_y = view.scrollable_content_region.y + 2
                assert (
                    "code 100" in app.screen._compositor.render_strips()[target_y].text
                )


@pytest.mark.asyncio
async def test_counted_g_reveals_virtualized_target_and_extends_visual_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DiffView, "VIRTUALIZE_LINE_THRESHOLD", 20)
    line_count = 300
    patch = f"@@ -40,{line_count} +50,{line_count} @@\n" + "\n".join(
        f" line {number}" for number in range(50, 50 + line_count)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        view = app.query_one(DiffView)
        await view.show_diff("test.py", parse_patch(patch, "test.py"))
        view.focus()
        await pilot.press("V", "2", "9", "8", "g")
        await wait_until(
            lambda: (
                not view._virt.render_pending
                and view._is_line_rendered(view.cursor_line)
                and (row := view._current_row()) is not None
                and view._row_is_visible(row)
            ),
            timeout=5.0,
        )

        line = view._current_line()
        assert line is not None
        assert line.new_line_no == 298
        assert view.visual_mode
        assert view.visual_anchor_line == 0
        assert view._cursor_ui.pending_count == ""

        scroll_y = view.scroll_y
        bottom_row = next(
            row
            for row in reversed(view._rows_for_current_mode())
            if view._row_is_visible(row)
        )
        assert bottom_row.new_line_no is not None
        await pilot.press(*str(bottom_row.new_line_no), "G")
        await wait_until(lambda: not view._virt.render_pending, timeout=5.0)
        line = view._current_line()
        assert line is not None and line.new_line_no == bottom_row.new_line_no
        assert view.scroll_y == scroll_y
        assert view.visual_mode
        assert view.visual_anchor_line == 0
