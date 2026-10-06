from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterable
from contextlib import nullcontext
from typing import ClassVar, Literal
from unittest.mock import AsyncMock

import pytest
from textual import events
from textual.app import App, ComposeResult
from textual.selection import Selection
from textual.widget import Widget
from textual.widgets import TextArea

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import PRFile, ReviewThread
from rit.state.store import PRStore
from rit.ui.components.combined_diff import build_combined_diff_document
from rit.ui.widgets import (
    diff_blocks,
    diff_comments,
    diff_folding,
    diff_highlight,
    diff_virtual,
)
from rit.ui.widgets.diff_plan import build_diff_plan
from rit.ui.widgets.diff_types import VirtualState
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


class VirtualLineGroupView:
    def __init__(self) -> None:
        patch = """@@ -1,2 +1,2 @@
 line1
 line2
@@ -20,2 +20,2 @@
 line20
 line21"""
        self._diff = parse_patch(patch, "test.py")
        plan = build_diff_plan(self._diff)
        self._all_lines = NoSliceLines(plan.all_lines)
        self._hunk_index_by_line = plan.hunk_index_by_line


class NoSliceLines(list):
    def __getitem__(self, index):
        assert not isinstance(index, slice), (
            "virtual grouping should not copy line slices"
        )
        return super().__getitem__(index)


class CursorDrivenVirtualRenderView:
    def __init__(self) -> None:
        self._render_request_token = 7
        self._virt = VirtualState(
            active=True,
            render_pending=True,
            cursor_shift_pending=True,
        )
        self.refresh_callbacks: list[Callable[[], None]] = []
        self.finalized: list[tuple[int, int]] = []
        self.revealed = False
        self.is_mounted = True
        self.mounted = False
        self.layout_reflows = 0

    def _reflow_retained_layout(self) -> None:
        assert self.mounted
        self.layout_reflows += 1

    def run_worker(
        self, coroutine: Coroutine[object, object, None], **_kwargs: object
    ) -> None:
        coroutine.close()
        raise AssertionError("Catch up before releasing the current render batch")

    async def _await_content_mounts(self) -> None:
        assert self._virt.render_pending
        self.mounted = True

    def _is_current_render_request(self, request_token: int) -> bool:
        return request_token == self._render_request_token

    def call_after_refresh(self, callback: Callable[[], None]) -> None:
        self.refresh_callbacks.append(callback)

    def _finalize_render_state_if_current(self, request_token: int) -> None:
        assert self._is_current_render_request(request_token)
        assert self.mounted
        self.finalized.append((self._virt.rendered_start, self._virt.rendered_end))


class HeaderWidget:
    def __init__(self) -> None:
        self.removed = False


@pytest.mark.asyncio
async def test_widget_removal_finishes_before_repeated_render_cancellation() -> None:
    started, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def message_pump_exit() -> None:
        await release.wait()
        closed.set()

    child = asyncio.create_task(message_pump_exit())

    async def remove_children() -> None:
        started.set()
        await asyncio.gather(child)

    render = asyncio.create_task(diff_virtual._finish_widget_removal(remove_children()))
    await started.wait()
    render.cancel()
    await asyncio.sleep(0)
    assert not render.done() and not child.cancelled()
    render.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await render
    assert closed.is_set() and child.done() and not child.cancelled()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError, asyncio.CancelledError])
async def test_widget_removal_propagates_its_own_failure(
    error: type[BaseException],
) -> None:
    async def failed_removal() -> None:
        raise error

    with pytest.raises(error):
        await diff_virtual._finish_widget_removal(failed_removal())


@pytest.mark.parametrize(
    "source_count, projected_count, virtual_threshold, block_threshold, virtual, blocks",
    [
        (0, 0, 800, 40, False, False),
        (39, 1, 800, 40, False, False),
        (40, 1, 800, 40, False, True),
        (800, 1, 800, 40, False, True),
        (801, 1, 800, 40, True, True),
        (0, 801, 800, 40, True, True),
        (90, 3, 90, 91, False, False),
        (12, 1, 10, 40, True, True),
        (12, 1, 800, 12, False, True),
    ],
)
def test_render_eligibility_uses_source_and_projection_sizes(
    source_count: int,
    projected_count: int,
    virtual_threshold: int,
    block_threshold: int,
    virtual: bool,
    blocks: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = DiffView()
    view._source_line_count = source_count
    view._all_lines = [DiffLine(1, 1, "line", "line")] * projected_count
    monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", virtual_threshold)
    monkeypatch.setattr(view, "BLOCK_RENDER_LINE_THRESHOLD", block_threshold)
    monkeypatch.setattr(view, "VIRTUAL_WINDOW_RADIUS", 3)

    diff_virtual._configure_virtual_window(view)

    assert view._virt.active is virtual
    assert view._virt.window_start == 0
    assert view._virt.window_end == (
        min(projected_count - 1, 6) if virtual else projected_count - 1
    )
    for split in (False, True):
        view.split = split
        assert diff_blocks._should_use_unified_block_renderer(view) is blocks
        assert diff_blocks._should_use_split_block_renderer(view) is (blocks and split)
    assert diff_highlight._should_use_windowed_highlight_strategy(view) is blocks
    assert diff_blocks._block_chunk_limit(view) == (
        None if blocks and not virtual else view.UNIFIED_BLOCK_CHUNK_SIZE
    )
    view._showing_full_file = True
    assert diff_blocks._block_chunk_limit(view) == view.UNIFIED_BLOCK_CHUNK_SIZE


def test_iter_virtualized_line_groups_does_not_copy_line_window() -> None:
    view = VirtualLineGroupView()

    groups = list(diff_virtual._iter_virtualized_line_groups(view, 1, 2))

    assert all(not isinstance(group, list) for group in groups)
    assert [[line.line_index for line in group] for group in groups] == [[1], [2]]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_virtual_window_shift_preserves_file_headers(
    mode: Literal["unified", "split"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def file_hunk(path: str) -> DiffHunk:
        return DiffHunk(
            old_start=1,
            old_count=8,
            new_start=1,
            new_count=8,
            lines=[
                DiffLine(
                    old_line_no=line_number,
                    new_line_no=line_number,
                    old_content=f"{path} {line_number}",
                    new_content=f"{path} {line_number}",
                    file_path=path,
                )
                for line_number in range(1, 9)
            ],
            starts_file=True,
            file_path=path,
        )

    diff = FileDiff(
        filename="All files",
        hunks=[file_hunk("one.py"), file_hunk("two.py"), file_hunk("folded.py")],
        show_hunk_headers=False,
    )
    diff, _ = diff_folding.build_viewed_file_fold_diff(
        diff, is_collapsed=lambda path: path == "folded.py"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode=mode, id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 4)) as pilot:
        diff_view = app.query_one(DiffView)
        monkeypatch.setattr(diff_view, "BLOCK_RENDER_LINE_THRESHOLD", 1)
        monkeypatch.setattr(diff_view, "VIRTUALIZE_LINE_THRESHOLD", 1)
        monkeypatch.setattr(diff_view, "VIRTUAL_WINDOW_RADIUS", 3)
        monkeypatch.setattr(diff_view, "VIRTUAL_WINDOW_SHIFT_MARGIN", 1)
        await diff_view.show_diff("All files", diff)
        await pilot.pause()

        assert diff_view._virt.active
        first_header = diff_view.query_one("#file-header-0")
        first_block = diff_view._line_widgets_by_index[3]
        assert len(diff_view.query("#file-header-1")) == 0

        diff_virtual._set_virtual_window_around(diff_view, 4)
        assert await diff_virtual._try_shift_virtual_window_incremental(diff_view)
        await pilot.pause()

        assert diff_view.query_one("#file-header-0") is first_header
        assert diff_view._line_widgets_by_index[3] is first_block
        assert diff_view._virt.rendered_start == 0

        diff_virtual._set_virtual_window_around(diff_view, 6)
        assert await diff_virtual._try_shift_virtual_window_incremental(diff_view)
        await pilot.pause()

        second_header = diff_view.query_one("#file-header-1")
        second_file_lines = diff_view._line_widgets_by_index[8]
        assert second_header.region.y < second_file_lines.region.y

        diff_virtual._set_virtual_window_around(diff_view, 10)
        assert await diff_virtual._try_shift_virtual_window_incremental(diff_view)
        await pilot.pause()
        assert len(diff_view.query("#file-header-0")) == 0
        assert diff_view.query_one("#file-header-1") is second_header
        assert diff_view._line_widgets_by_index[8] is second_file_lines

        for center in (14, 12, 14, 10):
            diff_virtual._set_virtual_window_around(diff_view, center)
            assert await diff_virtual._try_shift_virtual_window_incremental(diff_view)
            await pilot.pause()
            assert bool(diff_view.query("#file-header-2")) is (center == 14)
            assert 16 not in diff_view._line_widgets_by_index

        diff_virtual._set_virtual_window_around(diff_view, 4)
        assert await diff_virtual._try_shift_virtual_window_incremental(diff_view)
        await pilot.pause()

        assert len(diff_view.query("#file-header-1")) == 0

        diff_virtual._set_virtual_window_around(diff_view, 2)
        assert await diff_virtual._try_shift_virtual_window_incremental(diff_view)
        await pilot.pause()

        first_header = diff_view.query_one("#file-header-0")
        first_file_lines = diff_view._line_widgets_by_index[0]
        assert first_header.region.y < first_file_lines.region.y


@pytest.mark.asyncio
async def test_virtual_scroll_preserves_per_file_layout_for_code_and_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = [
        PRFile(filename="added.py", status="added", additions=40),
        PRFile(filename="removed.py", status="removed", deletions=40),
        PRFile(filename="modified.py", additions=1, deletions=1),
    ]
    patches = {
        "added.py": "@@ -0,0 +1,40 @@\n"
        + "\n".join(f"+value_{number} = {number}" for number in range(1, 41)),
        "removed.py": "@@ -1,40 +0,0 @@\n"
        + "\n".join(f"-value_{number} = {number}" for number in range(1, 41)),
        "modified.py": "@@ -1,40 +1,40 @@\n-old_value\n+new_value\n"
        + "\n".join(f" value_{number} = {number}" for number in range(2, 41)),
    }
    document = build_combined_diff_document(
        files,
        {path: parse_patch(patch, path) for path, patch in patches.items()},
    )
    assert document is not None
    document.diff.show_hunk_headers = True

    store = PRStore()
    for index, file in enumerate(files):
        side = "LEFT" if file.status == "removed" else "RIGHT"
        store.save_pending_file_comment("file draft", path=file.filename)
        store.save_pending_inline_comment(
            "line draft", path=file.filename, line=23, side=side
        )
        store.state.review_threads.append(
            ReviewThread.model_validate(
                {
                    "path": file.filename,
                    "line": 23,
                    "originalLine": 23,
                    "diffSide": side,
                    "isResolved": True,
                    "comments": {
                        "nodes": [
                            {
                                "databaseId": index + 1,
                                "body": "review comment",
                                "path": file.filename,
                                "line": 23,
                                "originalLine": 23,
                                "side": side,
                            }
                        ]
                    },
                }
            )
        )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(store=store, mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 16)) as pilot:
        view = app.query_one(DiffView)
        monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", 10)
        monkeypatch.setattr(view, "VIRTUAL_WINDOW_RADIUS", 10)
        monkeypatch.setattr(view, "VIRTUAL_WINDOW_SHIFT_MARGIN", 2)
        await view.show_diff("All files", document.diff)
        await pilot.pause()
        assert view.split and view._virt.active

        def assert_layout() -> None:
            for line_index in range(
                view._virt.rendered_start, view._virt.rendered_end + 1
            ):
                line = view._all_lines[line_index]
                split = line.file_path == "modified.py"
                widget = view._line_widgets_by_index[line_index]
                assert widget.has_class("split-container") is split, (
                    line.file_path,
                    line.old_line_no,
                    line.new_line_no,
                )
                if (line.new_line_no or line.old_line_no) != 23:
                    continue
                for layouts in (
                    view._comment_layout_widgets_by_line[line_index],
                    view._pending_comment_layout_widgets_by_line[line_index],
                ):
                    assert layouts
                    assert all(
                        layout.has_class("diff-comment-row-split") is split
                        for layout in layouts
                    )
            for (
                hunk_index,
                layouts,
            ) in view._file_comment_annotation_widgets_by_hunk.items():
                split = files[hunk_index].status == "modified"
                assert all(
                    layout.has_class("diff-comment-row-split") is split
                    for layout in layouts
                )
            for hunk_index, header in view._hunk_header_widgets.items():
                assert header.has_class("split-hunk-header-scroll") is (
                    files[hunk_index].status == "modified"
                )

        assert_layout()
        for center in (15, 30, 45, 60, 75, 90, 105, 90, 75, 60, 45, 30, 15, 5):
            previous_lines = dict(view._line_widgets_by_index)
            previous_headers = dict(view._file_header_widgets)
            previous_comments = dict(view._comment_layout_widgets_by_line)
            diff_virtual._set_virtual_window_around(view, center)
            assert await diff_virtual._try_shift_virtual_window_incremental(view)
            await pilot.pause()
            assert_layout()
            for index in previous_lines.keys() & view._line_widgets_by_index.keys():
                assert view._line_widgets_by_index[index] is previous_lines[index]
            for index in previous_headers.keys() & view._file_header_widgets.keys():
                assert view._file_header_widgets[index] is previous_headers[index]
            for index in (
                previous_comments.keys() & view._comment_layout_widgets_by_line.keys()
            ):
                assert (
                    view._comment_layout_widgets_by_line[index]
                    is previous_comments[index]
                )


@pytest.mark.parametrize("first,last", [(0, 20), (30, 80), (89, 99)])
def test_viewport_window_covers_both_edges(
    first: int, last: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = DiffView()
    view._all_lines = [DiffLine(1, 1, "line", "line")] * 100
    view._virt.active = True
    monkeypatch.setattr(view, "VIRTUAL_WINDOW_RADIUS", 3)
    monkeypatch.setattr(
        diff_virtual, "_viewport_line_range", lambda _view: (first, last)
    )

    assert diff_virtual._set_virtual_window_from_viewport(view)
    assert view._virt.window_start == max(0, first - 3)
    assert view._virt.window_end == min(99, last + 3)
    assert not diff_virtual._set_virtual_window_from_viewport(view)


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor_driven", [False, True])
async def test_pending_scroll_uses_latest_viewport_without_overriding_cursor(
    cursor_driven: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = DiffView()
    view._all_lines = [DiffLine(1, 1, "line", "line")] * 100
    view._virt = VirtualState(
        active=True,
        window_start=20,
        window_end=30,
        render_pending=True,
        cursor_shift_pending=cursor_driven,
        coalesced_center=90,
    )
    monkeypatch.setattr(view, "VIRTUAL_WINDOW_RADIUS", 3)
    monkeypatch.setattr(view, "batch", nullcontext)
    monkeypatch.setattr(diff_virtual, "_viewport_line_range", lambda _view: (80, 95))
    render = AsyncMock()
    monkeypatch.setattr(diff_virtual, "_render_virtual_window_and_finalize", render)

    await diff_virtual._run_virtual_window_render_for_request(
        view, view._render_request_token
    )

    render.assert_awaited_once_with(view)
    assert (view._virt.window_start, view._virt.window_end) == (
        (20, 30) if cursor_driven else (77, 98)
    )
    assert view._virt.coalesced_center == (90 if cursor_driven else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_wheel_scroll_covers_viewport_and_survives_widget_retirement(
    mode: Literal["unified", "split"], monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [
        DiffLine(number, number, f"old_{number}", f"new_{number}", is_modified=True)
        for number in range(1, 1001)
    ]
    diff = FileDiff("test.py", hunks=[DiffHunk(1, 1000, 1, 1000, lines=lines)])

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode=mode)

    app = TestApp()
    async with app.run_test(size=(120, 30)) as pilot:
        view = app.query_one(DiffView)
        await view.show_diff("test.py", diff)
        await pilot.pause()
        cursor = view.cursor_line
        x, y = 40, 15

        async def wheel(
            event_type: type[events.MouseScrollDown | events.MouseScrollUp],
        ) -> None:
            await app.on_event(
                event_type(
                    widget=None,
                    x=x,
                    y=y,
                    delta_x=0,
                    delta_y=0,
                    button=0,
                    shift=False,
                    meta=False,
                    ctrl=False,
                )
            )

        async def settled_at(expected_y: float) -> None:
            await wait_until(
                lambda: view.scroll_y == expected_y and not view._virt.render_pending,
                timeout=5.0,
            )
            await pilot.pause()
            first, last = diff_virtual._viewport_line_range(view)
            assert view._virt.rendered_start <= first <= last <= view._virt.rendered_end
            assert view.cursor_line == cursor
            region = view.scrollable_content_region
            strips = app.screen._compositor.render_strips()
            assert all(
                strips[row].text.strip() for row in range(region.y, region.bottom)
            )
            assert set(view._line_widgets_by_index) == set(
                range(view._virt.rendered_start, view._virt.rendered_end + 1)
            )
            assert len(view._line_widgets_by_index) <= (
                region.height
                + 2 * diff_virtual._effective_virtual_window_radius(view)
                + 2 * view.UNIFIED_BLOCK_CHUNK_SIZE
            )

        content = view.query_one("#diff-content")
        content.styles.height = 3
        await pilot.pause()
        assert content.virtual_size.height > content.content_size.height
        assert not content.allow_vertical_scroll
        y = content.region.y + 1
        await wheel(events.MouseScrollDown)
        await wait_until(lambda: view.scroll_y == app.scroll_sensitivity_y, timeout=5.0)
        assert content.scroll_y == 0
        content.styles.height = "auto"
        view.scroll_to(y=0, animate=False)
        y = 15
        await pilot.pause()

        for _ in range(12):
            await wheel(events.MouseScrollDown)
        await settled_at(12 * app.scroll_sensitivity_y)

        old_target, _ = app.screen.get_widget_at(x, y)
        assert old_target.has_class("code-content")
        remove = diff_virtual._remove_virtual_widgets
        injected = False

        async def remove_with_wheel(*widgets: Widget) -> None:
            nonlocal injected
            inject = not injected and any(
                old_target in widget.walk_children(with_self=True) for widget in widgets
            )
            if inject:
                injected = True
                await wheel(events.MouseScrollDown)
            await remove(*widgets)
            if inject:
                assert old_target._closed
                target, _ = app.screen.get_widget_at(x, y)
                assert not target._closed and not target._closing
                await wheel(events.MouseScrollDown)

        monkeypatch.setattr(diff_virtual, "_remove_virtual_widgets", remove_with_wheel)
        with monkeypatch.context() as retirement:
            retirement.setattr(
                diff_virtual,
                "_reuse_virtual_code_blocks",
                AsyncMock(return_value=False),
            )
            view._virt.render_pending = True
            diff_virtual._set_virtual_window_around(view, 500)
            destination = view._line_top_offsets[500]
            view.scroll_to(y=destination, animate=False)
            await diff_virtual._run_virtual_window_render_for_request(
                view, view._render_request_token
            )
        assert injected
        expected = destination + 2 * app.scroll_sensitivity_y
        await settled_at(expected)

        for direction, event_type in (
            (1, events.MouseScrollDown),
            (-1, events.MouseScrollUp),
        ):
            for _ in range(48):
                await wheel(event_type)
            expected += direction * 48 * app.scroll_sensitivity_y
            await settled_at(expected)

        view.cursor_line = view._viewport_center_line()
        await pilot.pause()
        assert await view.open_inline_comment_editor()
        await pilot.pause()
        editor = view._inline_comment_editor_widget
        assert editor is not None
        body = editor.query_one(TextArea)
        body.text = "draft kept while scrolling"
        body.move_cursor((0, 7))
        await pilot.pause()
        line = view.cursor_line
        for center in (line + 3, line - 3, line + 120, line):
            diff_virtual._set_virtual_window_around(view, center)
            view._virt.render_pending = True
            await diff_virtual._run_virtual_window_render_for_request(
                view, view._render_request_token
            )
            await wait_until(lambda: not view._virt.render_pending, timeout=5.0)
            await pilot.pause()
            current = view._inline_comment_editor_widget
            assert current is not None and current is editor
            assert body.has_focus
            current_body = current.query_one(TextArea)
            assert current_body.text == "draft kept while scrolling"
            assert current_body.cursor_location == (0, 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_virtual_spare_blocks_survive_changing_file_sizes(
    mode: Literal["unified", "split"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hunks = [
        DiffHunk(
            1,
            size,
            1,
            size,
            lines=[
                DiffLine(i, i, f"file{file}:{i}", f"file{file}:{i}")
                for i in range(1, size + 1)
            ],
            starts_file=True,
            file_path=f"file{file}.py",
        )
        for file, size in enumerate([10] * 8 + [1000] + [10] * 8)
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode=mode)

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        view = app.query_one(DiffView)
        await view.show_diff(
            "files.py", FileDiff("files.py", hunks=hunks, show_hunk_headers=False)
        )
        content = view.query_one("#diff-content")
        warmed: set[Widget] = set()
        for step, line in enumerate((20, 500, 1120, 20, 500, 1120, 20)):
            view.scroll_to(y=view._line_top_offsets[line], animate=False)
            await pilot.pause()
            await wait_until(
                lambda: (
                    not view._virt.render_pending
                    and view._virt.viewport_worker is None
                    and not view._hl_state.window_worker_active
                    and view.virtual_size.height
                    == view._virtual_content_height + content.scrollbar_size_horizontal
                ),
                timeout=5.0,
            )
            blocks = set(content.query(".diff-block"))
            if step == 2:
                warmed = blocks
            elif step > 2:
                assert blocks == warmed
            assert len(view._virt.spare_blocks) <= view.scrollable_content_region.height
            assert all(
                not block.display and not block.line_indices
                for block in view._virt.spare_blocks
            )
            assert all(
                not node.visible
                for block in view._virt.spare_blocks
                for node in block.walk_children(Widget, with_self=True)
            )
            assert set(view._virt.spare_blocks).isdisjoint(
                view._line_widgets_by_index.values()
            )
            assert view.virtual_size.height == (
                view._virtual_content_height + content.scrollbar_size_horizontal
            )
            first, last = diff_virtual._viewport_line_range(view)
            assert view._virt.rendered_start <= first <= last <= view._virt.rendered_end

        old_y = view.scroll_y
        old_headers = set(view._file_header_widgets)
        remove_headers = diff_virtual._remove_stale_virtual_file_headers
        redirected = False

        async def return_during_retirement(
            view: DiffView, start: int, end: int
        ) -> None:
            nonlocal redirected
            await remove_headers(view, start, end)
            if not redirected:
                redirected = True
                view.scroll_to(y=old_y, animate=False, immediate=True)

        with monkeypatch.context() as retirement:
            retirement.setattr(
                diff_virtual,
                "_remove_stale_virtual_file_headers",
                return_during_retirement,
            )
            view.scroll_to(y=view._line_top_offsets[500], animate=False)
            await wait_until(
                lambda: redirected and not view._virt.render_pending, timeout=5.0
            )
            await pilot.pause()
        assert view.scroll_y == old_y
        assert set(view._file_header_widgets) == old_headers
        assert set(content.query(".diff-block")) == warmed
        assert view.virtual_size.height == (
            view._virtual_content_height + content.scrollbar_size_horizontal
        )

        spare = view._virt.spare_blocks[0]
        code = spare.query_one(".code-content")
        app.screen.selections = {code: Selection(None, None)}
        view.scroll_to(y=view._line_top_offsets[500], animate=False)
        await wait_until(lambda: not view._virt.render_pending, timeout=5.0)
        await pilot.pause()
        assert spare._closed
        assert not view._virt.spare_blocks
        await view.show_diff("small.py", parse_patch("@@ -1 +1 @@\n small", "small.py"))
        await pilot.pause()
        assert not view._virt.spare_blocks and all(block._closed for block in warmed)


def test_extra_heights_single_entries_skip_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = object()
    thread = object()

    class View:
        _pending_comment_drafts_by_line: ClassVar[dict[int, list[object]]] = {
            3: [draft]
        }
        _comment_threads_by_line: ClassVar[dict[int, list[object]]] = {3: [thread]}

    monkeypatch.setattr(
        diff_virtual,
        "sum",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single extra-height entries should not use sum")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        diff_comments,
        "estimate_pending_draft_height",
        lambda _draft: 4,
    )
    monkeypatch.setattr(
        diff_comments,
        "estimate_thread_height",
        lambda _thread: 5,
    )

    assert diff_virtual._extra_heights_by_line(View()) == {3: 9}


@pytest.mark.asyncio
async def test_clear_virtual_hunk_headers_does_not_copy_header_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diff_virtual,
        "_remove_virtual_widgets",
        AsyncMock(side_effect=lambda header: setattr(header, "removed", True)),
    )

    class NoListItems:
        def __init__(self, items: Iterable[tuple[int, HeaderWidget]]) -> None:
            self._items = items

        def __iter__(self):
            return iter(self._items)

        def __len__(self) -> int:
            raise AssertionError("clearing virtual headers should not copy items")

    class HeaderMap(dict[int, HeaderWidget]):
        def items(self):
            return NoListItems(super().items())

    first = HeaderWidget()
    second = HeaderWidget()

    class View:
        _hunk_header_widgets = HeaderMap({1: first, 2: second})

    await diff_virtual._clear_virtual_hunk_headers(View())

    assert first.removed
    assert second.removed
    assert View._hunk_header_widgets == {}


@pytest.mark.asyncio
async def test_remove_stale_virtual_hunk_headers_does_not_copy_all_header_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        diff_virtual,
        "_remove_virtual_widgets",
        AsyncMock(side_effect=lambda header: setattr(header, "removed", True)),
    )

    class HeaderMap(dict[int, HeaderWidget]):
        def __iter__(self):
            raise AssertionError("stale header cleanup should not copy all keys")

    visible = HeaderWidget()
    stale = HeaderWidget()

    class View:
        _hunk_header_widgets = HeaderMap({1: visible, 2: stale})

        def _should_render_hunk_header(
            self,
            hunk_index: int,
            _window_start: int,
            _window_end: int,
        ) -> bool:
            return hunk_index == 1

        def _get_hunk_header_widget(self, hunk_index: int) -> HeaderWidget | None:
            return self._hunk_header_widgets.get(hunk_index)

    view = View()

    await diff_virtual._remove_stale_virtual_hunk_headers(view, 10, 20)

    assert not visible.removed
    assert stale.removed
    assert view._hunk_header_widgets == {1: visible}


@pytest.mark.parametrize("outside", [False, True])
def test_cursor_takes_over_a_prepared_viewport_frame(
    outside: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = CursorDrivenVirtualRenderView()
    state = view._virt
    state.window_start, state.window_end = 0, 100
    state.rendered_start, state.rendered_end = 0, 100
    state.cursor_shift_pending = False
    state.viewport_frame_token = view._render_request_token
    state.viewport_scroll_latched = True
    state.coalesced_center = 55
    state.pending_scroll = lambda: None
    launches: list[int] = []

    def set_window(_view: CursorDrivenVirtualRenderView, center: int) -> None:
        _view._virt.window_start, _view._virt.window_end = center - 20, center + 20

    def run_worker(
        coroutine: Coroutine[object, object, None], **_kwargs: object
    ) -> None:
        coroutine.close()
        launches.append(state.window_start)

    monkeypatch.setattr(view, "run_worker", run_worker)
    monkeypatch.setattr(
        diff_virtual, "_effective_virtual_window_shift_margin", lambda _: 1
    )
    monkeypatch.setattr(diff_virtual, "_set_virtual_window_around", set_window)

    diff_virtual._maybe_update_virtual_window(view, 250 if outside else 50)

    assert state.viewport_frame_token is None
    assert not state.viewport_scroll_latched
    assert state.coalesced_center is None and state.pending_scroll is None
    assert state.render_pending is outside
    assert state.cursor_shift_pending is outside
    assert len(launches) == int(outside)


@pytest.mark.asyncio
async def test_cursor_driven_virtual_render_reveals_before_releasing_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = CursorDrivenVirtualRenderView()

    async def shifted(_view: CursorDrivenVirtualRenderView) -> bool:
        return True

    def revealed(_view: CursorDrivenVirtualRenderView, _request_token: int) -> None:
        assert _view.layout_reflows == 1
        assert not _view._virt.render_pending
        _view.revealed = True

    monkeypatch.setattr(diff_virtual, "_try_shift_virtual_window_incremental", shifted)
    monkeypatch.setattr(diff_virtual, "_reveal_cursor_after_virtual_render", revealed)

    await diff_virtual._render_virtual_window_and_finalize(view)

    assert view.mounted
    assert len(view.refresh_callbacks) == 1
    assert view.revealed is True
    assert view.layout_reflows == 2
    assert view._virt.render_pending is False


@pytest.mark.asyncio
@pytest.mark.parametrize("cursor_driven", [False, True])
async def test_pending_viewport_catches_up_before_releasing_render_batch(
    cursor_driven: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = CursorDrivenVirtualRenderView()
    view._virt.cursor_shift_pending = cursor_driven
    view._virt.window_start, view._virt.window_end = 0, 10
    rendered: list[tuple[int, int]] = []

    async def shifted(_view: CursorDrivenVirtualRenderView) -> bool:
        start, end = _view._virt.window_start, _view._virt.window_end
        _view._virt.rendered_start, _view._virt.rendered_end = start, end
        rendered.append((start, end))
        _view._virt.coalesced_center = 25 if len(rendered) == 1 else None
        return True

    def latest_window(_view: CursorDrivenVirtualRenderView) -> bool:
        _view._virt.window_start, _view._virt.window_end = 17, 33
        return True

    monkeypatch.setattr(diff_virtual, "_try_shift_virtual_window_incremental", shifted)
    monkeypatch.setattr(diff_virtual, "_viewport_line_range", lambda _view: (20, 30))
    monkeypatch.setattr(
        diff_virtual, "_set_virtual_window_from_viewport", latest_window
    )
    monkeypatch.setattr(
        diff_virtual, "_effective_virtual_window_shift_margin", lambda _: 1
    )
    monkeypatch.setattr(
        diff_virtual,
        "_set_virtual_window_around",
        lambda _view, _: latest_window(_view),
    )
    monkeypatch.setattr(
        diff_virtual,
        "_reveal_cursor_after_virtual_render",
        lambda _view, _: setattr(_view, "revealed", True),
    )

    await diff_virtual._render_virtual_window_and_finalize(view)

    assert rendered == [(0, 10), (17, 33)]
    assert len(view.refresh_callbacks) == 1
    assert view.revealed is cursor_driven
    assert view.layout_reflows == (2 if cursor_driven else 1)
    for callback in view.refresh_callbacks:
        callback()
    assert view.finalized == [(17, 33)]
    assert view.mounted
    assert view.revealed is cursor_driven
    assert not view._virt.render_pending


@pytest.mark.asyncio
async def test_latest_placement_survives_virtual_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DiffView, "VIRTUALIZE_LINE_THRESHOLD", 20)
    patch = "@@ -1,300 +1,300 @@\n" + "\n".join(
        f" line{number}" for number in range(1, 301)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        view = app.query_one(DiffView)
        await view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        started, release = asyncio.Event(), asyncio.Event()
        shift = diff_virtual._try_shift_virtual_window_incremental

        async def delayed_shift(view: DiffView) -> bool:
            started.set()
            await release.wait()
            return await shift(view)

        monkeypatch.setattr(
            diff_virtual, "_try_shift_virtual_window_incremental", delayed_shift
        )
        view.jump_to_line_index(230, side="RIGHT", viewport_offset=7)
        await wait_until(started.is_set, timeout=5.0)
        view.jump_to_line_index(70, side="RIGHT", viewport_offset=0)
        release.set()
        await wait_until(
            lambda: not view._virt.render_pending and view._is_line_rendered(70),
            timeout=5.0,
        )
        await pilot.pause()
        assert view.cursor_line == 70
        assert (
            "line71"
            in app.screen._compositor.render_strips()[
                view.scrollable_content_region.y
            ].text
        )

        view.action_scroll_end()
        await wait_until(
            lambda: not view._virt.render_pending and view._is_line_rendered(299),
            timeout=5.0,
        )
        await pilot.pause()
        assert view.scroll_y == view.max_scroll_y
        view.action_center_cursor()
        center = (
            view.scrollable_content_region.y
            + view.scrollable_content_region.height // 2
        )
        await wait_until(
            lambda: "line300" in app.screen._compositor.render_strips()[center].text,
            timeout=5.0,
        )
