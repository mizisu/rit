import asyncio
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult
from textual.widget import Widget

from rit.services.github import ReviewThreadInfo
from rit.state.models import PR, PRComment, PRIssueComment, PRReview, PRUser, ReviewThread
from rit.state.store import PRStore
from rit.ui.components.pr_timeline import (
    INITIAL_TIMELINE_BODY_COUNT,
    PRTimeline,
    TIMELINE_BODY_MOUNT_DELAY,
)
from rit.ui.widgets.comment_card import BODY_PREVIEW_RETIRE_DELAY
from rit.ui.components.collapsible_markdown import CopyableCodeBlock
from rit.ui.widgets.review_thread_card import ReviewThreadItem


def test_initial_timeline_selection_does_not_scroll_viewport() -> None:
    store = PRStore()
    timeline = PRTimeline(store)
    item = Widget()
    timeline._navigable_items = [item]
    timeline._navigable_items_valid = True
    timeline._current_index = -1
    calls: list[bool] = []

    def fake_update_selection(index: int, scroll_to_view: bool = True) -> None:
        calls.append(scroll_to_view)
        timeline._current_index = index

    timeline._update_selection = fake_update_selection

    timeline._select_first_item()

    assert calls == [False]


@pytest.mark.asyncio
async def test_timeline_starts_with_comment_loading_cards() -> None:
    store = PRStore()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        comments = app.query_one("#comments-container")
        loading_cards = comments.query("CommentCard.timeline-loading")
        assert len(loading_cards) >= 2


@pytest.mark.asyncio
async def test_description_starts_as_loading_card_until_summary_loads() -> None:
    store = PRStore()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        description = app.query_one("#pr-description-card")

        assert description.has_class("timeline-loading")

        timeline.refresh_description(
            PR(number=123, body="Description body", author=PRUser(login="alice"))
        )
        await pilot.pause()

        assert not description.has_class("timeline-loading")


@pytest.mark.asyncio
async def test_refresh_timeline_queues_second_refresh_without_canceling_first() -> None:
    store = PRStore()

    class SlowTimeline(PRTimeline):
        def __init__(self) -> None:
            super().__init__(store)
            self.calls = 0
            self.first_started = asyncio.Event()
            self.allow_first = asyncio.Event()
            self.second_started = asyncio.Event()

        async def _build_timeline_async(self) -> None:
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.allow_first.wait()
            else:
                self.second_started.set()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SlowTimeline()

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(SlowTimeline)

        timeline.refresh_timeline()
        await asyncio.wait_for(timeline.first_started.wait(), timeout=1)

        timeline.refresh_timeline()
        await pilot.pause(0.05)

        assert timeline.calls == 1
        assert not timeline.second_started.is_set()

        timeline.allow_first.set()
        await asyncio.wait_for(timeline.second_started.wait(), timeout=1)


@pytest.mark.asyncio
async def test_queued_refresh_waits_one_paint_after_first_refresh() -> None:
    store = PRStore()

    class SlowTimeline(PRTimeline):
        def __init__(self) -> None:
            super().__init__(store)
            self.calls = 0
            self.first_started = asyncio.Event()
            self.allow_first = asyncio.Event()
            self.second_started = asyncio.Event()

        async def _build_timeline_async(self) -> None:
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.allow_first.wait()
            else:
                self.second_started.set()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield SlowTimeline()

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(SlowTimeline)

        timeline.refresh_timeline()
        await asyncio.wait_for(timeline.first_started.wait(), timeout=1)
        timeline.refresh_timeline()

        timeline.allow_first.set()
        await pilot.pause(0)

        assert not timeline.second_started.is_set()

        await asyncio.wait_for(timeline.second_started.wait(), timeout=1)


@pytest.mark.asyncio
async def test_timeline_yields_after_first_mounted_item() -> None:
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

    class YieldingTimeline(PRTimeline):
        def __init__(self) -> None:
            super().__init__(store)
            self.mounted_count = 0
            self.first_mounted = asyncio.Event()

        def _mount_issue_comment(
            self, container, comment, *, body_mount_delay=TIMELINE_BODY_MOUNT_DELAY
        ) -> None:
            self.mounted_count += 1
            super()._mount_issue_comment(
                container,
                comment,
                body_mount_delay=body_mount_delay,
            )
            if self.mounted_count == 1:
                self.first_mounted.set()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield YieldingTimeline()

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(YieldingTimeline)

        build_task = asyncio.create_task(timeline._build_timeline_async())
        await asyncio.wait_for(timeline.first_mounted.wait(), timeout=1)

        assert timeline.mounted_count == 1

        await build_task
        await pilot.pause()

        assert timeline.mounted_count == 2


@pytest.mark.asyncio
async def test_timeline_staggers_body_mount_delay_after_initial_items() -> None:
    store = PRStore()
    store.state.issue_comments = [
        PRIssueComment(
            id=index,
            body=f"Comment {index}",
            user=PRUser(login="alice"),
            created_at=datetime(2026, 4, index, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, index, tzinfo=timezone.utc),
        )
        for index in range(1, INITIAL_TIMELINE_BODY_COUNT + 4)
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)

        await timeline._build_timeline_async()
        await pilot.pause()

        cards = list(app.query("CommentCard.comment-box"))
        delays = [card._body_mount_delay for card in cards]

        assert delays[:INITIAL_TIMELINE_BODY_COUNT] == [
            TIMELINE_BODY_MOUNT_DELAY
        ] * INITIAL_TIMELINE_BODY_COUNT
        assert delays[INITIAL_TIMELINE_BODY_COUNT] > TIMELINE_BODY_MOUNT_DELAY
        assert delays[-1] > delays[INITIAL_TIMELINE_BODY_COUNT]


def test_timeline_comment_markdown_waits_for_description_first_paint() -> None:
    assert TIMELINE_BODY_MOUNT_DELAY >= BODY_PREVIEW_RETIRE_DELAY


@pytest.mark.asyncio
async def test_issue_comment_left_aligns_markdown_h1() -> None:
    store = PRStore()
    store.state.issue_comments = [
        PRIssueComment(
            id=1,
            body="# Test",
            user=PRUser(login="alice"),
            created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        timeline.refresh_timeline()
        await pilot.pause()
        await pilot.pause(TIMELINE_BODY_MOUNT_DELAY + BODY_PREVIEW_RETIRE_DELAY + 0.1)

        h1 = app.query_one("MarkdownH1")
        assert h1.styles.content_align == ("left", "middle")


@pytest.mark.asyncio
async def test_description_and_issue_comment_use_shared_comment_cards() -> None:
    store = PRStore()
    store.state.pr = PR(
        number=123,
        body="Description body",
        author=PRUser(login="alice"),
    )
    store.state.issue_comments = [
        PRIssueComment(
            id=1,
            body="Issue comment body",
            user=PRUser(login="bob"),
            created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        timeline.refresh_description(store.state.pr)
        timeline.refresh_timeline()
        await pilot.pause()
        await pilot.pause()

        assert len(app.query("CommentCard.description-container")) == 1
        assert len(app.query("CommentCard.comment-box")) == 1


@pytest.mark.asyncio
async def test_timeline_replaces_loading_cards_when_comments_load() -> None:
    store = PRStore()
    store.state.issue_comments = [
        PRIssueComment(
            id=1,
            body="Issue comment body",
            user=PRUser(login="bob"),
            created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        timeline.refresh_timeline()
        await pilot.pause()
        await pilot.pause()

        comments = app.query_one("#comments-container")
        assert len(comments.query("CommentCard.timeline-loading")) == 0
        assert len(app.query("CommentCard.comment-box")) == 1


@pytest.mark.asyncio
async def test_timeline_awaits_loading_card_removal_before_mounting_comments() -> None:
    store = PRStore()
    store.state.issue_comments = [
        PRIssueComment(
            id=1,
            body="Issue comment body",
            user=PRUser(login="bob"),
            created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)

        await timeline._build_timeline_async()

        comments = app.query_one("#comments-container")
        assert len(comments.query("CommentCard.timeline-loading")) == 0
        assert len(app.query("CommentCard.comment-box")) == 1


@pytest.mark.asyncio
async def test_pr_description_code_block_can_copy_raw_code() -> None:
    store = PRStore()
    store.state.pr = PR(
        number=123,
        body='''Description.

```bash
uv run pytest
```
''',
        author=PRUser(login="alice"),
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        timeline.refresh_description(store.state.pr)
        await pilot.pause()

        code_block = app.query_one(CopyableCodeBlock)
        code_block.copy_code()
        await pilot.pause()

        assert app.clipboard == "uv run pytest"


@pytest.mark.asyncio
async def test_timeline_sorts_pending_review_with_aware_comment_dates() -> None:
    store = PRStore()
    store.state.issue_comments = [
        PRIssueComment(
            id=1,
            body="Issue comment",
            user=PRUser(login="alice"),
            created_at=datetime(2026, 5, 19, 2, 20, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 19, 2, 20, tzinfo=timezone.utc),
        )
    ]
    store.state.reviews = [
        PRReview(
            id=10,
            body="",
            user=PRUser(login="bob"),
            submitted_at=None,
        )
    ]
    store.state.comments = [
        PRComment(
            id=100,
            body="Inline thread",
            user=PRUser(login="bob"),
            path="app.py",
            line=12,
            created_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
            pull_request_review_id=10,
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)

        await timeline._build_timeline_async()
        await pilot.pause()

        assert app.query_one("ReviewThreadItem") is not None


@pytest.mark.asyncio
async def test_timeline_refresh_thread_metadata_updates_existing_thread_card() -> None:
    store = PRStore()
    comment = PRComment(
        id=100,
        body="Inline thread",
        user=PRUser(login="alice"),
        path="app.py",
        line=12,
        side="RIGHT",
        created_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
    )
    store.state.comments = [comment]
    store.state.review_threads = [
        ReviewThread.model_validate(
            {
                "id": "",
                "isResolved": False,
                "path": "app.py",
                "line": 12,
                "comments": {"nodes": [comment]},
            }
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)

        await timeline._build_timeline_async()
        await pilot.pause()

        thread_item = app.query_one(ReviewThreadItem)
        assert thread_item.is_resolved is False
        scroll_container = SimpleNamespace(
            scroll_offset=SimpleNamespace(y=25),
            scrolled_home=False,
        )

        def scroll_home(*, animate: bool) -> None:
            scroll_container.scrolled_home = True

        scroll_container.scroll_home = scroll_home
        timeline._scroll_container = scroll_container

        store.state.thread_info_cache[100] = ReviewThreadInfo(
            thread_id="thread-100",
            is_resolved=True,
            path="app.py",
            line=12,
            root_comment_id=100,
        )

        timeline.refresh_thread_metadata()
        await pilot.pause()

        assert thread_item.is_resolved is True
        assert thread_item.collapsed is True
        assert timeline._thread_widget_info[thread_item] == ("thread-100", 100, True)
        assert scroll_container.scrolled_home is True


@pytest.mark.asyncio
async def test_timeline_sorts_review_threads_with_missing_and_aware_dates() -> None:
    store = PRStore()
    store.state.reviews = [
        PRReview(
            id=10,
            body="",
            user=PRUser(login="bob"),
            submitted_at=datetime(2026, 5, 19, 2, 30, tzinfo=timezone.utc),
        )
    ]
    store.state.comments = [
        PRComment(
            id=100,
            body="Aware thread",
            user=PRUser(login="bob"),
            path="app.py",
            line=12,
            created_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
            pull_request_review_id=10,
        ),
        PRComment(
            id=101,
            body="Missing date thread",
            user=PRUser(login="bob"),
            path="app.py",
            line=13,
            pull_request_review_id=10,
        ),
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)

        await timeline._build_timeline_async()
        await pilot.pause()

        assert len(app.query("ReviewThreadItem")) == 2


@pytest.mark.asyncio
async def test_pr_timeline_thread_uses_single_path_header() -> None:
    store = PRStore()
    store.state.comments = [
        PRComment(
            id=100,
            body="Inline thread",
            user=PRUser(login="bob"),
            path="app.py",
            line=12,
            created_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 19, 2, 27, tzinfo=timezone.utc),
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)

        await timeline._build_timeline_async()
        await pilot.pause()

        assert len(app.query("ReviewThreadItem .thread-header")) == 0
