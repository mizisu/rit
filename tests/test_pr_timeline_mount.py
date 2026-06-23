import pytest
from textual.css.query import NoMatches
from textual.geometry import Region
from textual.message_pump import NoActiveAppError
from textual.widget import Widget

from rit.state.store import PRStore
from rit.ui.components.pr_timeline import PRTimeline


class ScrollVector:
    def __init__(self, *, y: int = 0, height: int = 0) -> None:
        self.y = y
        self.height = height


class SimpleScrollContainer:
    scroll_offset = ScrollVector(y=0)
    size = ScrollVector(height=10)


def test_timeline_mount_ignores_missing_scroll_ancestor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())

    def missing_ancestor(*_args: object, **_kwargs: object) -> object:
        raise NoMatches("missing")

    monkeypatch.setattr(timeline, "query_ancestor", missing_ancestor)
    monkeypatch.setattr(timeline, "call_after_refresh", lambda *_args: None)

    timeline.on_mount()

    assert timeline._scroll_container is None


def test_timeline_mount_reraises_unexpected_ancestor_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())

    def fail_ancestor(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("ancestor lookup failed")

    monkeypatch.setattr(timeline, "query_ancestor", fail_ancestor)
    monkeypatch.setattr(timeline, "call_after_refresh", lambda *_args: None)

    with pytest.raises(RuntimeError, match="ancestor lookup failed"):
        timeline.on_mount()


def test_timeline_is_scroll_at_top_reraises_unexpected_offset_errors() -> None:
    timeline = PRTimeline(PRStore())

    class BrokenScrollContainer:
        @property
        def scroll_offset(self) -> object:
            raise RuntimeError("scroll offset failed")

    timeline._scroll_container = BrokenScrollContainer()

    with pytest.raises(RuntimeError, match="scroll offset failed"):
        timeline._is_scroll_at_top()


def test_timeline_restore_scroll_home_ignores_missing_active_app() -> None:
    timeline = PRTimeline(PRStore())

    class UnmountedScrollContainer:
        def scroll_home(self, *, animate: bool) -> None:
            raise NoActiveAppError()

    timeline._scroll_container = UnmountedScrollContainer()

    timeline._restore_scroll_home()


def test_timeline_restore_scroll_home_reraises_unexpected_scroll_errors() -> None:
    timeline = PRTimeline(PRStore())

    class BrokenScrollContainer:
        def scroll_home(self, *, animate: bool) -> None:
            raise RuntimeError("scroll home failed")

    timeline._scroll_container = BrokenScrollContainer()

    with pytest.raises(RuntimeError, match="scroll home failed"):
        timeline._restore_scroll_home()


def test_timeline_update_selection_ignores_missing_active_app_while_scrolling() -> None:
    timeline = PRTimeline(PRStore())
    item = Widget()
    timeline._navigable_items = [item]
    timeline._current_index = -1

    class UnmountedScrollContainer:
        def scroll_to_widget(self, _widget: Widget, *, animate: bool) -> None:
            raise NoActiveAppError()

    timeline._scroll_container = UnmountedScrollContainer()

    timeline._update_selection(0)

    assert timeline._current_index == 0
    assert item.has_class("--selected")


def test_timeline_update_selection_reraises_unexpected_scroll_errors() -> None:
    timeline = PRTimeline(PRStore())
    item = Widget()
    timeline._navigable_items = [item]
    timeline._current_index = -1

    class BrokenScrollContainer:
        def scroll_to_widget(self, _widget: Widget, *, animate: bool) -> None:
            raise RuntimeError("scroll to widget failed")

    timeline._scroll_container = BrokenScrollContainer()

    with pytest.raises(RuntimeError, match="scroll to widget failed"):
        timeline._update_selection(0)


def test_timeline_collect_navigable_items_reraises_unexpected_query_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())

    def fail_query(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("timeline query failed")

    monkeypatch.setattr(timeline, "query", fail_query)

    with pytest.raises(RuntimeError, match="timeline query failed"):
        timeline._collect_navigable_items()


def test_timeline_collect_navigable_items_reraises_unexpected_region_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())

    class BrokenRegionWidget(Widget):
        @property
        def region(self) -> Region:
            raise RuntimeError("region failed")

    monkeypatch.setattr(timeline, "query", lambda *_args: [BrokenRegionWidget()])

    with pytest.raises(RuntimeError, match="region failed"):
        timeline._collect_navigable_items()


def test_timeline_select_first_visible_item_falls_back_without_active_app() -> None:
    timeline = PRTimeline(PRStore())
    item = Widget()
    timeline._navigable_items = [item]
    timeline._navigable_items_valid = True
    timeline._current_index = -1

    class UnmountedScrollContainer:
        @property
        def scroll_offset(self) -> object:
            raise NoActiveAppError()

    timeline._scroll_container = UnmountedScrollContainer()

    timeline.select_first_visible_item()

    assert timeline._current_index == 0


def test_timeline_select_first_visible_item_reraises_unexpected_scroll_errors() -> None:
    timeline = PRTimeline(PRStore())
    item = Widget()
    timeline._navigable_items = [item]
    timeline._navigable_items_valid = True

    class BrokenScrollContainer:
        @property
        def scroll_offset(self) -> object:
            raise RuntimeError("scroll offset failed")

    timeline._scroll_container = BrokenScrollContainer()

    with pytest.raises(RuntimeError, match="scroll offset failed"):
        timeline.select_first_visible_item()


def test_timeline_select_first_visible_item_reraises_unexpected_region_errors() -> None:
    timeline = PRTimeline(PRStore())

    class BrokenRegionWidget(Widget):
        @property
        def region(self) -> Region:
            raise RuntimeError("region failed")

    timeline._navigable_items = [BrokenRegionWidget()]
    timeline._navigable_items_valid = True
    timeline._scroll_container = SimpleScrollContainer()

    with pytest.raises(RuntimeError, match="region failed"):
        timeline.select_first_visible_item()


@pytest.mark.asyncio
async def test_timeline_toggle_resolve_reraises_unexpected_success_message_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[bool] = []

    class Logger:
        def error(self, _message: str) -> None:
            pass

    class TestTimeline(PRTimeline):
        @property
        def log(self) -> Logger:
            return Logger()

    class Store:
        async def resolve_thread(self, thread_id: str, root_id: int) -> bool:
            return True

    def post_message(_message: object) -> None:
        raise RuntimeError("message dispatch failed")

    timeline = TestTimeline(PRStore())
    timeline.store = Store()
    monkeypatch.setattr(
        timeline,
        "get_current_thread_info",
        lambda: ("thread-1", 100, False),
    )
    monkeypatch.setattr(
        timeline,
        "_update_thread_resolved_ui",
        lambda _thread_id, _root_id, is_resolved: updates.append(is_resolved),
    )
    monkeypatch.setattr(timeline, "post_message", post_message)

    with pytest.raises(RuntimeError, match="message dispatch failed"):
        await timeline.toggle_resolve()

    assert updates == [True]
    assert timeline._pending_resolve_threads == set()
