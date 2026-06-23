"""Tests for full-file preview rendering."""

import pytest
from textual.app import App, ComposeResult

from rit.core.diff import parse_patch
from rit.state.store import PRStore
from rit.ui.widgets.diff_full_file_preview import build_full_file_diff
from rit.ui.widgets.diff_view import DiffView


def _content(line_count: int) -> str:
    return "\n".join(f"line {line}" for line in range(1, line_count + 1))


@pytest.mark.asyncio
async def test_full_file_preview_renders_source_change_markers_only() -> None:
    patch = """@@ -1,5 +1,5 @@
 line 1
-line 2
+line 2 updated
+line 2 extra
 line 3
-line 4
 line 5"""
    source_diff = parse_patch(patch, "preview.py")
    full_diff = build_full_file_diff(
        "preview.py",
        "\n".join(
            [
                "line 1",
                "line 2 updated",
                "line 2 extra",
                "line 3",
                "line 5",
            ]
        ),
        source_diff=source_diff,
    )
    lines_by_new_number = {
        line.new_line_no: line
        for hunk in full_diff.hunks
        for line in hunk.lines
        if line.new_line_no is not None
    }

    assert full_diff.show_hunk_headers is False
    assert lines_by_new_number[2].preview_change == "added"
    assert lines_by_new_number[3].preview_change == "modified"
    assert lines_by_new_number[5].preview_deleted_before is True

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=PRStore(), mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.current_file = "preview.py"
        diff_view._showing_full_file = True
        await diff_view.show_diff(
            "preview.py",
            full_diff,
            preserve_full_file_state=True,
        )
        await pilot.pause()

        prefix_texts = [
            str(getattr(node.content, "plain", node.content))
            for node in diff_view.query(".line-prefix")
        ]

        assert len(diff_view.query(".hunk-header")) == 0
        assert any("┃" in text for text in prefix_texts)
        assert any("▸" in text for text in prefix_texts)


@pytest.mark.asyncio
async def test_full_file_preview_sticky_header_tracks_line_and_section() -> None:
    patch = """@@ -2,2 +2,2 @@
 line 2
-line old
+line 3
@@ -8,2 +8,2 @@
 line 8
-line old
+line 9"""
    source_diff = parse_patch(patch, "preview.py")
    full_diff = build_full_file_diff(
        "preview.py",
        _content(12),
        source_diff=source_diff,
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=PRStore(), mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.current_file = "preview.py"
        diff_view._showing_full_file = True
        await diff_view.show_diff(
            "preview.py",
            full_diff,
            preserve_full_file_state=True,
        )
        await pilot.pause()

        diff_view.cursor_line = 7
        await pilot.pause()

        header = diff_view.query_one("#diff-header")
        header_text = str(getattr(header.content, "plain", header.content))

        assert "preview.py" in header_text
        assert "line 8/12" in header_text
        assert "change hunk 2/2" in header_text


@pytest.mark.asyncio
async def test_full_file_preview_opens_at_current_new_line() -> None:
    patch = """@@ -6,3 +6,3 @@
 line 6
-line 7 old
+line 7
 line 8"""
    source_diff = parse_patch(patch, "preview.py")

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("preview.py", source_diff)
        await pilot.pause()
        diff_view.cursor_line = diff_view._line_index_by_new_number[7]
        await pilot.pause()

        await diff_view.show_full_file_preview(
            "preview.py",
            _content(12),
            source_diff=source_diff,
        )
        await pilot.pause()

        current = diff_view._current_line()
        assert current is not None
        assert current.new_line_no == 7
        assert diff_view.cursor_line == diff_view._line_index_by_new_number[7]


@pytest.mark.asyncio
async def test_full_file_preview_exposes_comment_target_outside_diff_hunk() -> None:
    patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
    source_diff = parse_patch(patch, "preview.py")

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("preview.py", source_diff)
        await pilot.pause()

        await diff_view.show_full_file_preview(
            "preview.py",
            _content(6),
            source_diff=source_diff,
        )
        await pilot.pause()
        diff_view.cursor_line = diff_view._line_index_by_new_number[6]
        await pilot.pause()

        assert await diff_view.open_inline_comment_editor() is True
        await pilot.pause()
        await pilot.pause()
        assert diff_view.inline_comment_target() == ("preview.py", 6, "RIGHT")


@pytest.mark.asyncio
async def test_full_file_preview_restore_returns_to_original_diff_line() -> None:
    patch = """@@ -6,3 +6,3 @@
 line 6
-line 7 old
+line 7
 line 8"""
    source_diff = parse_patch(patch, "preview.py")

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=PRStore(), mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("preview.py", source_diff)
        await pilot.pause()
        original_line = diff_view._line_index_by_new_number[7]
        diff_view.cursor_line = original_line
        await pilot.pause()

        await diff_view.show_full_file_preview(
            "preview.py",
            _content(12),
            source_diff=source_diff,
        )
        await pilot.pause()

        diff_view.action_toggle_full_file()
        await pilot.pause()
        await pilot.pause()

        current = diff_view._current_line()
        assert diff_view.current_file == "preview.py"
        assert current is not None
        assert current.new_line_no == 7
        assert diff_view.cursor_line == original_line


@pytest.mark.asyncio
async def test_full_file_preview_opens_deleted_line_at_nearest_current_line() -> None:
    patch = """@@ -6,3 +6,2 @@
 line 6
-line 7 removed
 line 8"""
    source_diff = parse_patch(patch, "preview.py")

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_diff("preview.py", source_diff)
        await pilot.pause()
        diff_view.cursor_line = 1
        await pilot.pause()

        await diff_view.show_full_file_preview(
            "preview.py",
            "\n".join(
                [
                    "line 1",
                    "line 2",
                    "line 3",
                    "line 4",
                    "line 5",
                    "line 6",
                    "line 8",
                    "line 9",
                ]
            ),
            source_diff=source_diff,
        )
        await pilot.pause()

        current = diff_view._current_line()
        assert current is not None
        assert current.new_line_no == 7
        assert diff_view.cursor_line == diff_view._line_index_by_new_number[7]


@pytest.mark.asyncio
async def test_show_diff_same_file_exits_full_file_preview_state() -> None:
    patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
    source_diff = parse_patch(patch, "preview.py")

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        await diff_view.show_full_file_preview(
            "preview.py",
            _content(6),
            source_diff=source_diff,
        )
        await pilot.pause()

        await diff_view.show_diff("preview.py", source_diff)
        await pilot.pause()

        assert diff_view._showing_full_file is False
