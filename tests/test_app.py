"""Tests for the main rit application."""

import asyncio
from typing import cast

import pytest
from textual.widgets import Static

from rit.app import RitApp
from rit.cli import parse_pr_reference
from rit.core.diff import parse_patch
from rit.state.models import PR, LoadingState, PRComment, PRFile, PRReview
from rit.ui.screens.settings import SettingsScreen


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

    async def test_settings_action_opens_current_settings_screen(
        self,
        app: RitApp,
    ) -> None:
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()

            assert isinstance(app.screen, SettingsScreen)
            assert (
                _static_text(app.screen.query_one("#setting-ui-theme", Static))
                == "Theme: Catppuccin Macchiato"
            )
            assert (
                _static_text(app.screen.query_one("#setting-ui-diff-mode", Static))
                == "Diff View Mode: Auto (based on width)"
            )
            assert (
                _static_text(
                    app.screen.query_one("#setting-keybindings-vim-mode", Static)
                )
                == "Enable Vim keybindings?: On"
            )
            assert (
                _static_text(
                    app.screen.query_one("#setting-github-auto-resolve", Static)
                )
                == "Auto-resolve threads?: Off"
            )

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
        release = asyncio.Event()
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

            await release.wait()

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

    async def test_refresh_preserving_cursor_keeps_full_file_preview(
        self, app: RitApp
    ) -> None:
        from rit.ui.screens.main import MainScreen

        patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
        source_diff = parse_patch(patch, "preview.py")
        full_content = "\n".join(f"line {line}" for line in range(1, 7))

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.store.state.file_diffs = {"preview.py": source_diff}
            screen.store.state.file_contents["preview.py"] = full_content
            diff_view = screen.file_changes.diff_view

            await diff_view.show_full_file_preview(
                "preview.py",
                full_content,
                source_diff=source_diff,
            )
            await pilot.pause()
            current_line = diff_view._line_index_by_new_number[6]
            diff_view.cursor_line = current_line

            await screen._refresh_diff_preserving_cursor(
                "preview.py",
                current_line,
                "new",
                focus_diff=True,
            )
            await pilot.pause()

            assert diff_view._showing_full_file is True
            assert len(diff_view._all_lines) == 6
            assert diff_view.cursor_line == current_line

    async def test_post_inline_comment_keeps_editor_open_when_submit_fails(
        self,
        app: RitApp,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from rit.ui.screens.main import MainScreen

        patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
        source_diff = parse_patch(patch, "preview.py")

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            diff_view = screen.file_changes.diff_view
            await diff_view.show_diff("preview.py", source_diff)
            await pilot.pause()
            diff_view.cursor_line = diff_view._line_index_by_new_number[3]
            assert await diff_view.open_inline_comment_editor() is True
            await pilot.pause()

            async def fail_submit(*args, **kwargs) -> None:
                raise RuntimeError("submit failed")

            monkeypatch.setattr(
                screen.store,
                "submit_inline_comment",
                fail_submit,
            )

            with pytest.raises(RuntimeError, match="submit failed"):
                await screen._post_inline_comment(
                    "hello",
                    path="preview.py",
                    line=3,
                    side="RIGHT",
                )

            assert diff_view.inline_comment_target() == ("preview.py", 3, "RIGHT")

    async def test_save_inline_comment_draft_renders_immediately(
        self,
        app: RitApp,
    ) -> None:
        from rit.ui.screens.main import MainScreen
        from rit.ui.widgets.comment_card import CommentCard

        patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
        source_diff = parse_patch(patch, "preview.py")

        class DraftService:
            async def create_pending_review(self, *args, **kwargs) -> PRReview:
                return PRReview(id=88)

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.current_tab = 1
            screen.store.state.pr = PR(number=123, head_sha="deadbeef")
            screen.store.state.file_diffs = {"preview.py": source_diff}
            screen.store._service = DraftService()  # type: ignore[assignment]
            diff_view = screen.file_changes.diff_view
            await diff_view.show_diff("preview.py", source_diff)
            await pilot.pause()
            current_line = diff_view._line_index_by_new_number[3]
            diff_view.cursor_line = current_line

            await screen._save_inline_comment_draft(
                "hello draft",
                path="preview.py",
                line=3,
                side="RIGHT",
            )
            await pilot.pause()
            await pilot.pause()

            draft = diff_view.query_one("#pending-draft-1-right-0", CommentCard)
            assert draft._body == "hello draft"

    async def test_update_inline_comment_draft_renders_before_sync_finishes(
        self,
        app: RitApp,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from rit.ui.screens.main import MainScreen
        from rit.ui.widgets.comment_card import CommentCard

        patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
        source_diff = parse_patch(patch, "preview.py")

        class BlockingDraftService:
            def __init__(self) -> None:
                self.create_started = asyncio.Event()
                self.allow_create = asyncio.Event()

            async def create_pending_review(self, *args, **kwargs) -> PRReview:
                self.create_started.set()
                await self.allow_create.wait()
                return PRReview(id=88)

        service = BlockingDraftService()

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.current_tab = 1
            screen.store.state.pr = PR(number=123, head_sha="deadbeef")
            screen.store.state.file_diffs = {"preview.py": source_diff}
            screen.store._service = service  # type: ignore[assignment]
            diff_view = screen.file_changes.diff_view
            await diff_view.show_diff("preview.py", source_diff)
            await pilot.pause()
            current_line = diff_view._line_index_by_new_number[3]
            diff_view.cursor_line = current_line
            refresh_calls = 0
            original_refresh = screen._refresh_diff_preserving_cursor

            async def count_refresh(*args, **kwargs) -> None:
                nonlocal refresh_calls
                refresh_calls += 1
                await original_refresh(*args, **kwargs)

            monkeypatch.setattr(
                screen, "_refresh_diff_preserving_cursor", count_refresh
            )

            task = asyncio.create_task(
                screen._save_inline_comment_draft(
                    "hello draft",
                    path="preview.py",
                    line=3,
                    side="RIGHT",
                )
            )
            await asyncio.wait_for(service.create_started.wait(), timeout=1)
            await pilot.pause()

            draft = diff_view.query_one("#pending-draft-1-right-0", CommentCard)
            assert draft._body == "hello draft"
            assert not task.done()

            service.allow_create.set()
            assert await task is True
            assert refresh_calls == 1

    async def test_delete_inline_comment_draft_requires_selected_draft(
        self,
        app: RitApp,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from rit.ui.screens.main import MainScreen

        patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
        source_diff = parse_patch(patch, "preview.py")

        class DeleteService:
            def __init__(self) -> None:
                self.delete_called = False

            async def delete_pending_review(self, *args, **kwargs) -> None:
                self.delete_called = True

        service = DeleteService()

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.current_tab = 1
            screen.store.state.pr = PR(number=123, head_sha="deadbeef")
            screen.store.state.pending_review_id = 88
            screen.store.state.file_diffs = {"preview.py": source_diff}
            screen.store.save_pending_inline_comment(
                "hello draft",
                path="preview.py",
                line=3,
                side="RIGHT",
            )
            screen.store._service = service  # type: ignore[assignment]
            diff_view = screen.file_changes.diff_view
            await diff_view.show_diff("preview.py", source_diff)
            await pilot.pause()
            current_line = diff_view._line_index_by_new_number[3]
            diff_view.cursor_line = current_line
            assert diff_view._comment_cursor_index == 0
            refresh_calls = 0

            async def count_refresh(*args, **kwargs) -> None:
                nonlocal refresh_calls
                refresh_calls += 1

            monkeypatch.setattr(
                screen,
                "_refresh_diff_preserving_cursor",
                count_refresh,
            )

            assert await screen._delete_pending_inline_comment() is False

            assert screen.store.state.pending_review_comments[0].body == "hello draft"
            assert service.delete_called is False
            assert refresh_calls == 0

    async def test_delete_inline_comment_draft_renders_immediately(
        self,
        app: RitApp,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from rit.ui.screens.main import MainScreen

        patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
        source_diff = parse_patch(patch, "preview.py")

        class BlockingDeleteService:
            def __init__(self) -> None:
                self.delete_started = asyncio.Event()
                self.allow_delete = asyncio.Event()

            async def list_review_comments(self, *args, **kwargs) -> list[PRComment]:
                return [
                    PRComment(
                        id=5,
                        body="hello draft",
                        path="preview.py",
                        line=3,
                        side="RIGHT",
                        pull_request_review_id=88,
                    )
                ]

            async def delete_pending_review(self, *args, **kwargs) -> None:
                self.delete_started.set()
                await self.allow_delete.wait()

        service = BlockingDeleteService()

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.current_tab = 1
            screen.store.state.pr = PR(number=123, head_sha="deadbeef")
            screen.store.state.pending_review_id = 88
            screen.store.state.file_diffs = {"preview.py": source_diff}
            screen.store.save_pending_inline_comment(
                "hello draft",
                path="preview.py",
                line=3,
                side="RIGHT",
            )
            screen.store._service = service  # type: ignore[assignment]
            diff_view = screen.file_changes.diff_view
            await diff_view.show_diff("preview.py", source_diff)
            await pilot.pause()
            current_line = diff_view._line_index_by_new_number[3]
            diff_view.cursor_line = current_line
            diff_view._comment_cursor_index = 1
            refresh_calls = 0
            original_refresh = screen._refresh_diff_preserving_cursor

            async def count_refresh(*args, **kwargs) -> None:
                nonlocal refresh_calls
                refresh_calls += 1
                await original_refresh(*args, **kwargs)

            monkeypatch.setattr(
                screen,
                "_refresh_diff_preserving_cursor",
                count_refresh,
            )

            task = asyncio.create_task(screen._delete_pending_inline_comment())
            await pilot.pause()
            await pilot.pause()

            assert len(diff_view.query("CommentCard.pending-draft")) == 0
            assert not task.done()

            service.allow_delete.set()
            assert await task is True
            assert refresh_calls == 1

    async def test_save_full_file_preview_draft_outside_diff_renders_locally(
        self,
        app: RitApp,
    ) -> None:
        from rit.ui.screens.main import MainScreen
        from rit.ui.widgets.comment_card import CommentCard

        patch = """@@ -2,2 +2,3 @@
 line 2
+line 3 added
 line 4"""
        source_diff = parse_patch(patch, "preview.py")
        full_content = "\n".join(f"line {line}" for line in range(1, 7))

        class DraftService:
            def __init__(self) -> None:
                self.create_calls = 0

            async def create_pending_review(self, *args, **kwargs) -> PRReview:
                self.create_calls += 1
                return PRReview(id=88)

        service = DraftService()

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.current_tab = 1
            screen.store.state.pr = PR(number=123, head_sha="deadbeef")
            screen.store.state.file_diffs = {"preview.py": source_diff}
            screen.store.state.file_contents["preview.py"] = full_content
            screen.store._service = service  # type: ignore[assignment]
            diff_view = screen.file_changes.diff_view
            await diff_view.show_full_file_preview(
                "preview.py",
                full_content,
                source_diff=source_diff,
            )
            await pilot.pause()
            current_line = diff_view._line_index_by_new_number[6]
            diff_view.cursor_line = current_line

            await screen._save_inline_comment_draft(
                "local only draft",
                path="preview.py",
                line=6,
                side="RIGHT",
            )
            await pilot.pause()
            await pilot.pause()

            draft = diff_view.query_one("#pending-draft-5-right-0", CommentCard)
            assert draft._body == "local only draft"
            assert screen.store.state.pending_review_id is None
            assert service.create_calls == 0

    async def test_save_combined_diff_draft_renders_immediately(
        self,
        app: RitApp,
    ) -> None:
        from rit.ui.components.file_changes import COMBINED_DIFF_FILENAME
        from rit.ui.screens.main import MainScreen
        from rit.ui.widgets.comment_card import CommentCard

        patch = "@@ -1,1 +1,1 @@\n-old\n+new"

        class DraftService:
            async def create_pending_review(self, *args, **kwargs) -> PRReview:
                return PRReview(id=88)

        async with app.run_test() as pilot:
            screen = cast(MainScreen, app.screen)
            screen.switch_tab(1)
            screen.store.state.pr = PR(number=123, head_sha="deadbeef")
            screen.store.state.files = [
                PRFile(filename="one.py", status="modified", patch=patch),
                PRFile(filename="two.py", status="modified", patch=patch),
            ]
            screen.store.state.file_diffs = {
                "one.py": parse_patch(patch, "one.py"),
                "two.py": parse_patch(patch, "two.py"),
            }
            screen.store._service = DraftService()  # type: ignore[assignment]
            screen.file_changes.refresh_files()
            await pilot.pause()
            await pilot.pause()

            diff_view = screen.file_changes.diff_view
            assert diff_view.current_file == COMBINED_DIFF_FILENAME
            line_index = diff_view._line_index_by_file_new_number[("two.py", 1)]
            diff_view.cursor_line = line_index

            await screen._save_inline_comment_draft(
                "combined draft",
                path="two.py",
                line=1,
                side="RIGHT",
            )
            await pilot.pause()
            await pilot.pause()

            draft = diff_view.query_one("#pending-draft-3-right-0", CommentCard)
            assert draft._body == "combined draft"

    async def test_quit_action(self, app: RitApp) -> None:
        """Test that q quits the app."""
        async with app.run_test() as pilot:
            await pilot.press("q")
            assert not app.is_running
