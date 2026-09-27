"""Fairness and input preservation for virtual viewport transactions."""

import asyncio
from typing import Literal

import pytest
from rich.console import RenderableType
from textual import events
from textual.app import App, ComposeResult
from textual.screen import ModalScreen, Screen
from textual.worker import get_current_worker

from rit.core.diff import parse_patch
from rit.ui.widgets import diff_virtual
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


@pytest.mark.parametrize(
    "frames,allowed", [(0, True), (1, True), (2, False), (8, False)]
)
def test_ready_window_replacement_stops_at_frame_deadline(
    frames: int, allowed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = DiffView()
    state = view._virt
    state.render_pending = True
    state.viewport_scroll_latched = True
    state.viewport_frame_token = view._render_request_token
    monkeypatch.setattr(
        diff_virtual, "monotonic", lambda: frames / diff_virtual.MAX_FPS
    )

    assert diff_virtual._replace_viewport_before_frame(view) is allowed
    assert state.render_pending is not allowed
    assert state.viewport_scroll_latched is not allowed
    assert (state.viewport_frame_token is None) is allowed


def test_stale_window_cannot_release_new_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = DiffView()
    state = view._virt
    state.render_pending = True
    state.viewport_scroll_latched = True
    state.viewport_frame_token = view._render_request_token - 1
    monkeypatch.setattr(diff_virtual, "monotonic", lambda: 0.0)

    assert not diff_virtual._replace_viewport_before_frame(view)
    diff_virtual._resume_viewport_scroll(view, state, state.viewport_frame_token)
    assert state.render_pending


@pytest.mark.asyncio
@pytest.mark.parametrize("input_kind", ["wheel", "drag"])
async def test_continuous_native_input_paints_before_producer_stops(
    input_kind: Literal["wheel", "drag"], monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = 0
    active = False
    painted: list[int] = []

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode="split")

        def _display(self, screen: Screen, renderable: RenderableType | None) -> None:
            super()._display(screen, renderable)
            if active and renderable is not None and not self._batch_count:
                view = self.query_one(DiffView)
                first, last = diff_virtual._viewport_line_range(view)
                assert (
                    view._virt.rendered_start
                    <= first
                    <= last
                    <= view._virt.rendered_end
                )
                painted.append(prepared)

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        view = app.query_one(DiffView)
        patch = "@@ -1,1000 +1,1000 @@\n" + "\n".join(
            f" line{number}" for number in range(1, 1001)
        )
        await view.show_diff("test.py", parse_patch(patch, "test.py"))
        await pilot.pause()
        bar = view.vertical_scrollbar
        positions: tuple[int, ...] = ()
        if input_kind == "drag":
            assert await pilot.mouse_down(bar, offset=(bar.region.width - 1, 0))
            assert bar.grabbed is not None and app.mouse_captured is bar
            origin, origin_y = bar.grabbed, bar.grabbed_position
            positions = (
                bar.window_size - 2,
                2,
                bar.window_size - 3,
                3,
                bar.window_size - 1,
                1,
            )
            targets = [
                round(
                    min(
                        view.max_scroll_y,
                        max(
                            0,
                            origin_y
                            + (bar.region.y + position - origin.y)
                            * bar.window_virtual_size
                            / bar.window_size,
                        ),
                    )
                )
                for position in positions
            ]
            x, y = bar.region.right - 1, origin.y
        else:
            view.scroll_to(y=300, animate=False, immediate=True)
            await wait_until(lambda: not view._virt.render_pending)
            await pilot.pause()
            app.scroll_sensitivity_y = diff_virtual._effective_virtual_window_radius(
                view
            )
            targets = [300 + index * app.scroll_sensitivity_y for index in range(1, 7)]
            region = view.scrollable_content_region
            x, y = region.x + 20, region.y + 3

        next_input = 1
        clock = diff_virtual.monotonic()
        monkeypatch.setattr(diff_virtual, "monotonic", lambda: clock)
        original_mounts = view._await_content_mounts

        def send(index: int) -> None:
            nonlocal y
            if input_kind == "drag":
                next_y = bar.region.y + positions[index]
                app.post_message(
                    events.MouseMove(
                        None, x, next_y, 0, next_y - y, 1, False, False, False
                    )
                )
                y = next_y
            else:
                app.post_message(
                    events.MouseScrollDown(None, x, y, 0, 0, 0, False, False, False)
                )

        async def mounts_with_input() -> None:
            nonlocal prepared, next_input, clock
            await original_mounts()
            prepared += 1
            clock += 1
            if next_input < len(targets):
                index = next_input
                next_input += 1
                send(index)
                await wait_until(
                    lambda: view.scroll_target_y == targets[index], timeout=5
                )

        monkeypatch.setattr(view, "_await_content_mounts", mounts_with_input)
        active = True
        send(0)
        await wait_until(
            lambda: (
                next_input == len(targets)
                and view.scroll_y == targets[-1]
                and not view._virt.render_pending
            ),
            timeout=5,
        )
        await wait_until(lambda: prepared in painted, timeout=5)
        assert prepared == len(targets)
        assert set(range(1, len(targets) + 1)) <= set(painted), painted
        assert view.scroll_target_y == targets[-1]
        assert view.cursor_line == 0
        if input_kind == "drag":
            app.post_message(events.MouseUp(None, x, y, 0, 0, 1, False, False, False))
            await wait_until(lambda: app.mouse_captured is None)

        region = view.scrollable_content_region
        expected_line = view._line_index_at_vertical_offset(int(view.scroll_y) + 3)
        x, y = region.x + 20, region.y + 3
        target, _ = app.screen.get_widget_at(x, y)
        await pilot.click(target, offset=(x - target.region.x, y - target.region.y))
        assert view.cursor_line == expected_line


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unified", "split"])
async def test_held_half_page_keys_paint_covered_viewports(
    mode: Literal["unified", "split"],
) -> None:
    observing = False
    painted: list[int] = []

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView(mode=mode)

        def _display(self, screen: Screen, renderable: RenderableType | None) -> None:
            super()._display(screen, renderable)
            if observing and renderable is not None and not self._batch_count:
                view = self.query_one(DiffView)
                first, last = diff_virtual._viewport_line_range(view)
                assert (
                    view._virt.rendered_start
                    <= first
                    <= last
                    <= view._virt.rendered_end
                )
                painted.append(view.cursor_line)

    loop = asyncio.get_running_loop()
    factory = loop.get_task_factory()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        app = TestApp()
        async with app.run_test(size=(216, 67)) as pilot:
            view = app.query_one(DiffView)
            patch = "@@ -1,801 +1,801 @@\n" + "\n".join(
                f" line{number}" for number in range(1, 802)
            )
            await view.show_diff("test.py", parse_patch(patch, "test.py"))
            view.focus()
            await pilot.pause()
            assert view._virt.active
            observing = True
            step = view._half_page_step()
            target = 0
            for key, character, direction in (
                ("ctrl+d", "\x04", 1),
                ("ctrl+u", "\x15", -1),
            ):
                for _ in range(5):
                    previous_paints = len(painted)
                    target += 4 * step * direction
                    for _ in range(4):
                        app.post_message(events.Key(key, character))
                    await wait_until(
                        lambda target=target, previous_paints=previous_paints: (
                            view.cursor_line == target
                            and not view._virt.render_pending
                            and target in painted[previous_paints:]
                        ),
                        timeout=5,
                    )
                    row = view._current_row()
                    assert row is not None
                    assert view._row_is_visible(row), (
                        view.cursor_line,
                        view.scroll_y,
                        view._row_vertical_bounds(row),
                        diff_virtual._viewport_line_range(view),
                        (view._virt.rendered_start, view._virt.rendered_end),
                    )
            assert view.cursor_line == 0
            observing = False
    finally:
        loop.set_task_factory(factory)


@pytest.mark.asyncio
async def test_eager_worker_owns_window_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield DiffView()

    async def complete(view: DiffView, request_token: int) -> None:
        assert view._virt.viewport_worker is get_current_worker()
        assert request_token == view._render_request_token

    app = TestApp()
    async with app.run_test():
        view = app.query_one(DiffView)
        screen = view.screen
        await app.push_screen(ModalScreen())
        assert screen.is_current and app.screen is not screen
        state = view._virt
        state.render_pending = True
        monkeypatch.setattr(
            diff_virtual, "_run_virtual_window_render_for_request", complete
        )
        loop = asyncio.get_running_loop()
        factory = loop.get_task_factory()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            diff_virtual._queue_viewport_render(view)
            worker = state.viewport_worker
            assert worker is not None and worker.is_finished
            assert worker.error is None
            assert state.viewport_frame_token == view._render_request_token
            with app.batch_update():
                diff_virtual._viewport_frame_painted(view, screen)
                assert state.render_pending
                assert state.viewport_frame_token == view._render_request_token
            diff_virtual._viewport_frame_painted(view, screen)
            assert state.render_pending
            assert state.viewport_frame_token == view._render_request_token
            await app.pop_screen()
            await wait_until(lambda: not state.render_pending)
            assert state.viewport_frame_token is None
            assert app._batch_count == 0
        finally:
            loop.set_task_factory(factory)
