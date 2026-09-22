"""Tests for full-file preview rendering."""

import asyncio
import threading
from typing import Literal, cast

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PendingReviewComment, PRFile
from rit.state.store import PRStore
from rit.ui.widgets import diff_full_file_preview as full_preview_module
from rit.ui.widgets import diff_plan
from rit.ui.widgets.diff_full_file_preview import build_full_file_diff
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


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
            str(getattr(prefix.content, "plain", prefix.content))
            for prefix in (
                cast(Static, node) for node in diff_view.query(".line-prefix")
            )
        ]

        assert len(diff_view.query(".hunk-header")) == 0
        assert any("┃" in text for text in prefix_texts)
        assert any("▸" in text for text in prefix_texts)


@pytest.mark.asyncio
async def test_full_file_preview_uses_file_header_without_docked_header() -> None:
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

        file_header = diff_view.query_one("#file-header-0", Static)
        file_header_text = str(
            getattr(file_header.content, "plain", file_header.content)
        )

        assert "preview.py" in file_header_text


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


@pytest.mark.asyncio
async def test_full_file_preview_discards_result_after_newer_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_diff = parse_patch("@@ -1 +1 @@\n-old\n+new", "preview.py")
    newer_diff = parse_patch("@@ -1 +1 @@\n-before\n+after", "newer.py")
    started = threading.Event()
    release = threading.Event()
    original_build = full_preview_module.build_full_file_diff

    def blocking_build(*args, **kwargs):
        started.set()
        release.wait(timeout=2.0)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(full_preview_module, "build_full_file_diff", blocking_build)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test():
        diff_view = app.query_one(DiffView)
        await diff_view.show_diff("preview.py", source_diff)
        preview = asyncio.create_task(
            diff_view.show_full_file_preview(
                "preview.py",
                _content(3),
                source_diff=source_diff,
            )
        )
        assert await asyncio.to_thread(started.wait, 1.0)

        await diff_view.show_diff("newer.py", newer_diff)
        release.set()

        assert await preview is False
        assert diff_view.current_file == "newer.py"
        assert diff_view.current_diff is newer_diff
        assert diff_view._showing_full_file is False
        assert diff_view._saved_diff_plan_cache is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_preview_round_trip_renders_only_target_and_reuses_checked_plan(
    mode: Literal["unified", "split"], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = FileDiff(
        "preview.py",
        hunks=[
            DiffHunk(
                1,
                900,
                1,
                900,
                starts_file=True,
                file_path="preview.py",
                lines=[
                    DiffLine(
                        number,
                        number,
                        f"old {number}" if number == 750 else f"line {number}",
                        f"line {number}",
                        is_modified=number == 750,
                    )
                    for number in range(1, 901)
                ],
            )
        ],
    )
    canonical = FileDiff(
        "All files",
        hunks=[
            DiffHunk(
                1,
                20,
                1,
                20,
                starts_file=True,
                file_path="first.py",
                lines=[DiffLine(n, n, "context", "context") for n in range(1, 21)],
            ),
            *source.hunks,
        ],
        show_hunk_headers=False,
    )
    store = PRStore()
    store.state.files = [PRFile(filename=name) for name in ("first.py", "preview.py")]
    store.state.file_diffs["preview.py"] = source
    draft = PendingReviewComment(
        body="keep this draft " * 50, path="preview.py", line=749
    )
    store.state.pending_review.comments = [draft]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode=mode)

    async with TestApp().run_test(size=(120, 30)) as pilot:
        view = pilot.app.query_one(DiffView)
        await view.show_diff(canonical.filename, canonical)
        await pilot.pause()
        original_index = view._line_index_by_file_new_number[("preview.py", 750)]
        view.jump_to_line_index(original_index, side="LEFT", focus=True)
        view.cursor_column = 3
        await pilot.pause()
        offset = view._current_cursor_viewport_offset()
        cache = view._diff_plan_cache
        assert cache is not None
        revision = cache.revision
        renders: list[int] = []
        plans: list[FileDiff] = []
        render = view._render_diff
        build = diff_plan.build_diff_plan

        async def record_render() -> None:
            renders.append(view.cursor_line)
            assert view._virt.window_start <= view.cursor_line <= view._virt.window_end
            await render()

        def record_plan(diff: FileDiff, **kwargs):
            plans.append(diff)
            return build(diff, **kwargs)

        monkeypatch.setattr(view, "_render_diff", record_render)
        monkeypatch.setattr(diff_plan, "build_diff_plan", record_plan)
        for changed in (False, True):
            renders.clear()
            assert await view.show_full_file_preview(
                "preview.py", _content(950), source_diff=source
            )
            await pilot.pause()
            assert renders == [749]
            assert view._current_cursor_viewport_offset() == 2
            assert view._saved_diff_plan_cache is cache
            if changed:
                source.hunks[0].lines[749].new_content = "updated while previewing"
            renders.clear()
            plans.clear()
            view.action_toggle_full_file()
            await wait_until(
                lambda: (
                    view.current_diff is canonical
                    and view._committing_render_token is None
                ),
                timeout=2,
            )
            await pilot.pause()
            assert renders == [original_index]
            assert view._diff_plan_cache is cache
            assert view._saved_diff_plan_cache is None
            assert bool(plans) is changed
            assert (cache.revision > revision) is changed
            assert view._current_cursor_viewport_offset() == offset
            assert view.cursor_pane == "old" and view.cursor_column == 3
            assert store.state.pending_review.comments == [draft]
            assert store.get_file_diff("preview.py") is source
            assert store.is_inline_comment_diff_line(
                path="preview.py", line=750, side="RIGHT"
            )
            assert not store.is_inline_comment_diff_line(
                path="preview.py", line=950, side="RIGHT"
            )


@pytest.mark.asyncio
async def test_obsolete_restore_does_not_publish_or_move_the_newer_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = DiffView()
    source = parse_patch("@@ -1 +1 @@\n-old\n+new", "preview.py")
    newer = parse_patch("@@ -1 +1 @@\n-before\n+after", "newer.py")
    calls: list[object] = []

    async def superseded_render(*args, **kwargs) -> None:
        calls.append(args)
        view._render_request_token += 2
        view._source_diff = newer

    def capture_message(message) -> bool:
        calls.append(message)
        return True

    monkeypatch.setattr(view, "show_diff", superseded_render)
    monkeypatch.setattr(view, "post_message", capture_message)
    revision = view.view_revision
    await view._restore_diff_async("preview.py", source, None)
    assert len(calls) == 1
    assert view.current_diff is newer
    await view._restore_diff_async(
        "preview.py", source, None, expected_view_revision=revision
    )
    assert len(calls) == 1
    assert view.current_diff is newer


@pytest.mark.parametrize(
    ("line_number", "index"), [(None, 0), (-1, 0), (0, 0), (1, 0), (100, 99)]
)
def test_preview_position_uses_bounded_source_line(
    line_number: int | None, index: int
) -> None:
    position = full_preview_module.FullFileRestorePosition.for_preview(line_number)
    assert position.line == index
    assert position.cursor_pane == position.active_pane == "new"
    restore_index = full_preview_module.full_file_restore_line_index
    assert restore_index(position, line_count=10) == min(index, 9)
    assert restore_index(position, line_count=0) is None
