from __future__ import annotations

from collections.abc import Callable

import pytest

from rit.core.diff import parse_patch
from rit.ui.widgets import diff_virtual
from rit.ui.widgets import diff_comments
from rit.ui.widgets.diff_plan import build_diff_plan
from rit.ui.widgets.diff_types import VirtualState


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
        if isinstance(index, slice):
            raise AssertionError("virtual grouping should not copy line slices")
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
        self.revealed = False

    def _is_current_render_request(self, request_token: int) -> bool:
        return request_token == self._render_request_token

    def call_after_refresh(self, callback: Callable[[], None]) -> None:
        self.refresh_callbacks.append(callback)


class HeaderWidget:
    def __init__(self) -> None:
        self.removed = False

    async def remove(self) -> None:
        self.removed = True


def test_iter_virtualized_line_groups_does_not_copy_line_window() -> None:
    view = VirtualLineGroupView()

    groups = list(diff_virtual._iter_virtualized_line_groups(view, 1, 2))

    assert all(not isinstance(group, list) for group in groups)
    assert [[line.line_index for line in group] for group in groups] == [[1], [2]]


def test_mount_virtualized_ranges_at_bottom_uses_sorted_repair_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RangeRecord:
        def __init__(self, start: int, end: int) -> None:
            self.start = start
            self.end = end

        def __iter__(self):
            return iter((self.start, self.end))

        def __lt__(self, _other: object) -> bool:
            raise AssertionError("bottom repair ranges should not be resorted")

    mounted: list[tuple[int, int]] = []
    monkeypatch.setattr(
        diff_virtual,
        "_mount_virtualized_lines_at_bottom",
        lambda _view, _container, start, end: mounted.append((start, end)),
    )

    diff_virtual._mount_virtualized_ranges_at_bottom(
        object(),
        object(),
        [RangeRecord(1, 2), RangeRecord(5, 7)],
    )

    assert mounted == [(1, 2), (5, 7)]


def test_mount_virtualized_ranges_at_top_uses_reverse_repair_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RangeRecord:
        def __init__(self, start: int, end: int) -> None:
            self.start = start
            self.end = end

        def __iter__(self):
            return iter((self.start, self.end))

        def __lt__(self, _other: object) -> bool:
            raise AssertionError("top repair ranges should not be resorted")

    mounted: list[tuple[int, int]] = []
    monkeypatch.setattr(
        diff_virtual,
        "_mount_virtualized_lines_at_top",
        lambda _view, _container, start, end: mounted.append((start, end)),
    )

    diff_virtual._mount_virtualized_ranges_at_top(
        object(),
        object(),
        [RangeRecord(1, 2), RangeRecord(5, 7)],
    )

    assert mounted == [(5, 7), (1, 2)]


def test_extra_heights_single_entries_skip_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    draft = object()
    thread = object()

    class View:
        _pending_comment_drafts_by_line = {3: [draft]}
        _comment_threads_by_line = {3: [thread]}

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
async def test_clear_virtual_hunk_headers_does_not_copy_header_items() -> None:
    class NoListItems:
        def __init__(self, items: object) -> None:
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
async def test_remove_stale_virtual_hunk_headers_does_not_copy_all_header_keys() -> None:
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


@pytest.mark.asyncio
async def test_cursor_driven_virtual_render_stays_pending_until_revealed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = CursorDrivenVirtualRenderView()

    async def shifted(_view: CursorDrivenVirtualRenderView) -> bool:
        return True

    def revealed(_view: CursorDrivenVirtualRenderView, _request_token: int) -> None:
        _view.revealed = True

    monkeypatch.setattr(diff_virtual, "_try_shift_virtual_window_incremental", shifted)
    monkeypatch.setattr(diff_virtual, "_reveal_cursor_after_virtual_render", revealed)

    await diff_virtual._render_virtual_window_and_finalize(view)

    assert view._virt.render_pending is True
    assert view.refresh_callbacks
    assert view.revealed is False

    view.refresh_callbacks.pop()()

    assert view.revealed is True
    assert view._virt.render_pending is False
