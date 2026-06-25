"""Tests for file-selection behavior in the Files tab."""

import asyncio
from collections.abc import Sequence
from typing import Any, cast

import pytest
from textual.app import App, ComposeResult
from textual.signal import Signal
from textual.widgets import Static

from rit.core.diff import parse_patch
from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.services.github import PRDiscussion
from rit.state.models import PR, LoadingState, PRFile
from rit.state.store import PRStore
from rit.ui.components import file_changes as file_changes_module
from rit.ui.components.combined_diff import CombinedDiffDocument
from rit.ui.components.file_changes import FileChanges
from rit.ui.widgets.diff_render import _create_file_header_widget
from rit.ui.widgets.diff_view import DiffView


class FakeRawDiffService:
    def __init__(self, raw_diff: str) -> None:
        self.raw_diff = raw_diff
        self.calls: list[int] = []

    async def get_pr_diff_text(self, pr_number: int) -> str:
        self.calls.append(pr_number)
        return self.raw_diff


class FakeStreamingRawDiffService:
    def __init__(self, sections: list[str]) -> None:
        self.sections = sections
        self.calls: list[int] = []
        self.first_section_loaded = asyncio.Event()
        self.continue_streaming = asyncio.Event()

    async def iter_pr_diff_sections(self, pr_number: int):
        self.calls.append(pr_number)
        yield self.sections[0]
        self.first_section_loaded.set()
        await self.continue_streaming.wait()
        for section in self.sections[1:]:
            yield section


class FakeSlowSummaryStreamingService:
    def __init__(self, section: str) -> None:
        self.section = section
        self.summary_requested = asyncio.Event()
        self.allow_summary = asyncio.Event()
        self.first_section_loaded = asyncio.Event()

    async def get_pr_summary(self, pr_number: int) -> PR:
        self.summary_requested.set()
        await self.allow_summary.wait()
        return PR(number=pr_number, changedFiles=1)

    async def get_pr_discussion(self, pr_number: int) -> PRDiscussion:
        return PRDiscussion(body="", reviews=[], issue_comments=[], review_threads=[])

    async def iter_pr_diff_sections(self, pr_number: int):
        yield self.section
        self.first_section_loaded.set()


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


class FakeFilesAndStreamingService(FakeFilesService):
    def __init__(self, pages: dict[int, list[PRFile]], section: str) -> None:
        super().__init__(pages)
        self.section = section
        self.stream_calls: list[int] = []

    async def iter_pr_diff_sections(self, pr_number: int):
        self.stream_calls.append(pr_number)
        yield self.section


class FakeEmptyFileSourcesService(FakeFilesService):
    def __init__(self) -> None:
        super().__init__({})
        self.stream_calls: list[int] = []
        self.raw_diff_calls: list[int] = []

    async def iter_pr_diff_sections(self, pr_number: int):
        self.stream_calls.append(pr_number)
        if False:
            yield ""

    async def get_pr_diff_text(self, pr_number: int) -> str:
        self.raw_diff_calls.append(pr_number)
        return ""


def test_combined_file_line_starts_reuses_document_map_without_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line_starts = {"one.py": 0}
    document = CombinedDiffDocument(
        diff=FileDiff(filename="All files"),
        file_line_starts=line_starts,
        file_start_lines=(0,),
        file_start_names=("one.py",),
        line_lookup={},
    )
    file_changes = FileChanges(PRStore())
    file_changes._render_session.record_combined_document(("one.py",), document)

    monkeypatch.setattr(
        file_changes_module,
        "dict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("combined line starts should not be copied per access")
        ),
        raising=False,
    )

    assert file_changes._combined_file_line_starts is line_starts


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


def test_sidebar_width_watch_ignores_missing_file_tree_before_mount() -> None:
    FileChanges(PRStore()).watch_sidebar_width(42)


def test_sidebar_width_watch_reraises_unexpected_style_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStyles:
        @property
        def width(self) -> int:
            return 35

        @width.setter
        def width(self, _value: int) -> None:
            raise RuntimeError("style update failed")

    class BrokenFileTree:
        styles = BrokenStyles()

    monkeypatch.setattr(
        FileChanges,
        "file_tree",
        property(lambda _self: BrokenFileTree()),
    )

    with pytest.raises(RuntimeError, match="style update failed"):
        FileChanges(PRStore()).watch_sidebar_width(42)


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
async def test_store_load_files_streams_first_raw_diff_file_before_completion() -> None:
    first_section = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1 +1 @@
-old
+new
"""
    second_section = """diff --git a/two.py b/two.py
--- a/two.py
+++ b/two.py
@@ -1 +1 @@
-old
+new
"""
    service = FakeStreamingRawDiffService([first_section, second_section])
    messages = []
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, changedFiles=2)
    store.state.files_total_count = 2
    store._service = cast(Any, service)
    store.set_message_sink(messages.append)

    task = asyncio.create_task(store.load_files())
    await asyncio.wait_for(service.first_section_loaded.wait(), timeout=1.0)

    loaded_messages = [
        message for message in messages if isinstance(message, PRStore.FilesLoaded)
    ]
    assert [message.loaded_count for message in loaded_messages] == [1]
    assert [file.filename for file in store.state.files] == ["one.py"]
    assert store.state.file_diffs == {}

    diff = store.get_file_diff("one.py")
    assert diff is not None
    assert diff.filename == "one.py"
    assert set(store.state.file_diffs) == {"one.py"}

    service.continue_streaming.set()
    await task

    loaded_messages = [
        message for message in messages if isinstance(message, PRStore.FilesLoaded)
    ]
    assert [message.loaded_count for message in loaded_messages] == [1, 2]
    assert [file.filename for file in store.state.files] == ["one.py", "two.py"]
    assert set(store.state.file_diffs) == {"one.py"}
    assert store.state.files[1].additions == 1
    assert store.state.files[1].deletions == 1


@pytest.mark.asyncio
async def test_store_load_all_starts_streaming_files_before_summary_finishes() -> None:
    section = """diff --git a/one.py b/one.py
--- a/one.py
+++ b/one.py
@@ -1 +1 @@
-old
+new
"""
    service = FakeSlowSummaryStreamingService(section)
    messages = []
    store = PRStore(pr_number=123)
    store._service = cast(Any, service)
    store.set_message_sink(messages.append)

    task = asyncio.create_task(store.load_all())
    await asyncio.wait_for(service.summary_requested.wait(), timeout=1.0)
    await asyncio.wait_for(service.first_section_loaded.wait(), timeout=1.0)

    loaded_messages = [
        message for message in messages if isinstance(message, PRStore.FilesLoaded)
    ]
    assert [message.loaded_count for message in loaded_messages] == [1]
    assert [file.filename for file in store.state.files] == ["one.py"]

    service.allow_summary.set()
    await task


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


@pytest.mark.asyncio
async def test_store_load_files_prefers_rest_files_before_raw_diff_stream() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    raw_section = """diff --git a/raw.py b/raw.py
--- a/raw.py
+++ b/raw.py
@@ -1 +1 @@
-old
+new
"""
    service = FakeFilesAndStreamingService(
        {1: [PRFile(filename="rest.py", status="modified", patch=patch)]},
        raw_section,
    )
    store = PRStore(pr_number=123)
    store._service = cast(Any, service)

    await store.load_files()

    assert service.page_calls == [(123, 1, 100)]
    assert service.stream_calls == []
    assert [file.filename for file in store.state.files] == ["rest.py"]


@pytest.mark.asyncio
async def test_store_load_files_fetches_rest_until_empty_when_total_unknown() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    first_page = [
        PRFile(filename=f"file-{index}.py", status="modified", patch=patch)
        for index in range(100)
    ]
    second_page = [PRFile(filename="file-100.py", status="modified", patch=patch)]
    service = FakeFilesService({1: first_page, 2: second_page})
    messages = []
    store = PRStore(pr_number=123)
    store._service = cast(Any, service)
    store.set_message_sink(messages.append)

    await store.load_files()

    loaded_messages = [
        message for message in messages if isinstance(message, PRStore.FilesLoaded)
    ]
    assert service.page_calls == [(123, 1, 100)]
    assert service.multi_page_calls == [(123, tuple(range(2, 8)), 100)]
    assert [message.loaded_count for message in loaded_messages] == [100, 101]
    assert len(store.state.files) == 101


@pytest.mark.asyncio
async def test_store_load_files_switches_to_raw_stream_when_rest_limit_is_exceeded() -> (
    None
):
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    raw_section = """diff --git a/raw.py b/raw.py
--- a/raw.py
+++ b/raw.py
@@ -1 +1 @@
-old
+new
"""
    first_page = [
        PRFile(filename=f"file-{index}.py", status="modified", patch=patch)
        for index in range(100)
    ]
    service = FakeFilesAndStreamingService({1: first_page}, raw_section)
    store = PRStore(pr_number=123)
    store.state.pr = PR(number=123, changedFiles=3001)
    store.state.files_total_count = 3001
    store._service = cast(Any, service)

    await store.load_files()

    assert service.page_calls == [(123, 1, 100)]
    assert service.multi_page_calls == []
    assert service.stream_calls == [123]
    assert store.state.files[-1].filename == "raw.py"


@pytest.mark.asyncio
async def test_store_load_files_marks_error_when_all_file_sources_are_empty() -> None:
    service = FakeEmptyFileSourcesService()
    messages = []
    store = PRStore(pr_number=123)
    store._service = cast(Any, service)
    store.set_message_sink(messages.append)

    await store.load_files()

    assert service.page_calls == [(123, 1, 100)]
    assert service.stream_calls == [123]
    assert service.raw_diff_calls == [123]
    assert store.state.files_loading == LoadingState.ERROR
    assert store.state.error == "No changed files could be loaded"
    assert messages[-1] == PRStore.ErrorOccurred(
        error="No changed files could be loaded",
        source="load_files",
    )


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
async def test_file_changes_waits_for_files_to_finish_before_combined_diff() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.state.files_loading = LoadingState.LOADING
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

        assert file_changes.diff_view.current_file == "one.py"

        store.state.files_loading = LoadingState.LOADED
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        assert file_changes.diff_view.current_file == "All files"


@pytest.mark.asyncio
async def test_combined_diff_uses_prominent_file_headers_without_hunk_headers() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.state.files = [
        PRFile(
            filename="one.py",
            status="modified",
            patch=patch,
            additions=12,
            deletions=3,
        ),
        PRFile(
            filename="two.py",
            status="modified",
            patch=patch,
            additions=4,
            deletions=1,
        ),
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

        assert len(file_changes.diff_view.query(".hunk-header")) == 0

        first_header = file_changes.diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(first_header.content, "plain", first_header.content))

        assert first_header.outer_size.height == 1
        assert "one.py" in header_text
        assert "+12" in header_text
        assert "-3" in header_text
        assert "Unviewed" not in header_text
        assert "Modified" not in header_text
        assert "[M]" not in header_text
        assert "@@" not in header_text
        assert "auto" not in header_text.lower()
        assert "split" not in header_text.lower()
        assert "hunk" not in header_text.lower()


def test_file_header_prefers_current_file_stats_over_stale_hunk_metadata() -> None:
    store = PRStore()
    store.state.files = [
        PRFile(
            filename="one.py",
            status="modified",
            additions=12,
            deletions=3,
        )
    ]
    view = DiffView(store=store)
    view.split = False

    header = _create_file_header_widget(
        view,
        hunk_index=0,
        hunk=DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            starts_file=True,
            file_path="one.py",
            file_additions=12,
            file_deletions=0,
        ),
    )

    assert isinstance(header, Static)
    header_text = str(getattr(header.content, "plain", header.content))
    assert "+12" in header_text
    assert "-3" in header_text


def test_file_header_prefers_visible_file_list_over_stale_lookup_cache() -> None:
    store = PRStore()
    store.state.files = [
        PRFile(
            filename="one.py",
            status="modified",
            additions=12,
            deletions=3,
        )
    ]
    store.state.files_by_filename["one.py"] = PRFile(
        filename="one.py",
        status="modified",
        additions=12,
        deletions=0,
    )
    view = DiffView(store=store)
    view.split = False

    header = _create_file_header_widget(
        view,
        hunk_index=0,
        hunk=DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            starts_file=True,
            file_path="one.py",
            file_additions=12,
            file_deletions=0,
        ),
    )

    assert isinstance(header, Static)
    header_text = str(getattr(header.content, "plain", header.content))
    assert "+12" in header_text
    assert "-3" in header_text


def test_file_header_reuses_current_view_file_without_store_scan() -> None:
    class NoIterFiles(list[PRFile]):
        def __iter__(self):
            raise AssertionError("file header should reuse the current view file")

    store = PRStore()
    store.state.files = NoIterFiles()
    file = PRFile(
        filename="one.py",
        status="modified",
        additions=12,
        deletions=3,
    )
    view = DiffView(store=store)
    view.split = False
    view._file = file

    header = _create_file_header_widget(
        view,
        hunk_index=0,
        hunk=DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            starts_file=True,
            file_path="one.py",
            file_additions=0,
            file_deletions=0,
        ),
    )

    assert isinstance(header, Static)
    header_text = str(getattr(header.content, "plain", header.content))
    assert "+12" in header_text
    assert "-3" in header_text


def test_file_header_uses_planned_change_stats_without_diff_scan() -> None:
    class NoIterHunks(list):
        def __iter__(self):
            raise AssertionError("file header should reuse planned change stats")

    view = DiffView(store=PRStore())
    view.split = False
    view._diff = FileDiff(filename="All files", hunks=NoIterHunks())
    view._file_change_stats = {"one.py": (2, 1)}

    header = _create_file_header_widget(
        view,
        hunk_index=0,
        hunk=DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            starts_file=True,
            file_path="one.py",
        ),
    )

    assert isinstance(header, Static)
    header_text = str(getattr(header.content, "plain", header.content))
    assert "+2" in header_text
    assert "-1" in header_text


def test_file_header_width_accounts_for_rename_display_path_without_viewport() -> None:
    view = DiffView(store=PRStore())
    view.split = False

    header = _create_file_header_widget(
        view,
        hunk_index=0,
        hunk=DiffHunk(
            old_start=1,
            old_count=1,
            new_start=1,
            new_count=1,
            starts_file=True,
            file_path="new.py",
            file_old_path="old/location.py",
            file_additions=1,
            file_deletions=0,
        ),
    )

    assert isinstance(header, Static)
    header_text = str(getattr(header.content, "plain", header.content))
    assert header_text == "▾ +1 old/location.py -> new.py"


@pytest.mark.asyncio
async def test_file_header_keeps_change_counts_visible_in_narrow_panes() -> None:
    store = PRStore()
    store.state.files = [
        PRFile(
            filename="lemonbase/common/openapi.py",
            status="modified",
            additions=95,
            deletions=100,
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test(size=(72, 48)) as pilot:
        diff_view = app.query_one(FileChanges).diff_view
        diff = FileDiff(
            filename="All files",
            show_hunk_headers=False,
            hunks=[
                DiffHunk(
                    old_start=1,
                    old_count=1,
                    new_start=1,
                    new_count=1,
                    starts_file=True,
                    file_path="lemonbase/common/openapi.py",
                    file_status="modified",
                    file_additions=0,
                    file_deletions=100,
                    lines=[
                        DiffLine(
                            old_line_no=1,
                            new_line_no=None,
                            is_deleted=True,
                            file_path="lemonbase/common/openapi.py",
                        ),
                        DiffLine(
                            old_line_no=None,
                            new_line_no=1,
                            is_added=True,
                            file_path="lemonbase/common/openapi.py",
                        ),
                    ],
                )
            ],
        )

        await diff_view.show_diff("All files", diff)
        await pilot.pause()
        await pilot.pause()

        header = diff_view.query_one("#file-header-0", Static)
        header_text = str(getattr(header.content, "plain", header.content))

        assert header.outer_size.height == 1
        assert header_text.startswith("▾ -100 +95 ")
        assert "openapi.py" in header_text


@pytest.mark.asyncio
async def test_file_changes_loads_lazy_file_diffs_as_combined_scroll() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.state.files = [
        PRFile(filename="one.py", status="modified", patch=patch),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert file_changes.diff_view.current_file == "All files"
        assert file_changes._showing_combined_files is True
        assert file_changes._combined_file_line_starts == {"one.py": 0, "two.py": 2}
        assert set(store.state.file_diffs) == {"one.py", "two.py"}


@pytest.mark.asyncio
async def test_open_file_during_combined_load_does_not_render_single_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    one_requested = asyncio.Event()
    release_one = asyncio.Event()

    async def delayed_get_file_diff(filename: str) -> FileDiff:
        if filename == "one.py":
            one_requested.set()
            await release_one.wait()
        diff = parse_patch(patch, filename)
        store.state.file_diffs[filename] = diff
        return diff

    monkeypatch.setattr(store, "get_file_diff_async", delayed_get_file_diff)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await asyncio.wait_for(one_requested.wait(), timeout=1.0)
        await pilot.pause()

        calls: list[str] = []
        original_show_diff = file_changes.diff_view.show_diff

        async def counted_show_diff(filename: str, diff, **kwargs) -> None:
            calls.append(filename)
            await original_show_diff(filename, diff, **kwargs)

        file_changes.diff_view.show_diff = counted_show_diff  # type: ignore[method-assign]

        file_changes.open_file("two.py", focus_diff=True)
        await pilot.pause()
        await pilot.pause()

        assert calls == []

        release_one.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert calls == ["All files"]
        assert file_changes.diff_view.current_file == "All files"
        assert file_changes.diff_view.cursor_line == 2
        assert store.state.selected_file == "two.py"


@pytest.mark.asyncio
async def test_file_changes_renders_combined_scroll_without_line_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rit.ui.components.file_changes as file_changes_module

    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    store = PRStore()
    store.state.files = [
        PRFile(filename="one.py", status="modified", patch=patch),
        PRFile(filename="two.py", status="modified", patch=patch),
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in ["one.py", "two.py"]
    }
    monkeypatch.setattr(
        file_changes_module,
        "COMBINED_DIFF_LINE_THRESHOLD",
        1,
        raising=False,
    )

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
        assert file_changes._showing_combined_files is True
        assert file_changes._combined_file_line_starts == {"one.py": 0, "two.py": 2}
        assert store.state.selected_file == "one.py"


@pytest.mark.asyncio
async def test_combined_hunk_navigation_syncs_current_file_selection() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
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

        assert store.state.selected_file == "one.py"

        file_changes.next_hunk()
        await pilot.pause()

        assert file_changes.diff_view.cursor_line == 2
        assert store.state.selected_file == "two.py"
        assert file_changes.file_tree.selected_file == "two.py"


@pytest.mark.asyncio
async def test_combined_full_preview_uses_current_line_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in filenames
    }
    content_requests: list[str] = []

    async def fake_get_file_content(filename: str) -> str:
        content_requests.append(filename)
        return "new\nrestored context"

    monkeypatch.setattr(store, "get_file_content", fake_get_file_content)

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
        assert file_changes._showing_combined_files is True

        file_changes.diff_view.action_toggle_full_file()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert content_requests == ["one.py"]
        assert file_changes.diff_view.current_file == "one.py"
        assert file_changes.diff_view._showing_full_file is True
        assert file_changes._showing_combined_files is False
        assert store.state.selected_file == "one.py"
        prefix_texts = [
            str(getattr(prefix.content, "plain", prefix.content))
            for prefix in (
                cast(Static, node)
                for node in file_changes.diff_view.query(".line-prefix")
            )
        ]
        assert len(file_changes.diff_view.query(".preview-hunk-boundary")) == 0
        assert any("┃" in text for text in prefix_texts)


@pytest.mark.asyncio
async def test_combined_full_preview_opens_at_current_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -2,1 +2,1 @@\n-old\n+line 2"
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in filenames
    }
    content_requests: list[str] = []

    async def fake_get_file_content(filename: str) -> str:
        content_requests.append(filename)
        return "\n".join(f"line {line}" for line in range(1, 6))

    monkeypatch.setattr(store, "get_file_content", fake_get_file_content)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        assert file_changes.jump_to_file_location(
            "two.py",
            2,
            "RIGHT",
            focus_diff=False,
        )
        await pilot.pause()

        file_changes.diff_view.action_toggle_full_file()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        current = file_changes.diff_view._current_line()
        assert content_requests == ["two.py"]
        assert file_changes.diff_view.current_file == "two.py"
        assert current is not None
        assert current.new_line_no == 2


@pytest.mark.asyncio
async def test_combined_full_preview_restore_returns_to_original_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = """@@ -1,4 +1,4 @@
 line 1
-line 2 old
+line 2
 line 3
-line 4 old
+line 4"""
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in filenames
    }

    async def fake_get_file_content(filename: str) -> str:
        return "\n".join(f"line {line}" for line in range(1, 6))

    monkeypatch.setattr(store, "get_file_content", fake_get_file_content)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        assert file_changes.jump_to_file_location(
            "two.py",
            4,
            "RIGHT",
            focus_diff=False,
        )
        await pilot.pause()

        file_changes.diff_view.action_toggle_full_file()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        file_changes.diff_view.action_toggle_full_file()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        current = file_changes.diff_view._current_line()
        assert file_changes.diff_view.current_file == "All files"
        assert current is not None
        assert current.file_path == "two.py"
        assert current.new_line_no == 4


@pytest.mark.asyncio
async def test_combined_full_preview_restores_combined_scroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in filenames
    }

    async def fake_get_file_content(filename: str) -> str:
        return "new\nrestored context"

    monkeypatch.setattr(store, "get_file_content", fake_get_file_content)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        file_changes.diff_view.action_toggle_full_file()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        file_changes.diff_view.action_toggle_full_file()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert file_changes.diff_view.current_file == "All files"
        assert file_changes._showing_combined_files is True

        calls: list[str] = []

        async def counted_show_diff(filename: str, diff) -> None:
            calls.append(filename)

        file_changes.diff_view.show_diff = counted_show_diff  # type: ignore[method-assign]

        file_changes.file_tree.select_file("two.py")
        await pilot.pause()

        assert calls == []
        assert file_changes.diff_view.cursor_line == 2
        assert store.state.selected_file == "two.py"


@pytest.mark.asyncio
async def test_combined_location_jump_uses_cached_line_lookup() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in filenames
    }

    class ExplodingLines(list):
        def __iter__(self):
            raise AssertionError("line lookup should not scan hunk lines")

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await pilot.pause()
        await pilot.pause()

        diff = file_changes.diff_view._diff
        assert diff is not None
        diff.hunks[1].lines = cast(Any, ExplodingLines(diff.hunks[1].lines))

        assert (
            file_changes.jump_to_file_location(
                "two.py",
                1,
                "RIGHT",
                focus_diff=False,
            )
            is True
        )

        line = file_changes.diff_view._current_line()
        assert line is not None
        assert line.new_line_no == 1
        assert store.state.selected_file == "two.py"


@pytest.mark.asyncio
async def test_location_jump_during_combined_load_does_not_render_single_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    one_requested = asyncio.Event()
    release_one = asyncio.Event()

    async def delayed_get_file_diff(filename: str) -> FileDiff:
        if filename == "one.py":
            one_requested.set()
            await release_one.wait()
        diff = parse_patch(patch, filename)
        store.state.file_diffs[filename] = diff
        return diff

    monkeypatch.setattr(store, "get_file_diff_async", delayed_get_file_diff)

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileChanges(store=store)

    app = TestApp()
    async with app.run_test() as pilot:
        file_changes = app.query_one(FileChanges)
        file_changes.refresh_files()
        await asyncio.wait_for(one_requested.wait(), timeout=1.0)
        await pilot.pause()

        calls: list[str] = []
        original_show_diff = file_changes.diff_view.show_diff

        async def counted_show_diff(filename: str, diff, **kwargs) -> None:
            calls.append(filename)
            await original_show_diff(filename, diff, **kwargs)

        file_changes.diff_view.show_diff = counted_show_diff  # type: ignore[method-assign]

        assert (
            file_changes.jump_to_file_location(
                "two.py",
                1,
                "RIGHT",
                focus_diff=True,
            )
            is True
        )
        await pilot.pause()
        await pilot.pause()

        assert calls == []

        release_one.set()
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        assert calls == ["All files"]
        assert file_changes.diff_view.current_file == "All files"
        line = file_changes.diff_view._current_line()
        assert line is not None
        assert line.file_path == "two.py"
        assert line.new_line_no == 1
        assert store.state.selected_file == "two.py"


@pytest.mark.asyncio
async def test_combined_inline_comment_target_uses_line_file_path() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
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

        assert (
            file_changes.jump_to_file_location(
                "two.py",
                1,
                "RIGHT",
                focus_diff=False,
            )
            is True
        )

        assert file_changes.diff_view._inline_comment_target_for_current_line() == (
            "two.py",
            1,
            "RIGHT",
        )


@pytest.mark.asyncio
async def test_combined_diff_maps_pending_drafts_by_file_path() -> None:
    patch = "@@ -1,1 +1,1 @@\n-old\n+new"
    filenames = ["one.py", "two.py"]
    store = PRStore()
    store.state.files = [
        PRFile(filename=filename, status="modified", patch=patch)
        for filename in filenames
    ]
    store.state.file_diffs = {
        filename: parse_patch(patch, filename) for filename in filenames
    }
    store.save_pending_inline_comment(
        "draft for two",
        path="two.py",
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
        await pilot.pause()
        await pilot.pause()

        assert sorted(file_changes.diff_view._pending_comment_drafts_by_line) == [3]


@pytest.mark.asyncio
async def test_rapid_file_tree_selection_in_combined_scroll_jumps_without_rerender() -> (
    None
):
    """Rapid file-tree selection should jump inside the combined diff."""

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

        async def blocking_show_diff(filename: str, diff) -> None:
            calls.append(filename)

        file_changes.diff_view.show_diff = blocking_show_diff  # type: ignore[method-assign]

        file_changes.file_tree.select_file("two.py")
        file_changes.file_tree.select_file("three.py")
        await pilot.pause()
        await pilot.pause()

        assert calls == []
        assert file_changes.diff_view.cursor_line == 4
        assert file_changes.file_tree.selected_file == "three.py"


@pytest.mark.asyncio
async def test_open_file_focuses_current_diff_without_rerender() -> None:
    patch = "@@ -1,2 +1,2 @@\n-old\n+new"
    store = PRStore()
    store.state.files = [PRFile(filename="one.py", status="modified", patch=patch)]
    store.state.file_diffs = {"one.py": parse_patch(patch, "one.py")}
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

        await file_changes.diff_view.show_diff(
            "one.py",
            store.state.file_diffs["one.py"],
        )
        file_changes.file_tree.focus()
        await pilot.pause()

        calls: list[str] = []

        async def counted_show_diff(filename: str, diff) -> None:
            calls.append(filename)

        file_changes.diff_view.show_diff = counted_show_diff  # type: ignore[method-assign]

        file_changes.open_file("one.py", focus_diff=True)
        await pilot.pause()

        assert calls == []
        assert file_changes.diff_view.has_focus
        assert store.state.selected_file == "one.py"
