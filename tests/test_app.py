"""Tests for the main rit application."""

import asyncio
import threading
from typing import cast

import pytest
from textual.widgets import Static, Tree

from rit.app import RitApp
from rit.cli import parse_pr_reference
from rit.core.diff import parse_patch
from rit.state.models import PR, FileViewedState, LoadingState, PRFile
from rit.ui.screens.file_picker import FilePickerScreen


def _stub_initial_loads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_view_states: bool = False,
) -> None:
    async def fake_load_all(_self) -> None:
        return None

    monkeypatch.setattr("rit.state.store.PRStore.load_all", fake_load_all)

    if include_view_states:

        async def fake_load_file_view_states(_self) -> None:
            return None

        monkeypatch.setattr(
            "rit.state.store.PRStore.load_file_view_states",
            fake_load_file_view_states,
        )


def _simple_diff(
    filename: str,
    *,
    line_no: int = 1,
    old: str = "old_value",
    new: str = "new_value",
):
    return parse_patch(f"@@ -{line_no} +{line_no} @@\n-{old}\n+{new}", filename)


def _static_text(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


def test_copy_to_clipboard_updates_textual_and_system_clipboards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App clipboard copy should update Textual state and native clipboard."""
    copied: list[str] = []
    monkeypatch.setattr("rit.app.pyperclip.copy", copied.append)

    app = RitApp()

    app.copy_to_clipboard("src/rit/app.py")

    assert app.clipboard == "src/rit/app.py"
    assert copied == ["src/rit/app.py"]


class TestPRReferenceParsing:
    """Tests for PR reference parsing."""

    def test_parse_number_only(self) -> None:
        """Test parsing a simple PR number."""
        owner, repo, number = parse_pr_reference("123")
        assert owner is None
        assert repo is None
        assert number == 123

    def test_parse_full_reference(self) -> None:
        """Test parsing owner/repo#number format."""
        owner, repo, number = parse_pr_reference("owner/repo#456")
        assert owner == "owner"
        assert repo == "repo"
        assert number == 456

    def test_parse_complex_repo_name(self) -> None:
        """Test parsing with complex repo names."""
        owner, repo, number = parse_pr_reference("my-org/my-repo#789")
        assert owner == "my-org"
        assert repo == "my-repo"
        assert number == 789

    def test_parse_invalid_reference(self) -> None:
        """Test that invalid references raise an error."""
        import click

        with pytest.raises(click.BadParameter):
            parse_pr_reference("invalid")

    def test_parse_github_url(self) -> None:
        """Test parsing GitHub PR URL."""
        owner, repo, number = parse_pr_reference(
            "https://github.com/lemonbase-tech/lemonbase/pull/19419"
        )
        assert owner == "lemonbase-tech"
        assert repo == "lemonbase"
        assert number == 19419

    def test_parse_github_url_without_https(self) -> None:
        """Test parsing GitHub PR URL without https://."""
        owner, repo, number = parse_pr_reference("github.com/owner/repo/pull/123")
        assert owner == "owner"
        assert repo == "repo"
        assert number == 123

    def test_parse_github_url_with_extra_path(self) -> None:
        """Test parsing GitHub PR URL with extra path segments."""
        owner, repo, number = parse_pr_reference(
            "https://github.com/owner/repo/pull/123/files"
        )
        assert owner == "owner"
        assert repo == "repo"
        assert number == 123


class TestRitApp:
    """Tests for the RitApp class."""

    def test_pause_gc_on_scroll_enabled(self) -> None:
        """Test that Textual's experimental GC pause on scroll is enabled."""
        assert RitApp.PAUSE_GC_ON_SCROLL is True

    @pytest.fixture
    def app(self, monkeypatch: pytest.MonkeyPatch) -> RitApp:
        """Create a test app instance."""
        _stub_initial_loads(monkeypatch, include_view_states=True)
        return RitApp(owner="test", repo="repo", pr_number=123)

    async def test_pr_loaded_refreshes_description_before_discussion(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Summary load should paint the PR description without waiting for comments."""

        _stub_initial_loads(monkeypatch)

        from rit.state.store import PRStore
        from rit.ui.components.pr_info import PRInfo
        from rit.ui.screens.main import MainScreen

        calls: list[str] = []

        def fake_refresh_pr_data(pr_info: PRInfo) -> None:
            calls.append(pr_info.store.state.pr.body if pr_info.store.state.pr else "")

        monkeypatch.setattr(PRInfo, "refresh_pr_data", fake_refresh_pr_data)

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            pr = PR(number=123, title="Loaded PR", body="Loaded body")
            screen.store.state.pr = pr

            screen.on_pr_loaded(PRStore.PRLoaded(pr=pr))
            await pilot.pause()

            assert calls == ["Loaded body"]

    async def test_app_starts(self, app: RitApp) -> None:
        """Test that the app starts without errors."""
        async with app.run_test() as pilot:
            assert app.is_running

    async def test_app_has_main_screen(self, app: RitApp) -> None:
        """Test that the app shows the main screen."""
        async with app.run_test() as pilot:
            from rit.ui.screens.main import MainScreen

            assert isinstance(app.screen, MainScreen)

    def test_flash_uses_plain_text_for_markup_like_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rit.ui.messages import Flash

        calls = []

        def fake_notify(*args, **kwargs) -> None:
            calls.append((args, kwargs))

        app = RitApp(owner="test", repo="repo", pr_number=123)
        monkeypatch.setattr(app, "notify", fake_notify)

        app.on_flash(
            Flash(
                "Validation error [type=None, input_type=NoneType]",
                style="error",
            )
        )

        assert calls[0][0][0] == "Validation error [type=None, input_type=NoneType]"
        assert calls[0][1]["markup"] is False

    def test_store_error_notification_uses_plain_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from rit.state.store import PRStore
        from rit.ui.screens.main import MainScreen

        calls = []

        def fake_notify(*args, **kwargs) -> None:
            calls.append((args, kwargs))

        screen = MainScreen(owner="test", repo="repo", pr_number=123)
        monkeypatch.setattr(screen, "notify", fake_notify)

        screen.on_store_error(
            PRStore.ErrorOccurred(
                error="Validation error [type=None, input_type=NoneType]"
            )
        )

        assert calls[0][0][0] == "Validation error [type=None, input_type=NoneType]"
        assert calls[0][1]["markup"] is False

    async def test_pr_info_o_opens_pr_in_browser(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pressing o on PR Info should use the global browser-open action."""

        _stub_initial_loads(monkeypatch)
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(args: list[str], **kwargs: object) -> None:
            calls.append((args, kwargs))

        monkeypatch.setattr("rit.app.subprocess.run", fake_run)

        async with app.run_test() as pilot:
            await pilot.press("o")
            await pilot.pause()

        assert calls == [
            (
                [
                    "gh",
                    "pr",
                    "view",
                    "123",
                    "--web",
                    "-R",
                    "test/repo",
                ],
                {"check": True, "capture_output": True},
            )
        ]

    async def test_files_tab_defers_pr_info_discussion_render_until_pr_info_tab(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Hidden PR info rendering should not interrupt Files tab input."""

        _stub_initial_loads(monkeypatch)

        from rit.state.store import PRStore
        from rit.ui.components.pr_info import PRInfo
        from rit.ui.screens.main import MainScreen

        calls: list[str] = []

        def fake_refresh_pr_data(_pr_info: PRInfo) -> None:
            calls.append("data")

        def fake_refresh_comments(_pr_info: PRInfo) -> None:
            calls.append("comments")

        monkeypatch.setattr(PRInfo, "refresh_pr_data", fake_refresh_pr_data)
        monkeypatch.setattr(PRInfo, "refresh_comments", fake_refresh_comments)

        async with app.run_test() as pilot:
            await pilot.pause()

            screen = cast(MainScreen, app.screen)
            screen.current_tab = 1
            monkeypatch.setattr(
                screen.file_changes.file_tree,
                "refresh_files",
                lambda: None,
            )

            def fake_run_worker(coro, *args, **kwargs) -> None:
                coro.close()

            monkeypatch.setattr(screen, "run_worker", fake_run_worker)

            pr = PR(number=123, title="Loaded PR", body="Loaded body")
            screen.store.state.pr = pr
            screen.on_pr_discussion_loaded(PRStore.PRDiscussionLoaded(pr=pr))

            assert calls == []
            assert screen._pr_info_refresh_pending is True

            screen.current_tab = 0
            screen._refresh_pending_pr_info()

            assert calls == ["data", "comments"]
            assert screen._pr_info_refresh_pending is False

    async def test_staged_load_paints_first_file_then_combined_diff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = threading.Event()
        patch = "@@ -1,1 +1,1 @@\n-old\n+new"

        async def fake_load_all(store) -> None:
            store.state.pr = PR(
                number=123,
                title="Staged PR",
                baseRefName="main",
                headRefName="feature",
                changedFiles=2,
            )
            store.state.files_total_count = 2
            store._post_message(store.PRLoaded(pr=store.state.pr))

            first_file = PRFile(filename="one.py", status="modified", patch=patch)
            store.state.files_loading = LoadingState.LOADING
            store.state.files = [first_file]
            store.state.file_diffs = {"one.py": parse_patch(patch, "one.py")}
            store.state.files_loaded_count = 1
            store._post_message(
                store.FilesLoaded(
                    files=list(store.state.files), loaded_count=1, total_count=2
                )
            )

            await asyncio.to_thread(release.wait, 1.0)

            store.state.pr = store.state.pr.model_copy(update={"body": "Loaded body"})
            store._post_message(store.PRDiscussionLoaded(pr=store.state.pr))

            second_file = PRFile(filename="two.py", status="modified", patch=patch)
            store.state.files.append(second_file)
            store.state.file_diffs["two.py"] = parse_patch(patch, "two.py")
            store.state.files_loaded_count = 2
            store.state.files_loading = LoadingState.LOADED
            store._post_message(
                store.FilesLoaded(
                    files=list(store.state.files), loaded_count=2, total_count=2
                )
            )

        async def fake_load_file_view_states(_store) -> None:
            return None

        monkeypatch.setattr("rit.state.store.PRStore.load_all", fake_load_all)
        monkeypatch.setattr(
            "rit.state.store.PRStore.load_file_view_states",
            fake_load_file_view_states,
        )

        app = RitApp(owner="test", repo="repo", pr_number=123)

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            screen = app.screen
            file_count = screen.query_one("#file-count", Static)

            assert screen.header.pr_title == "Staged PR"
            assert screen.file_changes.diff_view.current_file == "one.py"
            assert _static_text(file_count) == "Files (1/2)"

            release.set()
            await pilot.pause()
            await pilot.pause()
            await pilot.pause()

            assert screen.file_changes.diff_view.current_file == "All files"
            assert screen.store.state.selected_file == "one.py"
            assert _static_text(file_count) == "Files (2)"

    async def test_quit_action(self, app: RitApp) -> None:
        """Test that q quits the app."""
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running
