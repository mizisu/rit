"""Tests for the main rit application."""

import asyncio
import threading
from typing import cast

import pytest
from textual.widgets import Static, TabbedContent, TextArea, Tree

from rit.app import RitApp
from rit.cli import parse_pr_reference
from rit.core.diff import parse_patch
from rit.state.models import PR, FileViewedState, LoadingState, PRFile
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
    return parse_patch(f"@@ -{line_no} +{line_no} @@\n-{old}\n+{new}", filename)


def _static_text(widget: Static) -> str:
    content = getattr(widget, "content", "")
    return str(getattr(content, "plain", content))


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
            tabbed = app.screen.query_one(TabbedContent)
            assert tabbed.active == "pr-info"

            await pilot.press("tab")
            assert tabbed.active == "files"

            await pilot.pause()
            tree = app.screen.query_one("#file-tree", Tree)
            assert tree.has_focus

    async def test_files_tab_ctrl_h_l_moves_between_tree_and_diff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+h/l should move across tree, old pane, and new pane."""

        _stub_initial_loads(monkeypatch)

        from rit.ui.screens.main import MainScreen
        from rit.ui.widgets import DiffView

        app = RitApp(owner="test", repo="repo", pr_number=123)
        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.switch_tab(1)
            diff_view = screen.query_one(DiffView)
            diff_view.mode = "split"
            await diff_view.show_diff(
                "src/app.py",
                _simple_diff("src/app.py"),
            )
            await pilot.pause()

            tree = screen.query_one("#file-tree", Tree)
            tree.focus()
            await pilot.pause()

            await pilot.press("ctrl+l")
            await pilot.pause()
            assert diff_view.has_focus
            assert diff_view.active_pane == "old"

            await pilot.press("ctrl+l")
            await pilot.pause()
            assert diff_view.has_focus
            assert diff_view.active_pane == "new"

            await pilot.press("ctrl+h")
            await pilot.pause()
            assert diff_view.has_focus
            assert diff_view.active_pane == "old"

            await pilot.press("ctrl+h")
            await pilot.pause()
            assert tree.has_focus

    async def test_text_entry_blocks_main_and_app_single_key_bindings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Typing in an inline text editor should not trigger app or tab actions."""

        _stub_initial_loads(monkeypatch)

        from rit.ui.screens.main import MainScreen

        app = RitApp(owner="test", repo="repo", pr_number=123)
        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.store.state.pr = PR(number=123, title="Test PR")
            screen.action_comment()
            await pilot.pause()

            tabbed = screen.query_one(TabbedContent)
            textarea = screen.query_one("#comment-editor-body", TextArea)
            assert textarea.has_focus

            await pilot.press("q", "j", "k", "tab")
            await pilot.pause()

            assert app.is_running
            assert tabbed.active == "pr-info"
            assert textarea.text.startswith("qjk")

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
            await pilot.press("tab")
            await pilot.pause()

            screen = cast(MainScreen, app.screen)
            pr = PR(number=123, title="Loaded PR", body="Loaded body")
            screen.store.state.pr = pr
            screen.on_pr_discussion_loaded(PRStore.PRDiscussionLoaded(pr=pr))
            await pilot.pause()

            assert calls == []
            assert screen._pr_info_refresh_pending is True

            await pilot.press("shift+tab")
            await pilot.pause()

            assert calls == ["data", "comments"]
            assert screen._pr_info_refresh_pending is False

    async def test_staged_load_paints_summary_and_first_file_before_full_load(
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

            assert screen.file_changes.diff_view.current_file == "one.py"
            assert _static_text(file_count) == "Files (2)"

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

    async def test_files_tab_e_focuses_file_tree(
        self, app: RitApp, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that pressing e in the diff focuses the file tree."""

        _stub_initial_loads(monkeypatch)

        async with app.run_test() as pilot:
            await pilot.press("tab")
            await pilot.pause()

            tree = app.screen.query_one("#file-tree")
            diff = app.screen.query_one("#diff-view-main")
            await diff.show_diff("src/test.py", _simple_diff("src/test.py", line_no=10))
            diff.focus()
            await pilot.pause()

            assert diff.has_focus

            await pilot.press("e")
            await pilot.pause()

            assert tree.has_focus

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
            screen.store.state.pr = PR(headRefName="feature/test", baseRefName="main")
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
            screen.store.state.pr = PR(headRefName="feature/test", baseRefName="main")
            await pilot.pause()

            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert isinstance(app.screen, BranchPickerScreen)

            await pilot.press("down", "enter")
            await pilot.pause()
            assert app.clipboard == "main"
