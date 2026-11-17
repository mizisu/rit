from typing import Literal

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Static, Tree

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
from rit.ui.widgets.file_tree import FileTree
from tests.conftest import wait_until


@pytest.mark.parametrize("filename", ["one.py", "tests/test_one.py"])
def test_collapse_viewed_file_clears_manual_expansion(filename: str) -> None:
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, viewer_viewed_state=FileViewedState.VIEWED)
    ]
    diff_view = DiffView(store=store)
    diff_view.expand_file(filename)

    assert diff_view._should_collapse_file(filename) is False

    diff_view.collapse_viewed_file(filename)

    assert diff_view._should_collapse_file(filename) is True

    store.state.files[0].viewer_viewed_state = FileViewedState.UNVIEWED
    assert diff_view._should_collapse_file(filename) is False


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

        assert await diff_view.toggle_current_file_fold()
        content = diff_view._content_widget
        assert content is not None
        assert all(child.is_mounted for child in content.children)
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
            lambda: (
                len(diff_view.query("#file-header-0")) == 1
                and diff_view.query_one("#file-header-0").region.height == 1
            )
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
async def test_repeated_file_header_fold_preserves_scroll_position() -> None:
    lines_per_file = 10
    patch = f"@@ -1,{lines_per_file} +1,{lines_per_file} @@\n" + "\n".join(
        f" line{line}" for line in range(lines_per_file)
    )
    store = PRStore()
    store.state.files_loading = LoadingState.LOADED
    store.state.files = [
        PRFile(filename=f"file{index}.py", status="modified", patch=patch)
        for index in range(10)
    ]
    store.state.file_diffs = {
        file.filename: parse_patch(patch, file.filename) for file in store.state.files
    }

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test(size=(100, 20)) as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await wait_until(lambda: file_changes.diff_view.current_file == "All files")
        await pilot.pause()

        diff_view = file_changes.diff_view
        filename = "file5.py"
        line_index = diff_view._first_line_index_for_file(filename)
        assert line_index is not None
        diff_view.jump_to_line_index(
            line_index,
            side="RIGHT",
            viewport_offset=6,
        )
        await pilot.pause()
        initial_scroll = int(diff_view.scroll_y)
        assert initial_scroll > 0
        assert diff_view.selected_file_header_path() is None

        for _ in range(2):
            assert await diff_view.toggle_current_file_fold()
            await pilot.pause()
            assert int(diff_view.scroll_y) == initial_scroll

            assert await diff_view.toggle_current_file_fold()
            await pilot.pause()
            assert int(diff_view.scroll_y) == initial_scroll


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
        await wait_until(
            lambda: (
                file_changes.workspace_ready
                and len(file_changes.diff_view.query("CommentCard.pending-draft")) == 1
            ),
            timeout=2.0,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["split", "unified"])
async def test_test_diff_notice_reveals_in_place_and_remembers_expansion(
    mode: Literal["split", "unified"],
) -> None:
    patch = "@@ -1,2 +1,2 @@\n-old\n+new\n line2"
    filename = "tests/test_example.py"
    store = PRStore()
    store.state.files_loading = LoadingState.LOADED
    store.state.files = [
        PRFile(filename="app.py", status="modified", patch=patch),
        PRFile(filename=filename, status="modified", patch=patch),
        PRFile(
            filename="tests/test_viewed.py",
            status="modified",
            patch=patch,
            viewer_viewed_state=FileViewedState.VIEWED,
        ),
    ]
    store.state.file_diffs = {
        file.filename: parse_patch(patch, file.filename) for file in store.state.files
    }

    store.state.file_diffs["app.py"] = parse_patch(
        patch.replace(" line2", " " + "x" * 240), "app.py"
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test(size=(120, 24)) as pilot:
        changes = app.query_one(FileChanges)
        view = changes.diff_view
        view.mode = mode
        changes.refresh_files()
        await wait_until(lambda: view.current_file == "All files")
        await wait_until(lambda: not changes._file_render_worker_active)
        await pilot.pause()
        source = view.current_diff
        assert source is not None
        assert view._folded_file_paths == {filename, "tests/test_viewed.py"}
        assert view.line_index_for_location("app.py", 2, "RIGHT") is not None
        assert view.line_index_for_location(filename, 2, "RIGHT") is None
        assert len(view.query(".test-diff-notice")) == 1
        notice = view.query_one(".test-diff-notice")
        header = view.query_one("#file-header-1")
        button = view.query_one("#show-test-diff-1", Button)
        assert str(view.query_one(".test-diff-notice-text", Static).content) == (
            "Test files are not shown by default"
        )
        assert str(button.label) == "Show diff"
        assert header.region.height == 1
        assert notice.region.height == 4
        assert notice.region.y == header.region.y + 1
        assert button.region.y == header.region.y + 3
        assert button.region.height == 1
        button_center, _ = button.region.center
        viewport_center, _ = view.scrollable_content_region.center
        assert abs(button_center - viewport_center) <= 1
        assert view.query_one("#file-header-2").region.y == header.region.y + 5
        assert view._hunk_header_top_offsets[2] - view._hunk_header_top_offsets[1] == 5
        assert view._sticky_header.region.height == 1

        content = view._content_widget
        assert content is not None
        if mode == "unified":
            content.scroll_to(x=30, animate=False, immediate=True)
        else:
            view._sync_split_horizontal_scroll(30)
        await pilot.pause()
        assert abs(button.region.center[0] - viewport_center) <= 1
        assert notice.region.x == view.scrollable_content_region.x
        if mode == "unified":
            assert content.scroll_x == 30
            content.scroll_to(x=0, animate=False, immediate=True)
        else:
            view._sync_split_horizontal_scroll(0)
        await pilot.pause()

        folded_paths = view._folded_file_paths
        store.state.files[1].viewer_viewed_state = FileViewedState.VIEWED
        view.collapse_viewed_file(filename)
        changes.update_file_view_state(filename)
        await wait_until(
            lambda: not view._fold_worker_active and not view.query(".test-diff-notice")
        )
        await pilot.pause()
        assert view._folded_file_paths == folded_paths
        assert view.query_one("#file-header-2").region.y == (
            view.query_one("#file-header-1").region.y + 1
        )
        assert view._hunk_header_top_offsets[2] - view._hunk_header_top_offsets[1] == 1

        store.state.files[1].viewer_viewed_state = FileViewedState.UNVIEWED
        changes.update_file_view_state(filename)
        await wait_until(
            lambda: not view._fold_worker_active and view.query(".test-diff-notice")
        )
        await pilot.pause()
        assert view._folded_file_paths == folded_paths
        assert view.query_one("#file-header-2").region.y == (
            view.query_one("#file-header-1").region.y + 5
        )

        changes.file_tree.query_one("#file-tree", Tree).focus()
        changes.file_tree.post_message(FileTree.FilePreviewed(filename))
        await wait_until(lambda: view.selected_file_header_path() == filename)
        assert changes.file_tree.has_focus_within
        assert view._is_file_folded(filename)
        changes.file_tree.post_message(FileTree.FileSelected(filename))
        await wait_until(lambda: view.has_focus)
        assert view._is_file_folded(filename)

        view.jump_to_line_index(0, side="RIGHT", focus=True)
        await pilot.pause()
        assert view.file_for_line_index(view.cursor_line) == "app.py"
        scroll_y = view.scroll_y
        assert await pilot.click("#show-test-diff-1")
        await wait_until(
            lambda: not view._is_file_folded(filename) and not view._fold_worker_active
        )
        await pilot.pause()
        assert not view._is_file_folded(filename)
        assert view.file_for_line_index(view.cursor_line) == filename
        assert view.has_focus
        assert view.scroll_y == scroll_y
        assert not view.query(".test-diff-notice")
        assert view.current_diff is source
        assert store.state.files[1].viewer_viewed_state == FileViewedState.UNVIEWED

        await pilot.press("enter")
        await wait_until(
            lambda: view._is_file_folded(filename) and not view._fold_worker_active
        )
        await pilot.pause()
        assert not view._is_file_folded("app.py")
        assert not view.query(".test-diff-notice")
        assert view.query_one("#file-header-2").region.y == (
            view.query_one("#file-header-1").region.y + 1
        )
        await pilot.press("enter")
        await wait_until(lambda: not view._is_file_folded(filename))
        await wait_until(lambda: not view._fold_worker_active)

        store.state.files[1].viewer_viewed_state = FileViewedState.VIEWED
        view.collapse_viewed_file(filename)
        changes.update_file_view_state(filename)
        await wait_until(
            lambda: view._is_file_folded(filename) and not view._fold_worker_active
        )
        assert not view.query(".test-diff-notice")
        store.state.files[1].viewer_viewed_state = FileViewedState.UNVIEWED
        changes.update_file_view_state(filename)
        await wait_until(
            lambda: not view._is_file_folded(filename) and not view._fold_worker_active
        )
        assert not view.query(".test-diff-notice")

        changes.open_file("app.py", focus_diff=False)
        changes.file_tree.post_message(FileTree.FileSelected(filename))
        await wait_until(lambda: changes.current_diff_file_target() == filename)
        view.mode = "unified" if mode == "split" else "split"
        await pilot.resize_terminal(100, 24)
        await wait_until(lambda: view.split == (mode != "split"))
        await pilot.pause()
        assert not view._is_file_folded(filename)

        for folded in (False, True):
            if folded:
                view.reset_comparison()
                await view.show_diff("All files", source)
                assert view._is_file_folded(filename)
            assert await view.show_full_file_preview(
                filename,
                "new\nline2\nline3",
                source_diff=store.state.file_diffs[filename],
            )
            assert view.line_index_for_location(filename, 3, "RIGHT") is not None
            assert not view.query(".test-diff-notice")
            view.action_toggle_full_file()
            await wait_until(lambda: view.current_file == "All files")
            await wait_until(lambda: not view._showing_full_file)
            assert view._is_file_folded(filename) is folded
            await wait_until(
                lambda folded=folded: bool(view.query(".test-diff-notice")) is folded
            )

        changes.open_file(filename, focus_diff=True)
        await wait_until(lambda: not view._is_file_folded(filename))
        assert store.state.files[1].viewer_viewed_state == FileViewedState.UNVIEWED
