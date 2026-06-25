from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.geometry import Region
from textual.message_pump import NoActiveAppError
from textual.widget import Widget

from rit.state.models import (
    PR,
    PRIssueComment,
    PRReview,
    PRUser,
    ReviewState,
    ReviewThreadInfo,
)
from rit.state.store import PRStore
import rit.ui.components.pr_timeline as pr_timeline_module
from rit.ui.components.pr_timeline import (
    INITIAL_TIMELINE_BODY_COUNT,
    PRTimeline,
    TIMELINE_BODY_MOUNT_DELAY,
)


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


def test_mount_review_with_threads_uses_projection_order_without_resorting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())
    mounted_threads: list[object] = []

    class Thread:
        @property
        def created_at(self) -> object:
            raise AssertionError("timeline mount should use projection thread order")

    first = Thread()
    second = Thread()

    class OrderedThreads:
        def __iter__(self):
            return iter((first, second))

    monkeypatch.setattr(
        timeline,
        "_mount_comment_thread",
        lambda _container, thread, **_kwargs: mounted_threads.append(thread),
    )

    timeline._mount_review_with_threads(
        object(),
        PRReview(state=ReviewState.COMMENTED, body=""),
        OrderedThreads(),
        body_mount_delay=0,
    )

    assert mounted_threads == [first, second]


def test_timeline_refresh_description_reuses_description_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(
        title="Fast path",
        body="Body",
        user=PRUser(login="mona"),
        html_url="https://github.com/owner/repo/pull/1",
    )
    timeline = PRTimeline(store)
    query_count = 0

    class DescriptionCard:
        updates = 0

        def remove_class(self, *_args: object) -> None:
            pass

        def set_content(self, *_args: object, **_kwargs: object) -> None:
            self.updates += 1

    card = DescriptionCard()

    def query_one(selector: object, *_args: object, **_kwargs: object) -> object:
        nonlocal query_count
        assert selector == "#pr-description-card"
        query_count += 1
        return card

    monkeypatch.setattr(timeline, "query_one", query_one)

    timeline.refresh_description(store.state.pr)
    timeline.refresh_description(store.state.pr)

    assert query_count == 1
    assert card.updates == 2


def test_timeline_refresh_thread_metadata_does_not_copy_thread_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())
    widget = Widget()

    class NoListItems:
        def __init__(self, items: object) -> None:
            self._items = items

        def __iter__(self):
            return iter(self._items)

        def __len__(self) -> int:
            raise AssertionError("thread metadata refresh should not copy items")

    class NoListThreadInfo(dict[Widget, tuple[str, int, bool]]):
        def items(self):
            return NoListItems(super().items())

    timeline._thread_widget_info = NoListThreadInfo({widget: ("old-thread", 100, False)})

    def get_thread_info(root_comment_id: int) -> ReviewThreadInfo:
        assert root_comment_id == 100
        return ReviewThreadInfo(
            thread_id="new-thread",
            is_resolved=True,
            path="file.py",
            line=12,
            root_comment_id=100,
        )

    monkeypatch.setattr(timeline.store, "get_thread_info", get_thread_info)

    timeline.refresh_thread_metadata()

    assert timeline._thread_widget_info[widget] == ("new-thread", 100, True)


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


def test_timeline_collect_navigable_items_does_not_copy_query_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())

    class PositionedWidget(Widget):
        @property
        def region(self) -> Region:
            return Region(0, 3, 10, 1)

    class NoListQueryResult:
        def __iter__(self):
            return iter([PositionedWidget()])

        def __len__(self) -> int:
            raise AssertionError("timeline navigation should not copy query results")

    monkeypatch.setattr(timeline, "query", lambda *_args: NoListQueryResult())

    timeline._collect_navigable_items()

    assert len(timeline._navigable_items) == 1
    assert timeline._navigable_items_valid is True


def test_timeline_collect_navigable_items_single_query_result_skips_sort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())

    class PositionedWidget(Widget):
        @property
        def region(self) -> Region:
            return Region(0, 3, 10, 1)

    monkeypatch.setattr(timeline, "query", lambda *_args: [PositionedWidget()])
    monkeypatch.setattr(
        pr_timeline_module,
        "sorted",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single fallback item should not sort")
        ),
        raising=False,
    )

    timeline._collect_navigable_items()

    assert len(timeline._navigable_items) == 1
    assert timeline._navigable_items_valid is True


def test_timeline_collect_navigable_items_finds_current_widget_in_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())
    first = Widget()
    current = Widget()
    timeline._navigable_items = [current]
    timeline._current_index = 0

    class NoMembershipCachedItems(list[Widget]):
        def __contains__(self, _item: object) -> bool:
            raise AssertionError("navigation cache should not scan with `in`")

        def index(self, _item: object, *_args: object) -> int:
            raise AssertionError("navigation cache should not rescan with index()")

    monkeypatch.setattr(
        timeline,
        "_cached_navigable_items",
        lambda: NoMembershipCachedItems([first, current]),
    )
    monkeypatch.setattr(
        timeline,
        "query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached navigable items should not query")
        ),
    )

    timeline._collect_navigable_items()

    assert timeline._current_index == 1
    assert timeline._navigable_items_valid is True


def test_timeline_collect_navigable_items_keeps_current_index_without_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeline = PRTimeline(PRStore())
    first = Widget()
    current = Widget()
    third = Widget()
    cached_items = [first, current, third]
    timeline._navigable_items = [first, current, third]
    timeline._current_index = 1

    monkeypatch.setattr(timeline, "_cached_navigable_items", lambda: cached_items)
    monkeypatch.setattr(
        timeline,
        "query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cached navigable items should not query")
        ),
    )
    monkeypatch.setattr(
        pr_timeline_module,
        "enumerate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unchanged current index should not rescan items")
        ),
        raising=False,
    )

    timeline._collect_navigable_items()

    assert timeline._navigable_items is cached_items
    assert timeline._current_index == 1
    assert timeline._navigable_items_valid is True


@pytest.mark.asyncio
async def test_timeline_collect_navigable_items_uses_mounted_order_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.issue_comments = [
        PRIssueComment(
            id=1,
            body="First",
            user=PRUser(login="alice"),
            created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        ),
        PRIssueComment(
            id=2,
            body="Second",
            user=PRUser(login="bob"),
            created_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        ),
    ]

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        await timeline._build_timeline_async()
        await pilot.pause()

        def fail_query(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("mounted timeline navigation should not query")

        monkeypatch.setattr(timeline, "query", fail_query)
        timeline._navigable_items = []
        timeline._navigable_items_valid = False

        timeline._collect_navigable_items()

        assert len(timeline._navigable_items) == 3


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


def test_timeline_body_mount_delay_is_capped_for_large_discussions() -> None:
    timeline = PRTimeline(PRStore())

    assert hasattr(pr_timeline_module, "TIMELINE_BODY_MOUNT_MAX_DELAY")
    max_delay = pr_timeline_module.TIMELINE_BODY_MOUNT_MAX_DELAY

    assert timeline._body_mount_delay_for_index(INITIAL_TIMELINE_BODY_COUNT) > (
        TIMELINE_BODY_MOUNT_DELAY
    )
    assert (
        timeline._body_mount_delay_for_index(INITIAL_TIMELINE_BODY_COUNT + 200)
        == max_delay
    )


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
