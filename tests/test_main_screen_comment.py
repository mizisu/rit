import asyncio
from contextlib import suppress

import pytest
from textual.widgets import TextArea

from rit.app import RitApp
from rit.core.diff import parse_patch
from rit.services.github import ReviewThreadInfo
from rit.state.models import (
    NodeList,
    PR,
    PRComment,
    PRFile,
    PRReview,
    ReviewState,
    ReviewThread,
)
from rit.ui.screens.main import MainScreen


class BlockingPendingReviewService:
    def __init__(self) -> None:
        self.create_started = asyncio.Event()
        self.allow_create = asyncio.Event()

    async def create_pending_review(
        self,
        pr_number: int,
        *,
        comments,
        body=None,
        commit_id=None,
    ) -> PRReview:
        self.create_started.set()
        await self.allow_create.wait()
        return PRReview(id=100, state=ReviewState.PENDING, body=body or "")

    async def delete_pending_review(self, pr_number: int, review_id: int) -> None:
        return None


@pytest.mark.asyncio
async def test_main_screen_comment_action_opens_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_load(self: MainScreen) -> None:
        return None

    monkeypatch.setattr(MainScreen, "_load_data", noop_load)

    class TestApp(RitApp):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(pr_number=1))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.store.state.pr = PR(number=1, title="Test PR")
        screen.action_comment()
        await pilot.pause()

        editor = screen.query_one("#issue-comment-editor")
        assert editor.has_class("-hidden") is False
        assert isinstance(app.focused, TextArea)


@pytest.mark.asyncio
async def test_main_screen_review_action_prefills_pending_review_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_load(self: MainScreen) -> None:
        return None

    monkeypatch.setattr(MainScreen, "_load_data", noop_load)

    class TestApp(RitApp):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(pr_number=1))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.store.state.pr = PR(number=1, title="Test PR")
        screen.store.state.pending_review_body = "saved summary"
        screen.action_review()
        await pilot.pause()

        modal = app.screen
        textarea = modal.query_one("#review-submit-body", TextArea)

        assert textarea.text == "saved summary"


@pytest.mark.asyncio
async def test_main_screen_review_action_opens_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_load(self: MainScreen) -> None:
        return None

    monkeypatch.setattr(MainScreen, "_load_data", noop_load)

    class TestApp(RitApp):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(pr_number=1))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.store.state.pr = PR(number=1, title="Test PR")
        screen.action_review()
        await pilot.pause()

        assert app.screen.__class__.__name__ == "ReviewSubmitScreen"


@pytest.mark.asyncio
async def test_main_screen_review_action_opens_modal_from_files_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_load(self: MainScreen) -> None:
        return None

    monkeypatch.setattr(MainScreen, "_load_data", noop_load)

    class TestApp(RitApp):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(pr_number=1))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.store.state.pr = PR(number=1, title="Test PR")
        screen.switch_tab(1)
        await pilot.pause()

        screen.action_review()
        await pilot.pause()

        assert app.screen.__class__.__name__ == "ReviewSubmitScreen"


@pytest.mark.asyncio
async def test_main_screen_file_comment_action_opens_compose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_load(self: MainScreen) -> None:
        return None

    monkeypatch.setattr(MainScreen, "_load_data", noop_load)

    class TestApp(RitApp):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(pr_number=1))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        screen.store.state.pr = PR(number=1, title="Test PR")
        diff = parse_patch("@@ -1,1 +1,1 @@\n-old\n+new", "test.py")
        await screen.file_changes.diff_view.show_diff("test.py", diff)
        screen.switch_tab(1)
        await pilot.pause()

        screen.action_comment()
        await pilot.pause()
        await pilot.pause()

        editor = screen.query_one("#diff-inline-comment-editor")
        assert editor.has_class("-hidden") is False
        assert isinstance(app.focused, TextArea)


@pytest.mark.asyncio
async def test_save_inline_comment_draft_renders_pending_card_before_sync_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_load(self: MainScreen) -> None:
        return None

    monkeypatch.setattr(MainScreen, "_load_data", noop_load)

    class TestApp(RitApp):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(pr_number=1))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        service = BlockingPendingReviewService()
        screen.store._service = service  # type: ignore[assignment]
        screen.store.state.pr = PR(number=1, title="Test PR", head_sha="deadbeef")
        patch = "@@ -1,1 +1,1 @@\n-old\n+new"
        diff = parse_patch(patch, "test.py")
        screen.store.state.files = [
            PRFile(filename="test.py", status="modified", patch=patch)
        ]
        screen.store.state.file_diffs = {"test.py": diff}
        await screen.file_changes.diff_view.show_diff("test.py", diff)
        screen.switch_tab(1)
        await pilot.pause()

        task = asyncio.create_task(
            screen._save_inline_comment_draft(
                "draft body",
                path="test.py",
                line=1,
                side="RIGHT",
            )
        )

        try:
            await asyncio.wait_for(service.create_started.wait(), timeout=1)
            await pilot.pause()

            assert task.done() is False
            assert len(screen.query("CommentCard.pending-draft")) == 1
        finally:
            service.allow_create.set()
            with suppress(Exception):
                await task


@pytest.mark.asyncio
async def test_pr_info_shift_o_opens_selected_pr_thread_in_combined_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def noop_load(self: MainScreen) -> None:
        return None

    monkeypatch.setattr(MainScreen, "_load_data", noop_load)

    class TestApp(RitApp):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(pr_number=1))

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, MainScreen)

        patch = "@@ -10,1 +10,1 @@\n-old\n+new"
        filenames = ["one.py", "two.py"]
        screen.store.state.pr = PR(number=1, title="Test PR")
        screen.store.state.files = [
            PRFile(filename=filename, status="modified", patch=patch)
            for filename in filenames
        ]
        screen.store.state.file_diffs = {
            filename: parse_patch(patch, filename) for filename in filenames
        }

        root = PRComment(
            id=42,
            body="please check this",
            path="two.py",
            line=10,
            side="RIGHT",
        )
        thread = ReviewThread(
            id="thread-42",
            path="two.py",
            line=10,
            diffSide="RIGHT",
            comments=NodeList(nodes=[root]),
        )
        screen.store.state.comments = [root]
        screen.store.state.review_threads = [thread]
        screen.store.state.thread_info_cache = {
            42: ReviewThreadInfo(
                thread_id="thread-42",
                is_resolved=False,
                path="two.py",
                line=10,
                root_comment_id=42,
            )
        }
        screen.store.state.thread_cache = {42: thread}

        screen.pr_info.refresh_comments()
        screen.file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        screen.pr_info.next_comment()
        await pilot.press("O")
        await pilot.pause()
        await pilot.pause()

        assert screen.current_tab == 1
        assert screen.file_changes.diff_view.current_file == "All files"
        assert screen.store.state.selected_file == "two.py"
        line = screen.file_changes.diff_view._current_line()
        assert line is not None
        assert line.new_line_no == 10
