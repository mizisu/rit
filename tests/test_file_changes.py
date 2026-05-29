"""Tests for file-selection behavior in the Files tab."""

import asyncio
import threading
from collections.abc import Sequence
from typing import Any, cast

import pytest
from textual.app import App, ComposeResult
from textual.signal import Signal

from rit.core.diff import parse_patch
from rit.state.models import PR, PRFile
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges


class FakeRawDiffService:
    def __init__(self, raw_diff: str) -> None:
        self.raw_diff = raw_diff
        self.calls: list[int] = []

    async def get_pr_diff_text(self, pr_number: int) -> str:
        self.calls.append(pr_number)
        return self.raw_diff


class FakeFilesService:
    def __init__(self, pages: dict[int, list[PRFile]]) -> None:
        self.pages = pages
        self.page_calls: list[tuple[int, int, int]] = []
        self.multi_page_calls: list[tuple[int, tuple[int, ...], int]] = []

    async def get_pr_files_page(
        self,
        pr_number: int,
        *,
        page: int,
        per_page: int = 100,
    ) -> list[PRFile]:
        self.page_calls.append((pr_number, page, per_page))
        return self.pages.get(page, [])

    async def get_pr_file_pages(
        self,
        pr_number: int,
        *,
        pages: Sequence[int],
        per_page: int = 100,
    ) -> dict[int, list[PRFile]]:
        self.multi_page_calls.append((pr_number, tuple(pages), per_page))
        return {page: self.pages.get(page, []) for page in pages}


def test_store_get_file_diff_parses_lazily_and_caches_status_metadata() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.state.files = [
        PRFile(
            filename="new.py",
            status="added",
            patch=patch,
            previousFilename="old.py",
        )
    ]

    diff = store.get_file_diff("new.py")

    assert diff is not None
    assert diff.filename == "new.py"
    assert diff.old_filename == "old.py"
    assert diff.is_new is True
    assert store.get_file_diff("new.py") is diff


@pytest.mark.asyncio
async def test_store_load_files_uses_raw_diff_for_complete_file_list() -> None:
    raw_diff = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1 +1 @@
-old
+new
diff --git a/two.py b/two.py
new file mode 100644
--- /dev/null
+++ b/two.py
@@ -0,0 +1 @@
+two
"""
    service = FakeRawDiffService(raw_diff)
    store = PRStore(pr_number=123)
    store._service = cast(Any, service)

    await store.load_files()

    assert service.calls == [123]
    assert [file.filename for file in store.state.files] == ["one.py", "two.py"]
    assert store.state.files[1].status == "added"
    assert set(store.state.file_diffs) == {"one.py", "two.py"}


@pytest.mark.asyncio
async def test_store_load_files_paints_first_page_then_concurrent_rest_without_eager_parsing() -> (
    None
):
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    first_page = [
        PRFile(filename=f"file-{index}.py", status="modified", patch=patch)
        for index in range(100)
    ]
    second_page = [PRFile(filename="file-100.py", status="modified", patch=patch)]
    service = FakeFilesService({1: first_page, 2: second_page})
    messages = []
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, changedFiles=101)
    store.state.files_total_count = 101
    store._service = cast(Any, service)
    store.set_message_sink(messages.append)

    await store.load_files()

    loaded_messages = [
        message for message in messages if isinstance(message, PRStore.FilesLoaded)
    ]
    assert service.page_calls == [(123, 1, 100)]
    assert service.multi_page_calls == [(123, (2,), 100)]
    assert [message.loaded_count for message in loaded_messages] == [100, 101]
    assert len(store.state.files) == 101
    assert store.state.file_diffs == {}
    assert store.state.files_by_filename["file-100.py"].filename == "file-100.py"


class DummySettings:
    def __init__(
        self,
        *,
        diff_mode: str = "auto",
        show_line_numbers: bool = True,
        word_diff: bool = True,
        theme: str = "catppuccin-macchiato",
    ) -> None:
        self.diff_mode = diff_mode
        self.show_line_numbers = show_line_numbers
        self.word_diff = word_diff
        self.theme = theme


@pytest.mark.asyncio
async def test_file_changes_applies_initial_diff_settings_from_app() -> None:
    store = PRStore()

    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.settings = DummySettings(
                diff_mode="unified",
                show_line_numbers=False,
                word_diff=False,
            )
            self.settings_changed_signal = Signal(self, "settings-changed")

        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        await pilot.pause()

        assert file_changes.diff_view.mode == "unified"
        assert file_changes.diff_view.show_line_numbers is False
        assert file_changes.diff_view.word_diff_enabled is False


@pytest.mark.asyncio
async def test_file_changes_updates_diff_settings_from_app_signal() -> None:
    store = PRStore()

    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.settings = DummySettings()
            self.settings_changed_signal = Signal(self, "settings-changed")

        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        await pilot.pause()

        app.settings_changed_signal.publish(("ui.diff_mode", "split", "auto"))
        app.settings_changed_signal.publish(("ui.show_line_numbers", False, True))
        app.settings_changed_signal.publish(("ui.word_diff", False, True))
        await pilot.pause()
        await pilot.pause()

        assert file_changes.diff_view.mode == "split"
        assert file_changes.diff_view.show_line_numbers is False
        assert file_changes.diff_view.word_diff_enabled is False


@pytest.mark.asyncio
async def test_theme_change_rehighlights_current_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -1,2 +1,2 @@\n-old\n+new"

    store = PRStore()
    store.state.file_diffs = {"one.py": parse_patch(patch, "one.py")}

    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.settings = DummySettings(theme="dracula")
            self.settings_changed_signal = Signal(self, "settings-changed")
            self.theme = "dracula"

        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()

    import rit.ui.widgets.diff_highlight as diff_highlight_module

    calls = {"count": 0}
    original = diff_highlight_module.highlight_lines_for_diff

    def counted_highlight(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        diff_highlight_module,
        "highlight_lines_for_diff",
        counted_highlight,
    )

    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        diff = store.state.file_diffs["one.py"]

        await file_changes.diff_view.show_diff("one.py", diff)
        await pilot.pause()
        await pilot.pause()

        baseline_calls = calls["count"]
        assert baseline_calls >= 1

        app.theme = "textual-light"
        app.settings_changed_signal.publish(("ui.theme", "textual-light", "dracula"))
        await pilot.pause()
        await pilot.pause()

        assert calls["count"] == baseline_calls + 1


@pytest.mark.asyncio
async def test_file_tree_shows_pending_draft_badge() -> None:
    store = PRStore()
    store.state.files = [PRFile(filename="one.py", status="modified")]
    store.save_pending_inline_comment(
        "hello pending",
        path="one.py",
        line=7,
        side="RIGHT",
    )

    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.settings = DummySettings()
            self.settings_changed_signal = Signal(self, "settings-changed")

        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        node = file_changes.file_tree._file_nodes["one.py"]
        label_text = getattr(node.label, "plain", str(node.label))
        assert "draft 1" in label_text


@pytest.mark.asyncio
async def test_file_changes_renders_loaded_file_diffs_as_combined_scroll() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
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
        await pilot.pause()
        await pilot.pause()

        assert file_changes.diff_view.current_file == "All files"
        assert file_changes._combined_file_line_starts == {"one.py": 0, "two.py": 2}
        assert file_changes.diff_view._diff is not None
        assert file_changes.diff_view._diff.hunks[0].starts_file is True
        assert file_changes.diff_view._diff.hunks[0].file_path == "one.py"
        assert file_changes.diff_view._diff.hunks[1].starts_file is True
        assert file_changes.diff_view._diff.hunks[1].file_path == "two.py"

        file_changes.file_tree.select_file("two.py")
        await pilot.pause()

        assert file_changes.diff_view.cursor_line == 2


@pytest.mark.asyncio
async def test_rapid_file_tree_selection_coalesces_to_latest_pending_diff() -> None:
    """Rapid file-tree selection should skip intermediate pending diff renders."""

    patch = "@@ -1,2 +1,2 @@\n-old\n+new"
    filenames = ["one.py", "two.py", "three.py"]

    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in filenames
    }
    store.state.selected_file = "one.py"

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        calls: list[str] = []
        started = threading.Event()
        unblock = threading.Event()

        async def blocking_show_diff(filename: str, diff) -> None:
            calls.append(filename)
            if len(calls) == 1:
                started.set()
                await asyncio.to_thread(unblock.wait, 1.0)

        file_changes.diff_view.show_diff = blocking_show_diff  # type: ignore[method-assign]

        file_changes.file_tree.select_file("one.py")
        await pilot.pause()
        assert started.wait(timeout=1.0) is True

        file_changes.file_tree.select_file("two.py")
        file_changes.file_tree.select_file("three.py")
        await pilot.pause()
        await pilot.pause()

        unblock.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert calls == ["one.py", "three.py"]
        assert file_changes.file_tree.selected_file == "three.py"
