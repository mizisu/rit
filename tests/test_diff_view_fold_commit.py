"""Interaction, source revision, and cancellation coverage for fold commits."""

import asyncio
import threading
from dataclasses import replace
from typing import Literal

import pytest
from rich.console import RenderableType
from textual._compositor import CompositorUpdate
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import TextArea

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state.models import FileViewedState, PRFile, ReviewThread
from rit.state.store import PRStore
from rit.ui.widgets.diff_fold_state import LineAnchor
from rit.ui.widgets.diff_plan_cache import DiffPlanCache
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


class FoldApp(App[None]):
    def __init__(
        self,
        *,
        mode: Literal["unified", "split", "auto"] = "unified",
        virtual: bool = False,
    ) -> None:
        super().__init__()
        self.store = PRStore()
        names = ["one.py", "two.py", "three.py"]
        self.store.state.files = [PRFile(filename=name) for name in names]
        self.source = FileDiff(
            filename="All files",
            hunks=[
                DiffHunk(
                    1,
                    30,
                    1,
                    30,
                    starts_file=True,
                    file_path=name,
                    lines=[
                        DiffLine(
                            number,
                            number,
                            f"old {number} " + "word " * 40,
                            f"new {number} " + "word " * 40,
                            is_modified=number % 5 == 0,
                        )
                        for number in range(1, 31)
                    ],
                )
                for name in names
            ],
        )
        self.view = DiffView(store=self.store, mode=mode)
        if virtual:
            for name, value in (
                ("VIRTUALIZE_LINE_THRESHOLD", 10),
                ("VIRTUAL_WINDOW_RADIUS", 30),
                ("VIRTUAL_WINDOW_SHIFT_MARGIN", 4),
            ):
                setattr(self.view, name, value)

    def compose(self) -> ComposeResult:
        yield self.view

    async def reconcile(self) -> None:
        await self.view._refresh_viewed_folds(self.source, self.source.filename)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
@pytest.mark.parametrize("virtual_threshold", [70, 800])
async def test_fold_render_policy_tracks_source_lifecycle(
    mode: Literal["unified", "split"],
    virtual_threshold: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FoldApp(mode=mode)
    app.source.is_fully_refined = False
    replacement = replace(app.source)
    view = app.view
    monkeypatch.setattr(view, "VIRTUALIZE_LINE_THRESHOLD", virtual_threshold)
    view._manually_folded_files.update({"one.py", "two.py"})
    virtual = virtual_threshold < 90

    async with app.run_test(size=(120, 22)) as pilot:
        await view.show_diff(app.source.filename, app.source)
        assert len(view._all_lines) == 32
        assert view._virt.active is virtual
        assert len(view.query(".diff-block")) == 1
        assert view._source_line_count == 90

        view._set_file_header_selection(2)
        assert await view.toggle_current_file_fold()
        assert len(view._all_lines) == 3
        assert view._virt.active is virtual
        assert not view.query(".diff-block")
        assert await view.toggle_current_file_fold()
        assert len(view._all_lines) == 32
        assert view._virt.active is virtual
        assert len(view.query(".diff-block")) == 1

        assert await view.show_full_file_preview("three.py", "preview")
        assert view._source_line_count == 1
        assert not view._virt.active
        assert len(view.query("#line-0")) == 1
        view._restore_diff_view()
        await wait_until(
            lambda: (
                view.current_diff is app.source
                and view._committing_render_token is None
                and not view._virt.render_pending
            ),
            timeout=1,
        )
        await pilot.pause()
        assert view.current_diff is app.source
        assert view._source_line_count == 90
        assert view._virt.active is virtual
        assert len(view.query(".diff-block")) == 1

        hunk = app.source.hunks[2]
        app.source.hunks = [replace(hunk, lines=hunk.lines[:1])]
        app.source.is_fully_refined = True
        await view.show_diff(app.source.filename, app.source)
        assert view._source_line_count == 1
        assert not view._virt.active
        assert not view.query(".diff-block")
        assert len(view.query("#line-0")) == 1

        await view.show_diff(replacement.filename, replacement)
        assert view._source_line_count == 90
        assert view._virt.active is virtual
        assert len(view.query(".diff-block")) == 1
        replacement.hunks.clear()
        await view.show_diff(replacement.filename, replacement)
        assert view._source_line_count == 0
        assert not view._virt.active
        assert not view.query(".diff-block")
        assert view._virt.window_end == -1


@pytest.mark.asyncio
async def test_split_fold_retains_prefix_and_rebuilds_shifted_suffix() -> None:
    app = FoldApp(mode="split")
    async with app.run_test(size=(120, 22)) as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        content = view._content_widget
        assert content is not None
        boundary = view._file_header_hunk_index("two.py")
        assert boundary == 1
        header = view._get_file_header_widget(boundary)
        assert header is not None
        children = tuple(content.children)
        child_boundary = children.index(header)
        prefix = children[:child_boundary]
        old_suffix = children[child_boundary:]
        first_block = view._split_blocks_by_line[0]

        view._set_file_header_selection(boundary)
        assert await view.toggle_current_file_fold()
        await pilot.pause()

        assert tuple(content.children[:child_boundary]) == prefix
        assert all(child.is_mounted for child in prefix)
        assert not set(old_suffix) & set(content.children)
        assert view._split_blocks_by_line[0] is first_block
        assert len(view._all_lines) == 61
        assert all(
            line.line_index == index for index, line in enumerate(view._all_lines)
        )
        three_index = view._line_index_by_file_new_number[("three.py", 1)]
        three_block = view._split_blocks_by_line[three_index]
        assert three_index in three_block.line_indices

        collapsed_suffix = tuple(content.children[child_boundary:])
        assert await view.toggle_current_file_fold()
        await pilot.pause()

        assert tuple(content.children[:child_boundary]) == prefix
        assert all(child.is_mounted for child in prefix)
        assert not set(collapsed_suffix) & set(content.children)
        assert view._split_blocks_by_line[0] is first_block
        assert len(view._all_lines) == 90
        assert all(
            line.line_index == index for index, line in enumerate(view._all_lines)
        )


@pytest.mark.asyncio
async def test_split_suffix_rebuild_reindexes_later_multi_hunk_files() -> None:
    app = FoldApp(mode="split")
    app.source.hunks.insert(
        2,
        DiffHunk(
            31,
            1,
            31,
            1,
            file_path="two.py",
            lines=[DiffLine(31, 31, "old tail", "new tail")],
        ),
    )
    async with app.run_test(size=(120, 22)) as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        first_block = view._split_blocks_by_line[0]
        view._set_file_header_selection(1)

        assert await view.toggle_current_file_fold()
        await pilot.pause()

        assert view._split_blocks_by_line[0] is first_block
        assert view._file_header_hunk_index("three.py") == 2
        assert len(view.query("#file-header-2")) == 1
        assert not view.query("#file-header-3")
        assert len(view._all_lines) == 61
        assert all(
            line.line_index == index for index, line in enumerate(view._all_lines)
        )

        assert await view.toggle_current_file_fold()
        await pilot.pause()

        assert view._split_blocks_by_line[0] is first_block
        assert view._file_header_hunk_index("three.py") == 3
        assert len(view.query("#file-header-3")) == 1
        assert not view.query("#file-header-2")
        assert len(view._all_lines) == 91


@pytest.mark.asyncio
async def test_split_fold_rebuilds_full_document_when_prefix_has_comments() -> None:
    app = FoldApp(mode="split")
    async with app.run_test(size=(120, 22)) as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        content = view._content_widget
        assert content is not None
        first_header = view._get_file_header_widget(0)
        assert first_header is not None
        app.store.state.review_threads = [ReviewThread(path="one.py")]

        view._set_file_header_selection(1)
        assert await view.toggle_current_file_fold()
        await pilot.pause()

        assert first_header not in content.children
        assert len(view._all_lines) == 61


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_fold_preserves_visible_selection_panes_and_viewport(
    mode: Literal["unified", "split"],
) -> None:
    app = FoldApp(mode=mode, virtual=True)
    async with app.run_test(size=(120, 18)) as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        index = view._line_index_by_file_old_number[("two.py", 25)]
        view.jump_to_line_index(index, side="LEFT", focus=True)
        view.cursor_column = 8
        view.visual_type = "char"
        view.visual_anchor_line = index - 1
        view.visual_anchor_column = 3
        view.visual_mode = True
        await pilot.pause()
        await wait_until(lambda: not view._virt.render_pending, timeout=1)
        cursor = view._all_lines[index]
        selection = view._all_lines[index - 1]
        anchor = LineAnchor(cursor, "two.py", side="old")
        top = anchor.top(view, mounted=True)
        assert top is not None
        view.scroll_to(y=top - 4, animate=False, immediate=True)
        content = view._content_widget
        assert content is not None
        if mode == "split":
            view._sync_split_horizontal_scroll(12)
        else:
            content.scroll_to(x=12, animate=False, immediate=True)
        await pilot.pause()
        top = anchor.top(view, mounted=True)
        assert top is not None
        offset = top - int(view.scroll_y)
        panes = view.active_pane, view.cursor_pane
        assert panes == ("new", "old")
        horizontal = view._split_horizontal_scroll_x if view.split else content.scroll_x

        app.store.state.files[0].viewer_viewed_state = FileViewedState.VIEWED
        for expanded in [False, True, False]:
            if expanded:
                view._expanded_viewed_files.add("one.py")
            else:
                view._expanded_viewed_files.discard("one.py")
            await app.reconcile()
            await pilot.pause()
            assert view._all_lines[view.cursor_line] is cursor
            assert view.visual_anchor_line is not None
            assert view._all_lines[view.visual_anchor_line] is selection
            assert view.visual_mode and view.visual_type == "char"
            assert view.cursor_column == 8 and view.visual_anchor_column == 3
            assert (view.active_pane, view.cursor_pane) == panes
            top = anchor.top(view, mounted=True)
            assert top is not None and top - int(view.scroll_y) == offset
            actual_x = (
                view._split_horizontal_scroll_x if view.split else content.scroll_x
            )
            assert actual_x == horizontal


@pytest.mark.asyncio
async def test_latest_toggle_and_viewed_rollback_win_during_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FoldApp()
    async with app.run_test() as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        view._set_file_header_selection(0)
        prepared, release = asyncio.Event(), asyncio.Event()
        build = view._build_render_plan

        async def gated(*args, **kwargs):
            result = await build(*args, **kwargs)
            prepared.set()
            await release.wait()
            return result

        monkeypatch.setattr(view, "_build_render_plan", gated)
        for manual in [True, False]:
            prepared.clear()
            release.clear()
            metadata = [(line.line_index, line.file_path) for line in view._all_lines]
            if manual:
                view._request_toggle_file_fold()
            else:
                app.store.state.files[0].viewer_viewed_state = FileViewedState.VIEWED
                view.refresh_viewed_folds()
            try:
                await asyncio.wait_for(prepared.wait(), timeout=1)
                assert app._batch_count == 0
                assert [
                    (line.line_index, line.file_path) for line in view._all_lines
                ] == metadata
                if manual:
                    for _ in range(3):
                        view._request_toggle_file_fold()
                else:
                    app.store.state.files[
                        0
                    ].viewer_viewed_state = FileViewedState.UNVIEWED
                    view.refresh_viewed_folds()
            finally:
                release.set()
            await wait_until(lambda: not view._fold_worker_active, timeout=2)
            assert not view._folded_file_paths
            assert len(view._all_lines) == 90


@pytest.mark.asyncio
async def test_navigation_and_resize_invalidate_prepared_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FoldApp(mode="auto", virtual=True)
    async with app.run_test(size=(80, 18)) as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        assert not view.split
        view._set_file_header_selection(0)
        prepared = asyncio.Event()
        modes: list[bool] = []
        build = view._build_render_plan

        async def observed(*args, **kwargs):
            result = await build(*args, **kwargs)
            if result is not None:
                modes.append(result[2])
            prepared.set()
            return result

        monkeypatch.setattr(view, "_build_render_plan", observed)
        async with view.lock:
            refresh = asyncio.create_task(view.toggle_current_file_fold())
            await asyncio.wait_for(prepared.wait(), timeout=1)
            target = view._line_index_by_file_new_number[("two.py", 11)]
            line = view._all_lines[target]
            view.jump_to_line_index(target, side="RIGHT", focus=True)
            refined = replace(line, new_content=line.new_content + " refined")
            app.source.hunks[1].lines[10] = refined
            view.mode = "split"
            await pilot.resize_terminal(180, 20)
        await refresh
        await pilot.pause()
        assert modes == [False, True]
        assert view.split and view._rows_split_ready
        assert view._all_lines[view.cursor_line] is refined
        assert view.selected_file_header_path() is None
        assert view._folded_file_paths == frozenset({"one.py"})
        prepared.clear()
        view._manually_folded_files.discard("one.py")
        async with view.lock:
            refresh = asyncio.create_task(app.reconcile())
            await asyncio.wait_for(prepared.wait(), timeout=1)
            replacement = replace(refined, new_content="refined again")
            lines = list(app.source.hunks[1].lines)
            lines[10] = replacement
            app.source.hunks[1].lines = lines
        await refresh
        assert modes[-2:] == [True, True]
        assert view._all_lines[view.cursor_line] is replacement
        assert not view._folded_file_paths


@pytest.mark.asyncio
async def test_source_revision_retries_plan_after_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FoldApp()
    async with app.run_test() as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        previous_width = view._unified_code_width
        prepared = asyncio.Event()
        release = asyncio.Event()
        build_count = 0
        build = view._build_render_plan

        async def gated(*args, **kwargs):
            nonlocal build_count
            result = await build(*args, **kwargs)
            build_count += 1
            if build_count == 1:
                prepared.set()
                await release.wait()
            return result

        monkeypatch.setattr(view, "_build_render_plan", gated)
        render = asyncio.create_task(view.show_diff(app.source.filename, app.source))
        try:
            await asyncio.wait_for(prepared.wait(), timeout=1)
            line = app.source.hunks[1].lines[0]
            app.source.mark_planning_changed()
            line.new_content = "refined " * 100
        finally:
            release.set()

        await render
        assert build_count == 2
        assert view._unified_code_width > previous_width
        assert view._all_lines[30].new_content == "refined " * 100


@pytest.mark.asyncio
async def test_fold_preserves_inline_and_file_drafts_and_hidden_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FoldApp(mode="split")
    app.source.hunks.insert(
        1,
        DiffHunk(
            31, 1, 31, 1, file_path="one.py", lines=[DiffLine(31, 31, "tail", "tail")]
        ),
    )
    async with app.run_test(size=(120, 22)) as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        index = view._line_index_by_file_old_number[("two.py", 20)]
        view.jump_to_line_index(index, side="LEFT", focus=True)
        assert await view.open_inline_comment_editor()
        await pilot.pause()
        inline = view._inline_comment_editor_widget
        assert inline is not None and inline.is_open
        body = inline.query_one(TextArea)
        body.insert("unsaved")
        body.history.checkpoint()
        body.insert(" draft")
        body.selection = type(body.selection)((0, 1), (0, 5))
        selection, history = body.selection, body.history
        header = view._file_header_hunk_index("three.py")
        assert header is not None
        view._set_file_header_selection(header)
        assert await view.open_file_comment_editor()
        await pilot.pause()
        file_editor = view._file_comment_editor_widget
        assert file_editor is not None and file_editor.is_open
        file_editor.query_one(TextArea).insert("file draft")
        prepared, release = asyncio.Event(), asyncio.Event()
        build = view._build_render_plan

        async def gated(*args, **kwargs):
            prepared.set()
            await release.wait()
            return await build(*args, **kwargs)

        monkeypatch.setattr(view, "_build_render_plan", gated)
        app.store.state.files[0].viewer_viewed_state = FileViewedState.VIEWED
        refresh = asyncio.create_task(app.reconcile())
        try:
            await asyncio.wait_for(prepared.wait(), timeout=1)
            await pilot.press("x")
        finally:
            release.set()
        await refresh
        await pilot.pause()
        inline = view._inline_comment_editor_widget
        file_editor = view._file_comment_editor_widget
        assert inline is not None and inline.is_open
        assert file_editor is not None and file_editor.is_open
        body = inline.query_one(TextArea)
        assert body.text == "unsaved draft"
        assert body.selection == selection and body.history is history
        body.action_undo()
        assert body.text == "unsaved"
        body.action_redo()
        assert body.text == "unsaved draft"
        assert file_editor.query_one(TextArea).text == "file draftx"
        assert view._inline_comment_editor_target == ("two.py", 20, "LEFT")
        assert view._inline_comment_editor_line_index == view.line_index_for_location(
            "two.py", 20, "LEFT"
        )
        assert view._file_comment_editor_hunk_index == view._file_header_hunk_index(
            "three.py"
        )

        app.store.state.files[1].viewer_viewed_state = FileViewedState.VIEWED
        await app.reconcile()
        assert view._inline_comment_editor_widget is None
        assert view._inline_comment_editor_target == ("two.py", 20, "LEFT")
        view._expanded_viewed_files.add("two.py")
        await app.reconcile()
        inline = view._inline_comment_editor_widget
        assert inline is not None and inline.is_open
        assert inline.query_one(TextArea).text == "unsaved draft"
        file_editor = view._file_comment_editor_widget
        assert file_editor is not None
        assert file_editor.query_one(TextArea).text == "file draftx"
        index = view._line_index_by_file_old_number[("two.py", 10)]
        view.jump_to_line_index(index, side="LEFT", focus=True)
        assert await view.open_inline_comment_editor()
        await pilot.pause()
        inline = view._inline_comment_editor_widget
        assert inline is not None
        assert inline.query_one(TextArea).text == ""
        assert view._inline_comment_editor_target == ("two.py", 10, "LEFT")
        file_editor = view._file_comment_editor_widget
        assert file_editor is not None
        assert file_editor.query_one(TextArea).text == "file draftx"


@pytest.mark.asyncio
async def test_cancelled_preparation_cannot_publish_over_new_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FoldApp()
    async with app.run_test() as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        view._set_file_header_selection(0)
        original_lines = view._all_lines
        metadata = [(line.line_index, line.file_path) for line in original_lines]
        prepared, release = threading.Event(), threading.Event()
        prepare = DiffPlanCache.prepare

        def gated(cache: DiffPlanCache, diff: FileDiff):
            result = prepare(cache, diff)
            prepared.set()
            assert release.wait(timeout=3)
            return result

        monkeypatch.setattr(DiffPlanCache, "prepare", gated)
        refresh = asyncio.create_task(view.toggle_current_file_fold())
        replacement = FileDiff(
            "new.py", hunks=[DiffHunk(1, 1, 1, 1, lines=[DiffLine(1, 1, "new", "new")])]
        )
        try:
            await wait_until(prepared.is_set, timeout=1)
            refresh.cancel()
            next_render = asyncio.create_task(view.show_diff("new.py", replacement))
            await wait_until(lambda: view._requested_source is replacement)
            assert not refresh.done() and not next_render.done()
            assert view._source_line_count == 90
            assert view._all_lines is original_lines
            assert [
                (line.line_index, line.file_path) for line in original_lines
            ] == metadata
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await refresh
        await next_render
        assert view.current_diff is replacement
        assert view._source_line_count == 1
        assert [
            (line.line_index, line.file_path) for line in original_lines
        ] == metadata
        cache = view._diff_plan_cache
        assert cache is not None
        revision = cache.revision
        await wait_until(
            lambda: any(key[0] == id(replacement) for key in view._hl_state.cache)
        )
        changed = replacement.hunks[0].lines[0]
        changed.new_content = "界" * 60
        await view.show_diff("new.py", replacement)
        assert view._diff_plan_cache is cache and cache.revision > revision
        assert not cache.has_source_changes
        assert view._unified_code_width == 120
        await wait_until(
            lambda: (
                changed.highlighted_new_content is not None
                and changed.highlighted_new_content.plain == changed.new_content
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "target_hunk"), [("unified", 0), ("split", 1)]
)
async def test_cancelled_commit_finishes_mount_before_replacement(
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["unified", "split"],
    target_hunk: int,
) -> None:
    app = FoldApp(mode=mode)
    async with app.run_test() as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        await pilot.pause()
        view._set_file_header_selection(target_hunk)
        target_path = app.source.hunks[target_hunk].file_path
        assert target_path is not None
        content = view._content_widget
        assert content is not None
        remove = content.remove_children
        removed, release = asyncio.Event(), asyncio.Event()

        async def paused_remove(*args, **kwargs):
            await remove(*args, **kwargs)
            removed.set()
            await release.wait()

        monkeypatch.setattr(content, "remove_children", paused_remove)
        preparing_replacement, release_replacement = asyncio.Event(), asyncio.Event()
        build = view._build_render_plan

        async def paused_replacement(diff: FileDiff, **kwargs):
            if diff.filename == "new.py":
                preparing_replacement.set()
                await release_replacement.wait()
            return await build(diff, **kwargs)

        monkeypatch.setattr(view, "_build_render_plan", paused_replacement)
        refresh = asyncio.create_task(view.toggle_current_file_fold())
        replacement = FileDiff(
            "new.py", hunks=[DiffHunk(1, 1, 1, 1, lines=[DiffLine(1, 1, "new", "new")])]
        )
        try:
            await asyncio.wait_for(removed.wait(), timeout=1)
            assert app._batch_count > 0
            refresh.cancel()
            next_render = asyncio.create_task(view.show_diff("new.py", replacement))
            await wait_until(lambda: view._requested_source is replacement)
            assert not refresh.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await refresh
        try:
            await asyncio.wait_for(preparing_replacement.wait(), timeout=1)
            assert view.current_diff is app.source
            assert len(view.query(f"#file-header-{target_hunk}")) == 1
            target_line = view.file_start_line_index(target_path)
            assert target_line is not None
            assert view._all_lines[target_line].is_folded_file_placeholder
            assert all(child.is_mounted for child in content.children)
            assert app._batch_count == 0
        finally:
            release_replacement.set()
        await next_render
        assert view.current_diff is replacement
        assert len(view.query("#line-0")) == 1
        assert all(child.is_mounted for child in content.children)
        assert view._committing_render_token is None
        assert not view._suspend_scroll_virtual_window_watch
        assert app._batch_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
@pytest.mark.parametrize("virtual", [False, True])
async def test_fold_first_painted_frame_has_final_header_position(
    mode: Literal["unified", "split"],
    virtual: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FoldApp(mode=mode, virtual=virtual)
    if virtual:
        monkeypatch.setattr(app.view, "VIRTUALIZE_LINE_THRESHOLD", 70)
    for hunk in app.source.hunks[:-1]:
        for line in hunk.lines:
            line.old_content = f"old {line.old_line_no}"
            line.new_content = f"new {line.new_line_no}"
    async with app.run_test(size=(120, 18)) as pilot:
        view = app.view
        await view.show_diff(app.source.filename, app.source)
        view.focus()
        await pilot.pause()
        frames: list[tuple[bool, int | None, float]] = []
        filename: str | None = None
        display = app._display

        def record_frame(screen: Screen, renderable: RenderableType | None) -> None:
            if (
                filename is not None
                and not app._batch_count
                and isinstance(renderable, CompositorUpdate)
            ):
                index = view._file_header_hunk_index(filename)
                header = (
                    view._get_file_header_widget(index) if index is not None else None
                )
                offset = (
                    header.region.y - view.scrollable_content_region.y
                    if header is not None and header.region.height
                    else None
                )
                frames.append((view._is_file_folded(filename), offset, view.scroll_y))
            display(screen, renderable)

        monkeypatch.setattr(app, "_display", record_frame)
        for filename, requested_offset in (
            ("two.py", 5),
            ("three.py", 16),
            ("three.py", 5),
        ):
            index = view._file_header_hunk_index(filename)
            assert index is not None
            view._set_file_header_selection(index)
            view.scroll_to(
                y=view._hunk_header_top_offsets[index] - requested_offset,
                animate=False,
                immediate=True,
            )
            await wait_until(lambda: not view._virt.render_pending)
            await pilot.pause()
            for folded in (True, False):
                frames.clear()
                await pilot.press("enter")
                await wait_until(
                    lambda filename=filename, folded=folded: (
                        not view._fold_worker_active
                        and not view._virt.render_pending
                        and view._is_file_folded(filename) == folded
                    )
                )
                await pilot.pause()
                index = view._file_header_hunk_index(filename)
                assert index is not None
                header = view._get_file_header_widget(index)
                assert header is not None
                offset = header.region.y - view.scrollable_content_region.y
                painted = [
                    (y, scroll) for state, y, scroll in frames if state == folded
                ]
                assert painted
                assert set(painted) == {(offset, view.scroll_y)}
                assert offset + view.scroll_y == view._hunk_header_top_offsets[index]
                if filename == "two.py":
                    assert offset == 5
                elif folded:
                    assert view.scroll_y == view.max_scroll_y
                    assert 0 <= offset < view.scrollable_content_region.height
