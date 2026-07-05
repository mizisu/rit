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
from rit.ui.widgets.diff_folding import FOLDED_VIEWED_FILE_MESSAGE
from tests.conftest import wait_until


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
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
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
        file_changes.refresh_files()
        await wait_until(lambda: file_changes.diff_view.current_file == "All files")

        diff_view = file_changes.diff_view
        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))

        assert header_text.startswith("▸ one.py")
        assert diff_view._folded_file_paths == frozenset({"one.py"})
        assert diff_view._all_lines[0].new_content == FOLDED_VIEWED_FILE_MESSAGE

        diff_view.focus()
        await pilot.press("enter")
        await wait_until(lambda: "one.py" not in diff_view._folded_file_paths)

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▾ one.py")
        assert all(
            line.new_content != FOLDED_VIEWED_FILE_MESSAGE
            for line in diff_view._all_lines
            if line.file_path == "one.py"
        )

        await pilot.press("enter")
        await wait_until(lambda: "one.py" in diff_view._folded_file_paths)

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▸ one.py")


@pytest.mark.asyncio
async def test_enter_toggles_unviewed_file_fold_in_combined_diff() -> None:
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

        diff_view.focus()
        await pilot.press("enter")
        await wait_until(lambda: "one.py" in diff_view._folded_file_paths)

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▸ one.py")
        assert diff_view._all_lines[0].new_content == FOLDED_VIEWED_FILE_MESSAGE

        await pilot.press("enter")
        await wait_until(lambda: "one.py" not in diff_view._folded_file_paths)

        first_header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))
        assert header_text.startswith("▾ one.py")


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
