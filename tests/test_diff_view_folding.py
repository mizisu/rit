import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.state.models import (
    FileViewedState,
    LoadingState,
    PRComment,
    PRFile,
    ReviewThread,
)
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges
from rit.ui.widgets.comment_card import CommentCard
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until


def test_collapse_viewed_file_clears_manual_expansion() -> None:
    filename = "one.py"
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, viewer_viewed_state=FileViewedState.VIEWED)
    ]
    diff_view = DiffView(store=store)
    diff_view._expanded_viewed_files.add(filename)

    assert diff_view._should_collapse_file(filename) is False

    diff_view.collapse_viewed_file(filename)

    assert diff_view._should_collapse_file(filename) is True


def _make_review_thread(path: str, *, root_id: int, line: int) -> ReviewThread:
    root = PRComment.model_validate(
        {
            "databaseId": root_id,
            "body": "comment",
            "path": path,
            "line": line,
            "originalLine": line,
            "side": "RIGHT",
        }
    )
    return ReviewThread.model_validate(
        {
            "path": path,
            "line": line,
            "originalLine": line,
            "comments": {"nodes": [root]},
        }
    )


@pytest.mark.asyncio
async def test_enter_toggles_viewed_file_fold_in_combined_diff() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old_value\n+new_value"
    store = PRStore()
    store.state.files_loading = LoadingState.LOADED
    store.state.files = [
        PRFile(
            filename="one.py",
            status="modified",
            patch=patch,
            viewer_viewed_state=FileViewedState.VIEWED,
        ),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in ["one.py", "two.py"]
    }

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.diff_view.mode = "split"
        file_changes.refresh_files()
        await wait_until(lambda: file_changes.diff_view.current_file == "All files")
        await wait_until(
            lambda: len(file_changes.diff_view.query("#file-header-0")) == 1
        )
        await wait_until(
            lambda: (
                len(file_changes.diff_view.query("#line-1-old")) == 1
                and len(file_changes.diff_view.query("#line-1-new")) == 1
            )
        )
        await wait_until(
            lambda: file_changes.diff_view.selected_file_header_path() == "one.py"
        )

        diff_view = file_changes.diff_view
        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))

        assert header_text.startswith("▸ one.py")
        assert diff_view._folded_file_paths == frozenset({"one.py"})
        assert diff_view._all_lines[0].is_folded_file_placeholder
        assert diff_view.selected_file_header_path() == "one.py"
        assert diff_view._line_heights[0] == 0
        assert diff_view.split is True
        assert len(diff_view.query("#line-0")) == 0
        assert len(diff_view.query("#line-0-old")) == 0
        assert len(diff_view.query("#line-0-new")) == 0
        assert len(diff_view.query("#line-1-old")) == 1
        assert len(diff_view.query("#line-1-new")) == 1

        diff_view.focus()
        await pilot.press("j")
        await wait_until(lambda: diff_view.selected_file_header_path() == "two.py")
        assert diff_view.cursor_line == 1
        await pilot.press("k")
        await wait_until(lambda: diff_view.selected_file_header_path() == "one.py")

        await pilot.press("enter")
        await wait_until(lambda: "one.py" not in diff_view._folded_file_paths)

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▾ one.py")
        assert all(
            not line.is_folded_file_placeholder
            for line in diff_view._all_lines
            if line.file_path == "one.py"
        )

        await pilot.press("enter")
        await wait_until(lambda: "one.py" in diff_view._folded_file_paths)
        await wait_until(lambda: diff_view.selected_file_header_path() == "one.py")

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▸ one.py")


@pytest.mark.asyncio
async def test_click_toggles_unviewed_file_fold_in_combined_diff() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.state.files_loading = LoadingState.LOADED
    store.state.files = [
        PRFile(filename="one.py", status="modified", patch=patch),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in ["one.py", "two.py"]
    }

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await wait_until(lambda: file_changes.diff_view.current_file == "All files")

        diff_view = file_changes.diff_view
        assert diff_view._folded_file_paths == frozenset()
        await wait_until(
            lambda: len(diff_view.query("#file-header-0")) == 1
            and diff_view.query_one("#file-header-0").region.height == 1
        )

        await pilot.click("#file-header-0", offset=(1, 0))
        await wait_until(lambda: "one.py" in diff_view._folded_file_paths)
        await wait_until(lambda: diff_view.selected_file_header_path() == "one.py")

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▸ one.py")
        assert diff_view._all_lines[0].is_folded_file_placeholder
        assert diff_view.selected_file_header_path() == "one.py"
        assert len(diff_view.query("#line-0")) == 0

        await pilot.press("enter")
        await wait_until(lambda: "one.py" not in diff_view._folded_file_paths)

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▾ one.py")


@pytest.mark.asyncio
async def test_enter_on_selected_pending_draft_does_not_fold_file() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.state.files_loading = LoadingState.LOADED
    store.state.files = [
        PRFile(filename="one.py", status="modified", patch=patch),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in ["one.py", "two.py"]
    }
    store.save_pending_inline_comment(
        "pending comment",
        path="one.py",
        line=1,
        side="RIGHT",
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await wait_until(lambda: file_changes.diff_view.current_file == "All files")
        await wait_until(
            lambda: len(file_changes.diff_view.query("CommentCard.pending-draft")) == 1
        )

        diff_view = file_changes.diff_view
        diff_view.cursor_line = diff_view._comment_line_indices[0]
        diff_view.focus()
        await pilot.press("j")
        await pilot.pause()

        draft = diff_view.query_one("CommentCard.pending-draft", CommentCard)
        assert diff_view._comment_cursor_index == 1
        assert "--cursor-line" in draft.classes
        assert draft.has_class("-collapsed") is False
        initial_height = draft.region.height

        await pilot.press("enter")
        await pilot.pause()

        assert diff_view._folded_file_paths == frozenset()
        assert diff_view._manually_folded_files == set()
        assert draft.is_mounted is True
        assert draft.has_class("-collapsed") is True
        assert draft.region.height < initial_height

        await pilot.press("enter")
        await pilot.pause()

        assert diff_view._folded_file_paths == frozenset()
        assert diff_view._manually_folded_files == set()
        assert draft.has_class("-collapsed") is False
        assert draft.region.height == initial_height


@pytest.mark.asyncio
async def test_folded_file_hides_inline_comments_until_expanded() -> None:
    patch = "@@ -1,2 +1,2 @@\n-old\n+new\n line2"
    store = PRStore()
    store.state.files_loading = LoadingState.LOADED
    store.state.files = [
        PRFile(
            filename="one.py",
            status="modified",
            patch=patch,
            viewer_viewed_state=FileViewedState.VIEWED,
        ),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in ["one.py", "two.py"]
    }
    store.state.review_threads.append(
        _make_review_thread("one.py", root_id=101, line=1)
    )
    store.state.review_threads.append(
        _make_review_thread("two.py", root_id=202, line=1)
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await wait_until(lambda: file_changes.diff_view.current_file == "All files")
        await pilot.pause()
        await pilot.pause()

        diff_view = file_changes.diff_view
        assert "one.py" in diff_view._folded_file_paths
        assert len(diff_view.query("#inline-thread-101")) == 0
        assert len(diff_view.query("#inline-thread-202")) == 1

        diff_view.focus()
        await pilot.press("enter")
        await wait_until(lambda: "one.py" not in diff_view._folded_file_paths)

        assert len(diff_view.query("#inline-thread-101")) == 1
