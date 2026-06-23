from __future__ import annotations

from collections.abc import Callable

import pytest

from rit.ui.widgets import diff_virtual
from rit.ui.widgets.diff_types import VirtualState


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
