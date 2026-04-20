"""Tests for the main rit application."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from rit.app import RitApp
from rit.cli import parse_pr_reference
from rit.state.models import FileViewedState, PR, PRFile
from rit.ui.screens.branch_picker import BranchPickerScreen


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
    from rit.core.diff import parse_patch

    return parse_patch(f"@@ -{line_no} +{line_no} @@\n-{old}\n+{new}", filename)


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
        """Test tab switching with Tab and Shift+Tab."""
        async with app.run_test() as pilot:
            from textual.widgets import TabbedContent

            tabbed = app.screen.query_one(TabbedContent)
            assert tabbed.active == "pr-info"

            await pilot.press("tab")
            assert tabbed.active == "files"

            await pilot.press("shift+tab")
            assert tabbed.active == "pr-info"

    async def test_files_tab_focuses_tree_by_default(self, app: RitApp) -> None:
        """Test that entering Files tab focuses the file tree."""
        async with app.run_test() as pilot:
            from textual.widgets import TabbedContent, Tree

            tabbed = app.screen.query_one(TabbedContent)
            assert tabbed.active == "pr-info"

            await pilot.press("tab")
            assert tabbed.active == "files"

            await pilot.pause()
            tree = app.screen.query_one("#file-tree", Tree)
            assert tree.has_focus

    async def test_files_tab_shift_hl_moves_between_tree_and_split_panes(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that Shift+H/L moves focus across tree, left diff, and right diff."""

        _stub_initial_loads(monkeypatch)

        async with app.run_test() as pilot:
            await pilot.press("tab")
            await pilot.pause()

            tree = app.screen.query_one("#file-tree")
            diff = app.screen.query_one("#diff-view-main")
            diff.mode = "split"
            await diff.show_diff("test.py", _simple_diff("test.py"))
            await pilot.pause()

            assert tree.has_focus

            await pilot.press("L")
            await pilot.pause()
            assert diff.has_focus
            assert diff.active_pane == "old"

            await pilot.press("L")
            await pilot.pause()
            assert diff.has_focus
            assert diff.active_pane == "new"

            await pilot.press("H")
            await pilot.pause()
            assert diff.has_focus
            assert diff.active_pane == "old"

            await pilot.press("H")
            await pilot.pause()
            assert tree.has_focus

    async def test_files_tab_m_toggles_viewed_for_diff_focus_current_file(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Diff focus should toggle the file currently shown in the diff."""

        _stub_initial_loads(monkeypatch, include_view_states=True)

        calls: list[tuple[str, bool]] = []

        async def fake_set_file_viewed(filename: str, *, viewed: bool) -> None:
            calls.append((filename, viewed))

        async with app.run_test() as pilot:
            from textual.widgets import Tree

            await pilot.press("tab")
            await pilot.pause()

            screen = app.screen
            screen.store.state.pr = PR(number=123)
            screen.store.state.files = [
                PRFile(filename="one.py", status="modified"),
                PRFile(filename="two.py", status="modified"),
            ]
            monkeypatch.setattr(screen.store, "set_file_viewed", fake_set_file_viewed)

            file_tree = screen.file_changes.file_tree
            diff = screen.file_changes.diff_view
            file_tree.refresh_files(screen.store.state.files)
            await pilot.pause()

            await diff.show_diff("one.py", _simple_diff("one.py"))
            await pilot.pause()

            tree = file_tree.query_one("#file-tree", Tree)
            tree.move_cursor(file_tree._file_nodes["two.py"])
            diff.focus()
            await pilot.pause()

            await pilot.press("m")
            await pilot.pause()
            await pilot.pause()

            assert (
                screen.store.state.files[0].viewer_viewed_state
                == FileViewedState.VIEWED
            )
            assert (
                screen.store.state.files[1].viewer_viewed_state
                == FileViewedState.UNVIEWED
            )
            assert calls == [("one.py", True)]

    async def test_files_tab_m_toggles_viewed_for_tree_cursor_file(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Tree focus should toggle the file under the tree cursor, not the diff file."""

        _stub_initial_loads(monkeypatch, include_view_states=True)

        calls: list[tuple[str, bool]] = []

        async def fake_set_file_viewed(filename: str, *, viewed: bool) -> None:
            calls.append((filename, viewed))

        async with app.run_test() as pilot:
            from textual.widgets import Tree

            await pilot.press("tab")
            await pilot.pause()

            screen = app.screen
            screen.store.state.pr = PR(number=123)
            screen.store.state.files = [
                PRFile(filename="one.py", status="modified"),
                PRFile(filename="two.py", status="modified"),
            ]
            screen.store.state.selected_file = "one.py"
            monkeypatch.setattr(screen.store, "set_file_viewed", fake_set_file_viewed)

            file_tree = screen.file_changes.file_tree
            diff = screen.file_changes.diff_view
            file_tree.refresh_files(screen.store.state.files)
            await pilot.pause()

            await diff.show_diff("one.py", _simple_diff("one.py"))
            await pilot.pause()

            tree = file_tree.query_one("#file-tree", Tree)
            tree.move_cursor(file_tree._file_nodes["two.py"])
            tree.focus()
            await pilot.pause()

            await pilot.press("m")
            await pilot.pause()
            await pilot.pause()

            assert (
                screen.store.state.files[0].viewer_viewed_state
                == FileViewedState.UNVIEWED
            )
            assert (
                screen.store.state.files[1].viewer_viewed_state
                == FileViewedState.VIEWED
            )
            assert calls == [("two.py", True)]

    async def test_files_tab_e_opens_current_file_in_parent_nvim(
        self,
        app: RitApp,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that pressing e opens the current file at the diff cursor."""

        _stub_initial_loads(monkeypatch)

        target = tmp_path / "src" / "test.py"
        target.parent.mkdir(parents=True)
        target.write_text("print('hello')\n")

        monkeypatch.setattr(
            "rit.ui.screens.main._resolve_repo_root",
            lambda: tmp_path,
        )

        calls: list[tuple[Path, int, int]] = []

        def fake_open(path: Path, *, line: int, column: int) -> None:
            calls.append((path, line, column))

        monkeypatch.setattr("rit.ui.screens.main._open_in_parent_nvim", fake_open)

        async with app.run_test() as pilot:
            await pilot.press("tab")
            await pilot.pause()

            diff = app.screen.query_one("#diff-view-main")
            await diff.show_diff("src/test.py", _simple_diff("src/test.py", line_no=10))
            diff.cursor_column = 4
            await pilot.pause()

            await pilot.press("e")
            await pilot.pause()

            assert calls == [(target, 10, 5)]
            assert app.is_running is True

    def test_open_in_parent_nvim_uses_single_remote_send(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that opening in parent Nvim includes cursor placement and float close."""
        from rit.ui.screens.main import _open_in_parent_nvim

        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs) -> SimpleNamespace:
            calls.append(command)
            return SimpleNamespace(stdout="")

        monkeypatch.setenv("NVIM", "/tmp/nvim.sock")
        monkeypatch.setattr("rit.ui.screens.main.subprocess.run", fake_run)

        _open_in_parent_nvim(Path("/tmp/src/test.py"), line=12, column=5)

        assert len(calls) == 1
        assert calls[0][:4] == ["nvim", "--server", "/tmp/nvim.sock", "--remote-send"]
        assert "local target_line = 12;" in calls[0][4]
        assert "local target_column = 4;" in calls[0][4]
        assert "vim.api.nvim_win_set_cursor" in calls[0][4]
        assert "vim.api.nvim_win_close(origin, true)" in calls[0][4]

    async def test_quit_action(self, app: RitApp) -> None:
        """Test that q quits the app."""
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running

    async def test_ctrl_b_opens_picker_and_copies_head_branch(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that ctrl+b opens the branch picker and Enter copies head."""

        _stub_initial_loads(monkeypatch)

        async with app.run_test() as pilot:
            screen = app.screen
            screen.store.state.pr = PR(head_ref="feature/test", base_ref="main")
            await pilot.pause()

            await pilot.press("ctrl+b")
            await pilot.pause()
            assert isinstance(app.screen, BranchPickerScreen)

            await pilot.press("enter")
            await pilot.pause()
            assert app.clipboard == "feature/test"

    async def test_ctrl_b_picker_copies_base_branch_on_files_tab(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that the picker can copy base branch from the Files tab."""

        _stub_initial_loads(monkeypatch)

        async with app.run_test() as pilot:
            screen = app.screen
            screen.store.state.pr = PR(head_ref="feature/test", base_ref="main")
            await pilot.pause()

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert isinstance(app.screen, BranchPickerScreen)

            await pilot.press("down", "enter")
            await pilot.pause()
            assert app.clipboard == "main"
