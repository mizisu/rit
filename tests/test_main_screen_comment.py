import pytest
from textual.widgets import TextArea

from rit.app import RitApp
from rit.core.diff import parse_patch
from rit.state.models import PR
from rit.ui.screens.main import MainScreen


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
