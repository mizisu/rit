"""Tests for DiffView split rendering and performance-oriented behavior."""

import asyncio
import threading

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.ui.widgets import diff_search as _search_mod
from rit.ui.widgets import diff_blocks as _blocks_mod
from rit.ui.widgets import diff_highlight as _hl_mod
from rit.ui.widgets import diff_virtual as _virtual_mod
from rit.ui.widgets.diff_view import DiffView, SplitDiffBlock, UnifiedDiffBlock
from rit.ui.widgets.diff_visual import LineContent
from tests.conftest import wait_until


def _as_plain(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


def _diff_view_render_idle(diff_view: DiffView) -> bool:
    return (
        not diff_view._hl_state.window_worker_active
        and diff_view._hl_state.queued_window is None
        and not diff_view._cursor_ui.flush_pending
        and not diff_view._virt.render_pending
    )


@pytest.mark.asyncio
async def test_split_mode_renders_old_and_new_panes_for_modified_line() -> None:
    """Split mode should render side-by-side panes with old/new content."""

    patch = """@@ -1,3 +1,3 @@
-old content here
+new content here
 line2
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        old_code = diff_view.query_one("#line-0-old .code-content", Static)
        new_code = diff_view.query_one("#line-0-new .code-content", Static)

        assert "old content here" in _as_plain(old_code)
        assert "new content here" in _as_plain(new_code)


@pytest.mark.asyncio
async def test_show_diff_reuses_highlight_cache_for_same_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Showing the same diff repeatedly should not re-highlight every time."""

    patch = """@@ -1,2 +1,2 @@
-old
+new"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    calls = {"count": 0}
    original = diff_highlight_module.highlight_lines_for_diff

    def counted_highlight(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff", counted_highlight
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        assert calls["count"] == 1


@pytest.mark.asyncio
async def test_show_diff_renders_plain_first_then_applies_highlight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache miss should render plain content first, then refresh highlighted content in place."""

    patch = """@@ -1,1 +1,1 @@
-old_value
+new_value"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    original = diff_highlight_module.highlight_lines_for_diff

    def blocking_highlight(*args, **kwargs):
        started.set()
        unblock.wait(timeout=1.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff", blocking_highlight
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()

        assert started.wait(timeout=1.0) is True
        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        modified = diff.hunks[0].lines[0]
        assert modified.highlighted_old_content is None
        assert modified.highlighted_new_content is None

        old_code = diff_view.query_one("#line-0-old .code-content", Static)
        new_code = diff_view.query_one("#line-0-new .code-content", Static)
        assert "old_value" in _as_plain(old_code)
        assert "new_value" in _as_plain(new_code)

        unblock.set()
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls
        assert modified.highlighted_old_content is not None
        assert modified.highlighted_new_content is not None
        assert any(
            cache_key[:2] == (id(diff), diff_view.word_diff_enabled)
            for cache_key in diff_view._hl_state.cache
        )


@pytest.mark.asyncio
async def test_rapid_show_diff_coalesces_full_highlight_to_latest_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid file switches should skip stale full-diff highlight jobs."""

    patch = "@@ -1,2 +1,2 @@\n-old\n+new"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    calls: list[str] = []
    original = diff_highlight_module.highlight_lines_for_diff

    def blocking_highlight(diff, *args, **kwargs):
        calls.append(diff_names[id(diff)])
        if len(calls) == 1:
            started.set()
            unblock.wait(timeout=1.0)
        return original(diff, *args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff", blocking_highlight
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff1 = parse_patch(patch, "one.py")
        diff2 = parse_patch(patch, "two.py")
        diff3 = parse_patch(patch, "three.py")

        diff_names = {
            id(diff1): "one.py",
            id(diff2): "two.py",
            id(diff3): "three.py",
        }

        await diff_view.show_diff("one.py", diff1)
        await pilot.pause()
        assert started.wait(timeout=1.0) is True

        await diff_view.show_diff("two.py", diff2)
        await diff_view.show_diff("three.py", diff3)
        await pilot.pause()

        unblock.set()
        await pilot.pause()
        await pilot.pause()

        assert calls[0] == "one.py"
        assert "three.py" in calls
        assert "two.py" not in calls


@pytest.mark.asyncio
async def test_rapid_windowed_highlight_coalesces_to_latest_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid range-highlight requests should skip intermediate queued windows."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 221))
    patch = f"@@ -1,220 +1,220 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    calls: list[tuple[int, int]] = []
    original = diff_highlight_module.highlight_lines_for_diff_range

    def blocking_range_highlight(diff, start_line, end_line, *args, **kwargs):
        calls.append((start_line, end_line))
        if len(calls) == 1:
            started.set()
            unblock.wait(timeout=1.0)
        return original(diff, start_line, end_line, *args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module,
        "highlight_lines_for_diff_range",
        blocking_range_highlight,
    )

    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "medium.py")

        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()
        assert started.wait(timeout=1.0) is True

        _hl_mod._queue_highlight_diff_range(diff_view, "medium.py", diff, 80, 110)
        _hl_mod._queue_highlight_diff_range(diff_view, "medium.py", diff, 150, 180)
        await pilot.pause()

        unblock.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert calls[-1] == (150, 180)
        assert (80, 110) not in calls


@pytest.mark.asyncio
async def test_windowed_highlight_refreshes_grouped_blocks_without_full_rerender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visible window highlighting should refresh grouped blocks without full `_render_diff()`."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    started = threading.Event()
    unblock = threading.Event()
    original = diff_highlight_module.highlight_lines_for_diff_range

    def blocking_range_highlight(*args, **kwargs):
        started.set()
        unblock.wait(timeout=1.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module,
        "highlight_lines_for_diff_range",
        blocking_range_highlight,
    )

    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        assert started.wait(timeout=1.0) is True
        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1
        assert diff.hunks[0].lines[0].highlighted_old_content is None

        unblock.set()
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls
        assert diff.hunks[0].lines[0].highlighted_old_content is not None


@pytest.mark.asyncio
async def test_dynamic_virtual_window_radius_shrinks_for_modified_heavy_diff() -> None:
    """Default adaptive windowing should use a smaller radius for modified-heavy diffs."""

    simple_patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f" line{i}" for i in range(1, 201)
    )
    modified_patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        simple_diff = parse_patch(simple_patch, "simple.py")
        await diff_view.show_diff("simple.py", simple_diff)
        await pilot.pause()
        simple_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

        modified_diff = parse_patch(modified_patch, "modified.py")
        await diff_view.show_diff("modified.py", modified_diff)
        await pilot.pause()
        modified_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

        assert modified_radius < simple_radius


@pytest.mark.asyncio
async def test_dynamic_virtual_window_radius_respects_rendered_row_height() -> None:
    """Unified modified-heavy diffs should use a smaller radius than split ones."""

    modified_patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class UnifiedApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class SplitApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    unified_app = UnifiedApp()
    async with unified_app.run_test(size=(100, 24)) as pilot:
        diff_view = unified_app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        diff = parse_patch(modified_patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        unified_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

    split_app = SplitApp()
    async with split_app.run_test(size=(100, 24)) as pilot:
        diff_view = split_app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        diff = parse_patch(modified_patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        split_radius = _virtual_mod._effective_virtual_window_radius(diff_view)

    assert unified_radius < split_radius


@pytest.mark.asyncio
async def test_dynamic_virtual_window_shift_margin_scales_with_adaptive_radius() -> (
    None
):
    """Adaptive shift margins should derive from the effective adaptive radius."""

    patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10

        diff = parse_patch(patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        radius = _virtual_mod._effective_virtual_window_radius(diff_view)
        assert _virtual_mod._effective_virtual_window_shift_margin(diff_view) == max(
            1, radius // diff_view.DYNAMIC_WINDOW_SHIFT_DIVISOR
        )


@pytest.mark.asyncio
async def test_custom_virtual_window_radius_override_is_respected() -> None:
    """Instance overrides should bypass adaptive radius tuning."""

    patch = "@@ -1,200 +1,200 @@\n" + "\n".join(
        f"-old{i}\n+new{i}" for i in range(1, 201)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 7

        diff = parse_patch(patch, "modified.py")
        await diff_view.show_diff("modified.py", diff)
        await pilot.pause()

        assert _virtual_mod._effective_virtual_window_radius(diff_view) == 7


@pytest.mark.asyncio
async def test_large_diff_uses_windowed_rendering_and_shifts_with_cursor() -> None:
    """Large diffs should render in a moving window instead of mounting all lines."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,41 @@\n{context_lines}\n+added_line"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        # Lower thresholds for deterministic test behavior.
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")

        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is True
        assert len(diff_view.query(".-virtual-buffer")) > 0

        # Move cursor deep into the file, which should shift the virtual window.
        diff_view.cursor_line = 20
        await pilot.pause()
        await pilot.pause()

        assert diff_view._virt.window_start > 0
        assert diff_view._virt.window_start <= 20 <= diff_view._virt.window_end

        rendered_line = diff_view._get_line_container(20)
        assert rendered_line is not None


@pytest.mark.asyncio
async def test_cursor_movement_uses_cached_widgets_after_render() -> None:
    """Cursor movement should not need fresh DOM queries after render completes."""

    patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        def fail_query(*args, **kwargs):
            raise AssertionError("cursor movement should use cached widgets")

        diff_view.query = fail_query  # type: ignore[method-assign]
        diff_view.query_one = fail_query  # type: ignore[method-assign]

        await pilot.press("j")
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.active_pane == "new"
        assert diff_view.cursor_column == 1


@pytest.mark.asyncio
async def test_horizontal_cursor_move_updates_only_active_widget() -> None:
    """Horizontal motions should not rebuild the inactive split-pane widget."""

    patch = """@@ -1,3 +1,3 @@
 line1
-old content here
+new content here
 line3"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("j")
        await pilot.pause()

        old_code = diff_view.query_one("#line-1-old .code-content", Static)
        new_code = diff_view.query_one("#line-1-new .code-content", Static)

        old_updates = {"count": 0}
        new_updates = {"count": 0}
        original_old_update = old_code.update
        original_new_update = new_code.update

        def counted_old_update(*args, **kwargs):
            old_updates["count"] += 1
            return original_old_update(*args, **kwargs)

        def counted_new_update(*args, **kwargs):
            new_updates["count"] += 1
            return original_new_update(*args, **kwargs)

        old_code.update = counted_old_update  # type: ignore[method-assign]
        new_code.update = counted_new_update  # type: ignore[method-assign]

        await pilot.press("l")
        await pilot.pause()

        assert diff_view.active_pane == "new"
        assert old_updates["count"] == 0
        assert new_updates["count"] == 1


@pytest.mark.asyncio
async def test_next_word_cross_line_uses_single_batched_cursor_pipeline() -> None:
    """`w` across lines should update cursor rendering once per affected line."""

    patch = """@@ -1,2 +1,2 @@
 line1
 line2"""

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")

        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        diff_view.cursor_column = len("line1") - 1
        await pilot.pause()

        updates = {"count": 0}
        original_update_line_cursor = diff_view._update_line_cursor

        def counted_update_line_cursor(line_idx: int) -> None:
            updates["count"] += 1
            original_update_line_cursor(line_idx)

        diff_view._update_line_cursor = counted_update_line_cursor  # type: ignore[method-assign]

        await pilot.press("w")
        await pilot.pause()

        assert diff_view.cursor_line == 1
        assert diff_view.cursor_column == 0
        assert updates["count"] == 2


@pytest.mark.asyncio
async def test_visual_selection_cache_tracks_rendered_window_only() -> None:
    """Visual selection cache should only contain currently rendered line indices."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 51))
    patch = f"@@ -1,50 +1,50 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        diff_view.focus()
        await pilot.pause()

        await pilot.press("V")
        await pilot.pause()

        if diff_view._visual_selection_specs:
            assert (
                min(diff_view._visual_selection_specs) >= diff_view._virt.window_start
            )
            assert max(diff_view._visual_selection_specs) <= diff_view._virt.window_end

        diff_view.cursor_line = 20
        await pilot.pause()
        await pilot.pause()

        await pilot.press("j")
        await pilot.pause()

        if diff_view._visual_selection_specs:
            assert (
                min(diff_view._visual_selection_specs) >= diff_view._virt.window_start
            )
            assert max(diff_view._visual_selection_specs) <= diff_view._virt.window_end


@pytest.mark.asyncio
async def test_medium_unified_diff_uses_blocks_without_virtualization() -> None:
    """Medium unified diffs should use grouped blocks before full virtualization kicks in."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 151))
    patch = f"@@ -1,150 +1,150 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view.query(".diff-block .code-content")) < 150


@pytest.mark.asyncio
async def test_medium_split_diff_uses_blocks_without_virtualization() -> None:
    """Medium split diffs should use grouped blocks before full virtualization kicks in."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 151))
    patch = f"@@ -1,150 +1,150 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view.query(".split-block .code-content")) < 300


@pytest.mark.asyncio
async def test_medium_unified_diff_uses_hunk_sized_blocks_without_chunk_splitting() -> (
    None
):
    """Medium unified diffs should group an entire hunk into one block."""

    first_hunk = "\n".join(f" line{i}" for i in range(1, 151))
    second_hunk = "\n".join(f" line{i}" for i in range(300, 450))
    patch = f"@@ -1,150 +1,150 @@\n{first_hunk}\n@@ -300,150 +300,150 @@\n{second_hunk}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        assert len(diff_view.query(".diff-block")) == 2


@pytest.mark.asyncio
async def test_medium_split_diff_uses_hunk_sized_blocks_without_chunk_splitting() -> (
    None
):
    """Medium split diffs should group an entire hunk into one split block."""

    first_hunk = "\n".join(f" line{i}" for i in range(1, 151))
    second_hunk = "\n".join(f" line{i}" for i in range(300, 450))
    patch = f"@@ -1,150 +1,150 @@\n{first_hunk}\n@@ -300,150 +300,150 @@\n{second_hunk}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)

        diff = parse_patch(patch, "medium.py")
        await diff_view.show_diff("medium.py", diff)
        await pilot.pause()

        assert diff_view._virt.active is False
        split_blocks = list(diff_view.query(".split-block"))
        hunk_headers = list(diff_view.query(".hunk-header"))
        assert len(split_blocks) == 2
        assert len(hunk_headers) == 2

        first_block = split_blocks[0]
        second_header = hunk_headers[1]
        second_block = split_blocks[1]

        assert first_block.region.height == 150
        assert (
            second_header.region.y == first_block.region.y + first_block.region.height
        )
        assert (
            second_block.region.y
            == second_header.region.y + second_header.region.height
        )


@pytest.mark.asyncio
async def test_virtualized_unified_mode_groups_simple_lines_into_blocks() -> None:
    """Large unified diffs should group simple visible lines into block widgets."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view._unified_blocks_by_line) == rendered_line_count
        assert len(diff_view.query(".diff-block .code-content")) < rendered_line_count
        assert len(diff_view.query(".diff-block-anchor")) == 0


@pytest.mark.asyncio
async def test_virtualized_unified_mode_groups_modified_lines_into_blocks() -> None:
    """Large unified diffs should group modified lines into unified block widgets."""

    patch = "@@ -1,40 +1,40 @@\n" + "\n".join(f"-old{i}\n+new{i}" for i in range(1, 41))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view._unified_blocks_by_line) == rendered_line_count

        block = diff_view._unified_blocks_by_line[rendered_start]
        assert isinstance(block, UnifiedDiffBlock)
        assert "-" in block._annotations.numbers[0].plain
        assert "+" in block._annotations.numbers[1].plain

        visual = block._code._render()
        assert isinstance(visual, LineContent)
        old_line = visual.code_lines[0]
        new_line = visual.code_lines[1]
        assert old_line is not None
        assert new_line is not None
        assert "old" in old_line.plain
        assert "new" in new_line.plain
        assert len(visual.code_lines) == rendered_line_count * 2
        assert len(block._annotations.numbers) == rendered_line_count * 2


@pytest.mark.asyncio
async def test_virtualized_split_mode_groups_simple_lines_into_blocks() -> None:
    """Large split diffs should group simple visible lines into split block widgets."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view._split_blocks_by_line) == rendered_line_count
        assert (
            len(diff_view.query(".split-block .code-content")) < rendered_line_count * 2
        )
        assert len(diff_view.query(".diff-block-anchor")) == 0


@pytest.mark.asyncio
async def test_virtualized_split_mode_groups_modified_lines_into_blocks() -> None:
    """Large split diffs should group modified lines into split block widgets."""

    patch = "@@ -1,40 +1,40 @@\n" + "\n".join(f"-old{i}\n+new{i}" for i in range(1, 41))

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view._split_blocks_by_line) == rendered_line_count

        block = diff_view._split_blocks_by_line[rendered_start]
        assert isinstance(block, SplitDiffBlock)
        assert "-" in block._left_annotations.numbers[0].plain
        assert "+" in block._right_annotations.numbers[0].plain

        left_visual = block._left_code._render()
        right_visual = block._right_code._render()
        assert isinstance(left_visual, LineContent)
        assert isinstance(right_visual, LineContent)
        left_line = left_visual.code_lines[0]
        right_line = right_visual.code_lines[0]
        assert left_line is not None
        assert right_line is not None
        assert "old" in left_line.plain
        assert "new" in right_line.plain


@pytest.mark.asyncio
async def test_grouped_block_cursor_scroll_uses_row_offsets_without_line_anchors() -> (
    None
):
    """Cursor scrolling within a grouped block should not depend on per-line anchor widgets."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 121))
    patch = f"@@ -1,120 +1,120 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 40
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert len(diff_view.query(".diff-block-anchor")) == 0
        assert diff_view.scroll_y == 0

        diff_view.cursor_line = 20
        await pilot.pause()
        await pilot.pause()

        assert diff_view.scroll_y > 0


@pytest.mark.asyncio
async def test_virtualized_auto_mode_groups_simple_lines_into_unified_blocks() -> None:
    """Large auto-mode diffs should use unified blocks when layout resolves unified."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 40)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert diff_view.split is False
        assert len(diff_view.query(".diff-block")) >= 1
        assert len(diff_view._unified_blocks_by_line) == rendered_line_count
        assert len(diff_view.query(".diff-block .code-content")) < rendered_line_count


@pytest.mark.asyncio
async def test_virtualized_auto_mode_groups_simple_lines_into_split_blocks() -> None:
    """Large auto-mode diffs should use split blocks when layout resolves split."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 41))
    patch = f"@@ -1,40 +1,40 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="auto", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        rendered_start, rendered_end = diff_view._get_rendered_line_bounds()
        rendered_line_count = rendered_end - rendered_start + 1

        assert diff_view._virt.active is True
        assert diff_view.split is True
        assert len(diff_view.query(".split-block")) >= 1
        assert len(diff_view._split_blocks_by_line) == rendered_line_count
        assert (
            len(diff_view.query(".split-block .code-content")) < rendered_line_count * 2
        )


@pytest.mark.asyncio
async def test_grouped_unified_virtual_window_shift_preserves_surviving_block_identity() -> (
    None
):
    """Small grouped unified shifts should keep unaffected middle blocks mounted."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 301))
    patch = f"@@ -1,300 +1,300 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 80
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 10

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        original_block = diff_view._unified_blocks_by_line[130]

        diff_view.cursor_line = 151
        await pilot.pause()
        await pilot.pause()

        assert diff_view._unified_blocks_by_line[130] is original_block


@pytest.mark.asyncio
async def test_grouped_split_virtual_window_shift_preserves_surviving_block_identity() -> (
    None
):
    """Small grouped split shifts should keep unaffected middle blocks mounted."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 301))
    patch = f"@@ -1,300 +1,300 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(160, 40)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 80
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 10

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        original_block = diff_view._split_blocks_by_line[130]

        diff_view.cursor_line = 151
        await pilot.pause()
        await pilot.pause()

        assert diff_view._split_blocks_by_line[130] is original_block


@pytest.mark.asyncio
async def test_grouped_unified_virtual_window_shift_avoids_full_rerender() -> None:
    """Grouped unified window shifts should avoid falling back to full `_render_diff()`."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1
        assert len(diff_view.query(".diff-block")) >= 1

        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls
        assert diff_view._virt.rendered_start == diff_view._virt.window_start


@pytest.mark.asyncio
async def test_grouped_split_virtual_window_shift_avoids_full_rerender() -> None:
    """Grouped split window shifts should avoid falling back to full `_render_diff()`."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1
        assert len(diff_view.query(".split-block")) >= 1

        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls
        assert diff_view._virt.rendered_start == diff_view._virt.window_start


@pytest.mark.asyncio
async def test_cursor_ui_flush_coalesces_multiple_requests_in_same_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cursor-side UI refresh requests should merge into one flush."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff = parse_patch(patch, "test.py")
        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await pilot.pause()

        flush_calls = {"count": 0}
        grouped_calls: list[set[int]] = []
        search_calls = {"count": 0}
        status_calls = {"count": 0}

        original_flush = diff_view._flush_queued_cursor_ui_updates
        original_grouped = _blocks_mod._refresh_grouped_blocks_for_lines
        original_search = _search_mod.sync_match_index_to_cursor
        original_status = diff_view._update_status_line

        def counted_flush() -> None:
            flush_calls["count"] += 1
            original_flush()

        def counted_grouped(view, lines: set[int]) -> bool:
            grouped_calls.append(set(lines))
            return original_grouped(view, lines)

        def counted_search(view: DiffView) -> None:
            search_calls["count"] += 1
            original_search(view)

        def counted_status() -> None:
            status_calls["count"] += 1
            original_status()

        diff_view._flush_queued_cursor_ui_updates = counted_flush  # type: ignore[method-assign]
        monkeypatch.setattr(
            _blocks_mod, "_refresh_grouped_blocks_for_lines", counted_grouped
        )
        monkeypatch.setattr(_search_mod, "sync_match_index_to_cursor", counted_search)
        diff_view._update_status_line = counted_status  # type: ignore[method-assign]

        diff_view._queue_cursor_ui_flush(
            cursor_lines={0},
            sync_search_match=True,
            update_status_line=True,
        )
        diff_view._queue_cursor_ui_flush(
            cursor_lines={1},
            sync_search_match=True,
            update_status_line=True,
        )

        assert diff_view._cursor_ui.flush_pending is True

        await pilot.pause()
        await pilot.pause()

        assert flush_calls["count"] == 1
        assert grouped_calls == [{0, 1}]
        assert search_calls["count"] == 1
        assert status_calls["count"] == 1


@pytest.mark.asyncio
async def test_half_page_scroll_flushes_cursor_ui_immediately_after_scroll_adjustment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half-page cursor scrolls should repaint immediately after viewport adjustment."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        monkeypatch.setattr(
            _hl_mod, "_queue_highlight_diff", lambda filename, diff: None
        )

        diff = parse_patch(patch, "test.py")
        await diff_view.show_diff("test.py", diff)
        await pilot.pause()
        await pilot.pause()

        assert len(diff_view.query(".diff-block")) >= 1

        diff_view.cursor_line = 60
        await pilot.pause()
        await pilot.pause()

        await diff_view.action_half_page_down()

        assert diff_view._cursor_ui.flush_pending is False
        assert diff_view._cursor_ui.dirty_lines == set()
        await pilot.pause()
        await pilot.pause()

        diff_view.cursor_line = 80
        await pilot.pause()
        await pilot.pause()

        await diff_view.action_half_page_up()

        assert diff_view._cursor_ui.flush_pending is False
        assert diff_view._cursor_ui.dirty_lines == set()


@pytest.mark.asyncio
async def test_unified_block_refresh_reuses_cached_static_row_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unified grouped blocks should reuse cached static row data on cursor refresh."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        monkeypatch.setattr(
            _hl_mod, "_queue_highlight_diff", lambda filename, diff: None
        )

        static_calls = {"count": 0}
        base_calls = {"count": 0}
        original_static = _blocks_mod._compute_unified_block_static_rows
        original_base = diff_view._compute_base_code_content

        def counted_static(view, line):
            static_calls["count"] += 1
            return original_static(view, line)

        def counted_base(line, *, side="auto", empty_fallback=""):
            base_calls["count"] += 1
            return original_base(
                line,
                side=side,
                empty_fallback=empty_fallback,
            )

        monkeypatch.setattr(
            _blocks_mod, "_compute_unified_block_static_rows", counted_static
        )
        diff_view._compute_base_code_content = counted_base  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        initial_static = static_calls["count"]
        initial_base = base_calls["count"]
        assert initial_static > 0
        assert initial_base > 0
        assert len(diff_view.query(".diff-block")) >= 1

        diff_view.cursor_line = 10
        await pilot.pause()
        await pilot.pause()
        diff_view.cursor_line = 11
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        assert static_calls["count"] == initial_static
        assert base_calls["count"] == initial_base


@pytest.mark.asyncio
async def test_split_block_refresh_reuses_cached_static_row_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Split grouped blocks should reuse cached static row data on cursor refresh."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 131))
    patch = f"@@ -1,130 +1,130 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        monkeypatch.setattr(
            _hl_mod, "_queue_highlight_diff", lambda filename, diff: None
        )

        static_calls = {"count": 0}
        base_calls = {"count": 0}
        original_static = _blocks_mod._compute_split_block_static_row
        original_base = diff_view._compute_base_code_content

        def counted_static(view, line):
            static_calls["count"] += 1
            return original_static(view, line)

        def counted_base(line, *, side="auto", empty_fallback=""):
            base_calls["count"] += 1
            return original_base(
                line,
                side=side,
                empty_fallback=empty_fallback,
            )

        monkeypatch.setattr(
            _blocks_mod, "_compute_split_block_static_row", counted_static
        )
        diff_view._compute_base_code_content = counted_base  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        initial_static = static_calls["count"]
        initial_base = base_calls["count"]
        assert initial_static > 0
        assert initial_base > 0
        assert len(diff_view.query(".split-block")) >= 1

        diff_view.cursor_line = 10
        await pilot.pause()
        await pilot.pause()
        diff_view.cursor_line = 11
        await pilot.pause()
        await pilot.pause()
        await wait_until(lambda: _diff_view_render_idle(diff_view), timeout=1.0)

        assert static_calls["count"] == initial_static
        assert base_calls["count"] == initial_base


@pytest.mark.asyncio
async def test_scroll_coalesces_pending_virtual_window_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid scroll updates should coalesce to the latest viewport target."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        calls = {"count": 0}
        started = threading.Event()
        unblock = threading.Event()
        original = _virtual_mod._render_virtual_window_and_finalize

        async def blocking_render_finalize(view) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                started.set()
                await asyncio.to_thread(unblock.wait, 1.0)
            await original(view)

        monkeypatch.setattr(
            _virtual_mod,
            "_render_virtual_window_and_finalize",
            blocking_render_finalize,
        )

        diff_view.scroll_to(y=120, animate=False)
        await pilot.pause()
        await pilot.pause()

        assert started.wait(timeout=1.0) is True

        diff_view.scroll_to(y=360, animate=False)
        await pilot.pause()
        await pilot.pause()

        queued_center = diff_view._virt.coalesced_center
        assert queued_center is not None

        unblock.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await wait_until(
            lambda: (
                not diff_view._virt.render_pending
                and diff_view._virt.rendered_start
                <= queued_center
                <= diff_view._virt.rendered_end
            ),
            timeout=2.0,
        )

        assert 2 <= calls["count"] <= 3
        assert (
            diff_view._virt.rendered_start
            <= queued_center
            <= diff_view._virt.rendered_end
        )


@pytest.mark.asyncio
async def test_cursor_coalesces_pending_virtual_window_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rapid cursor jumps should coalesce to the latest virtual-window target."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()

        calls = {"count": 0}
        started = threading.Event()
        unblock = threading.Event()
        original = _virtual_mod._render_virtual_window_and_finalize

        async def blocking_render_finalize(view) -> None:
            calls["count"] += 1
            if calls["count"] == 1:
                started.set()
                await asyncio.to_thread(unblock.wait, 1.0)
            await original(view)

        monkeypatch.setattr(
            _virtual_mod,
            "_render_virtual_window_and_finalize",
            blocking_render_finalize,
        )

        diff_view.cursor_line = 80
        await pilot.pause()
        await pilot.pause()

        assert started.wait(timeout=1.0) is True

        diff_view.cursor_line = 360
        await pilot.pause()
        await pilot.pause()

        queued_center = diff_view._virt.coalesced_center
        assert queued_center == 360

        unblock.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert calls["count"] == 2
        assert diff_view._virt.rendered_start <= 360 <= diff_view._virt.rendered_end


@pytest.mark.asyncio
async def test_medium_windowed_highlight_tracks_scroll_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Medium non-virtualized diffs should request new highlight windows after scrolling."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 221))
    patch = f"@@ -1,220 +1,220 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    range_calls = {"count": 0}
    original_range = diff_highlight_module.highlight_lines_for_diff_range

    def counted_range(*args, **kwargs):
        range_calls["count"] += 1
        return original_range(*args, **kwargs)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        diff_highlight_module, "highlight_lines_for_diff_range", counted_range
    )

    try:
        async with app.run_test(size=(100, 12)) as pilot:
            diff_view = app.query_one(DiffView)
            diff = parse_patch(patch, "medium.py")
            await diff_view.show_diff("medium.py", diff)
            await pilot.pause()
            await pilot.pause()

            initial_calls = range_calls["count"]
            assert diff_view._virt.active is False
            assert initial_calls >= 1

            diff_view.scroll_to(y=140, animate=False)
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            assert range_calls["count"] > initial_calls
    finally:
        monkeypatch.undo()


@pytest.mark.asyncio
async def test_virtualized_large_diff_preserves_scroll_height_with_spacer_buffers() -> (
    None
):
    """Large virtualized diffs should keep full scroll height via spacer buffers."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert diff_view._virt.active is True
        assert diff_view.max_scroll_y > 1000
        bottom_buffer = diff_view.query_one("#virtual-buffer-bottom", Static)
        bottom_height = getattr(bottom_buffer.styles.height, "value", 0)
        assert bottom_height > 1000


@pytest.mark.asyncio
async def test_virtualized_window_tracks_scroll_position_without_cursor_moves() -> None:
    """Viewport scrolling should shift the virtual window even when the cursor stays put."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 1501))
    patch = f"@@ -1,1500 +1,1500 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert diff_view.cursor_line == 0
        assert diff_view._virt.window_start == 0

        diff_view.scroll_to(y=120, animate=False)
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert diff_view.cursor_line == 0
        assert diff_view.scroll_y > 0
        assert diff_view._virt.window_start > 0
        top_buffer = diff_view.query_one("#virtual-buffer-top", Static)
        top_height = getattr(top_buffer.styles.height, "value", 0)
        assert top_height > 0


@pytest.mark.asyncio
async def test_virtual_window_down_shift_avoids_full_rerender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Downward virtual-window shifts should use incremental DOM updates."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1
        monkeypatch.setattr(
            _blocks_mod, "_should_use_unified_block_renderer", lambda view: False
        )

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        # Small downward shift keeps overlap with previous window.
        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()

        # No additional full rerender; only incremental shift path.
        assert render_calls["count"] == baseline_calls


@pytest.mark.asyncio
async def test_virtual_window_up_shift_avoids_full_rerender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upward virtual-window shifts should use incremental DOM updates."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 61))
    patch = f"@@ -1,60 +1,60 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1
        monkeypatch.setattr(
            _blocks_mod, "_should_use_unified_block_renderer", lambda view: False
        )

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        # First move window down with overlap.
        diff_view.cursor_line = 6
        await pilot.pause()
        await pilot.pause()
        after_down_calls = render_calls["count"]
        assert after_down_calls == baseline_calls

        # Then move up with overlap.
        diff_view.cursor_line = 2
        await pilot.pause()
        await pilot.pause()

        # Still no additional full rerender; incremental path should handle it.
        assert render_calls["count"] == after_down_calls


@pytest.mark.asyncio
async def test_grouped_virtual_large_jump_mounts_without_scanning_all_hunk_lines() -> (
    None
):
    """Grouped virtual-window jumps should mount from the visible line window."""

    hunks: list[str] = []
    for hunk_index in range(50):
        start = hunk_index * 100 + 1
        lines = "\n".join(f" line{line_no}" for line_no in range(start, start + 100))
        hunks.append(f"@@ -{start},100 +{start},100 @@\n{lines}")
    patch = "\n".join(hunks)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    class CountingLines(list):
        def __iter__(self):
            iterated["count"] += len(self)
            return super().__iter__()

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)

        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        iterated = {"count": 0}
        for hunk in diff.hunks:
            hunk.lines = CountingLines(hunk.lines)

        diff_view.cursor_line = 4000
        await wait_until(
            lambda: (
                not diff_view._virt.render_pending
                and diff_view._virt.window_start <= 4000 <= diff_view._virt.window_end
            ),
            timeout=5.0,
        )

        assert diff_view._virt.window_start <= 4000 <= diff_view._virt.window_end
        window_size = diff_view._virt.window_end - diff_view._virt.window_start + 1
        assert iterated["count"] <= window_size * 2


@pytest.mark.asyncio
async def test_grouped_virtual_large_jump_uses_full_window_rerender() -> None:
    """No-overlap grouped jumps should use the cheaper full window rerender path."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 101))
    patch = f"@@ -1,100 +1,100 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        render_calls = {"count": 0}
        original_render_diff = diff_view._render_diff

        async def counted_render_diff() -> None:
            render_calls["count"] += 1
            await original_render_diff()

        diff_view._render_diff = counted_render_diff  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        baseline_calls = render_calls["count"]
        assert baseline_calls >= 1

        diff_view.cursor_line = 80
        await pilot.pause()
        await pilot.pause()

        assert render_calls["count"] == baseline_calls + 1


@pytest.mark.asyncio
async def test_virtualized_show_diff_does_not_build_content_for_layout_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Large virtualized diffs should avoid content construction for width scans."""

    context_lines = "\n".join(f" line{i}" for i in range(1, 5001))
    patch = f"@@ -1,5000 +1,5000 @@\n{context_lines}"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="unified", id="diff-view")

    app = TestApp()
    async with app.run_test() as pilot:
        diff_view = app.query_one(DiffView)
        diff_view.VIRTUALIZE_LINE_THRESHOLD = 10
        diff_view.VIRTUAL_WINDOW_RADIUS = 3
        diff_view.VIRTUAL_WINDOW_SHIFT_MARGIN = 1

        base_calls = {"count": 0}
        original_base = diff_view._compute_base_code_content

        def counted_base(line, *, side="auto", empty_fallback=""):
            base_calls["count"] += 1
            return original_base(line, side=side, empty_fallback=empty_fallback)

        diff_view._compute_base_code_content = counted_base  # type: ignore[method-assign]

        diff = parse_patch(patch, "big.py")
        await diff_view.show_diff("big.py", diff)
        await pilot.pause()

        assert base_calls["count"] < 100
