"""Tests for file-selection behavior in the Files tab."""

import asyncio
import threading

import pytest
from textual.app import App, ComposeResult
from textual.signal import Signal

from rit.core.diff import parse_patch
from rit.state.models import PRFile
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges


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
        assert "draft 1" in node.label.plain


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
