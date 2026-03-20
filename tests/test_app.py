"""Tests for the main rit application."""

import pytest
from textual.pilot import Pilot

from rit.app import RitApp
from rit.cli import parse_pr_reference


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
    def app(self) -> RitApp:
        """Create a test app instance."""
        return RitApp(owner="test", repo="repo", pr_number=123)

    async def test_app_starts(self, app: RitApp) -> None:
        """Test that the app starts without errors."""
        async with app.run_test() as pilot:
            assert app.is_running

    async def test_app_has_main_screen(self, app: RitApp) -> None:
        """Test that the app shows the main screen."""
        async with app.run_test() as pilot:
            from rit.ui.screens.main import MainScreen

            assert isinstance(app.screen, MainScreen)

    async def test_tab_switching_with_keys(self, app: RitApp) -> None:
        """Test tab switching with Shift+H/L keyboard shortcuts."""
        async with app.run_test() as pilot:
            from textual.widgets import TabbedContent

            tabbed = app.screen.query_one(TabbedContent)
            assert tabbed.active == "pr-info"

            # Switch to Files tab with Shift+L
            await pilot.press("L")
            assert tabbed.active == "files"

            # Switch back to PR Info with Shift+H
            await pilot.press("H")
            assert tabbed.active == "pr-info"

    async def test_tab_switching_with_tab_key(self, app: RitApp) -> None:
        """Test cycling through tabs with Tab key."""
        async with app.run_test() as pilot:
            from textual.widgets import TabbedContent

            tabbed = app.screen.query_one(TabbedContent)
            assert tabbed.active == "pr-info"

            await pilot.press("tab")
            assert tabbed.active == "files"

            await pilot.press("tab")
            assert tabbed.active == "pr-info"

    async def test_files_tab_focuses_tree_by_default(self, app: RitApp) -> None:
        """Test that entering Files tab focuses the file tree."""
        async with app.run_test() as pilot:
            from textual.widgets import TabbedContent, Tree

            tabbed = app.screen.query_one(TabbedContent)
            assert tabbed.active == "pr-info"

            await pilot.press("L")
            assert tabbed.active == "files"

            await pilot.pause()
            tree = app.screen.query_one("#file-tree", Tree)
            assert tree.has_focus

    async def test_quit_action(self, app: RitApp) -> None:
        """Test that q quits the app."""
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running
