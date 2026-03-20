"""Tests for file-selection behavior in the Files tab."""

import asyncio
import threading

import pytest
from textual.app import App, ComposeResult

from rit.core.diff import parse_patch
from rit.state.models import PRFile
from rit.state.store import PRStore
from rit.ui.components.file_changes import FileChanges


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
