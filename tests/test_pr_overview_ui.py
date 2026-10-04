"""High-value overview UI and first-entry diff navigation coverage."""

import asyncio
from typing import cast

import pytest
from rich.color import Color
from textual import on
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Button, Static

from rit.app import RitApp
from rit.state.file_ingest import begin_file_ingest
from rit.state.models import PR, FileViewedState, LoadingState, PRFile
from rit.state.pr_overview import (
    PRCheck,
    PRChecksSnapshot,
    PRFileMetadata,
    PRFilesSnapshot,
)
from rit.state.store import PRStore
from rit.ui.components.pr_info import PRInfo
from rit.ui.components.pr_overview import FileGroup, PRChecks, SummaryOptionList
from rit.ui.screens.main import MainScreen
from tests.conftest import wait_until


async def test_large_file_summary_reveals_only_requested_rows_and_reuses_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=1, title="Large PR")
    special_paths = (
        "src/collapsible_markdown_with_a_very_long_filename.py",
        "src/name with spaces.py",
        "src/한글과🧪아주긴파일이름.py",
        "package_a/config.py",
        "package_b/config.py",
    )
    paths = special_paths + tuple(f"src/file-{index}.py" for index in range(4995))
    files = tuple(
        PRFile(
            filename=path,
            additions=12345 if index == 0 else 1,
            deletions=6789 if index == 0 else 0,
            status=("added", "removed", "renamed", "copied", "modified")[index % 5],
        )
        for index, path in enumerate(paths)
    )
    store.state.file_summary.value = PRFilesSnapshot.from_metadata(
        PRFileMetadata("base", "head", files)
    )
    store.state.file_summary.loading = LoadingState.LOADED
    store.state.checks.value = PRChecksSnapshot(
        "head", "SUCCESS", (PRCheck("ci", "CI", "success", "SUCCESS"),)
    )
    store.state.checks.loading = LoadingState.LOADED

    def unexpected_diff(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("overview rendering must not load diffs")

    monkeypatch.setattr(store, "get_file_diff", unexpected_diff)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PRInfo(store)

    app = TestApp()
    async with app.run_test(size=(150, 40)) as pilot:
        info = app.query_one(PRInfo)
        info.refresh_overview()
        await pilot.pause()
        group = app.query_one("#pr-file-group-implementation", FileGroup)
        options = group.query_one(SummaryOptionList)
        assert options.option_count == 6
        assert len(list(options.children)) == 0
        assert len(list(app.query("*"))) < 100
        assert options.virtual_size.height == options.option_count
        original = [options.get_option_at_index(index) for index in range(5)]
        info.focus_main()
        await pilot.pause()
        idle_border = info.query_one("#sidebar").styles.border.left[1]
        options.focus()
        options.highlighted = 0
        await pilot.pause()
        assert not options.styles.border.top[0]
        assert not options.styles.border.left[0]
        assert not options.get_component_styles(
            "option-list--option-highlighted"
        ).text_style.reverse
        selected = options.get_visual_style(
            "option-list--option", "option-list--option-highlighted"
        )
        normal = options.get_visual_style("option-list--option")
        assert selected.foreground == normal.foreground
        assert selected.background != normal.background
        assert "+12345 -6789" in options.render_line(0).text
        assert "…" in options.render_line(0).text
        assert options.tooltip is not None
        assert special_paths[0] in str(options.tooltip)
        heading = group.query_one(Button)
        assert heading.styles.text_align == "left"
        assert not heading.styles.text_style.reverse
        count = group.query_one(".file-group-count", Static)
        totals = group.query_one(".file-group-totals", Static)
        assert info.query_one("#sidebar").region.width == 44
        assert totals.parent is count.parent is heading.parent
        assert totals.region.y == count.region.y == heading.region.y
        assert totals.region.height == count.region.height == heading.region.height == 1
        assert heading.region.right <= count.region.x
        assert count.region.right < totals.region.x
        assert totals.region.right <= group.content_region.right
        assert options.region.y == heading.region.bottom
        assert "+17344 -6789" in str(totals.content)
        assert info.query_one("#sidebar").styles.border.left[1] != idle_border
        options.highlighted = options.get_option_index("more")
        await pilot.press("enter")
        await wait_until(lambda: options.option_count == 26)
        assert all(
            options.get_option_at_index(index) is option
            for index, option in enumerate(original)
        )
        revealed = options.get_option_at_index(20)
        options.highlighted = 20
        info.focus_main()
        info.focus_sidebar()
        await pilot.pause()
        assert options.has_focus and options.highlighted == 20
        assert options.get_option_at_index(20) is revealed

        group.query_one(Button).press()
        await wait_until(lambda: not options.display)
        group.query_one(Button).press()
        await wait_until(lambda: options.display)
        assert options.option_count == 26
        assert options.get_option_at_index(20) is revealed

        checks = app.query_one(PRChecks).query_one(SummaryOptionList)
        store.state.checks.value = PRChecksSnapshot(
            "head",
            "SUCCESS",
            (
                PRCheck("ci", "CI", "success", "SUCCESS"),
                PRCheck("optional", "Optional job", "neutral", "SKIPPED"),
            ),
        )
        info.refresh_overview()
        for focused in (False, True):
            checks.focus() if focused else info.focus_main()
            await pilot.pause()
            segments = [
                segment
                for segment in checks.render_line(0)
                if "Checks passed" in segment.text
            ]
            assert len(segments) == 1
            style = segments[0].style
            assert style is not None and style.color == Color.parse("#a6da95")

        store.state.checks.value = PRChecksSnapshot(
            "head", "PENDING", (PRCheck("ci", "CI", "pending", "IN_PROGRESS"),)
        )
        info.refresh_overview()
        assert options.get_option_at_index(20) is revealed
        checks = app.query_one(PRChecks).query_one(SummaryOptionList)
        assert "running" in str(checks.get_option_at_index(0).prompt)
        checks.highlighted = 0
        checks.action_select()
        await wait_until(lambda: checks.option_count == 2)
        store.state.checks.value = PRChecksSnapshot("head", None)
        info.refresh_overview()
        assert checks.option_count == 1
        assert checks.disabled
        assert checks not in app.screen.focus_chain
        assert "All passed" not in str(checks.get_option_at_index(0).prompt)

        for width in (100, 80, 40):
            await pilot.resize_terminal(width, 30)
            await pilot.pause()
            sidebar = info.query_one("#sidebar", VerticalScroll)
            main = info.query_one("#main-scroll", VerticalScroll)
            assert options.virtual_size.height == options.option_count
            assert all(
                options.render_line(index).cell_length
                <= options.scrollable_content_region.width
                for index in range(3)
            )
            assert sidebar.region.right <= info.region.right
            assert totals.region.y == count.region.y == heading.region.y
            assert totals.region.height == 1
            assert heading.region.right <= count.region.x
            assert count.region.right < totals.region.x
            assert totals.region.right <= group.content_region.right
            if width == 100:
                assert sidebar.region.width == 38
            if width < 94:
                assert 0 < sidebar.region.height <= 14
                assert main.region.height > sidebar.region.height
                assert sidebar.region.y >= main.region.bottom


async def test_single_category_and_mixed_prs_preserve_choices_and_handle_empty_errors() -> (
    None
):
    store = PRStore()
    store.state.pr = PR(number=1, title="Different PR shapes")

    class TestApp(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.opened: list[str] = []

        def compose(self) -> ComposeResult:
            yield PRInfo(store)

        @on(FileGroup.OpenFileRequested)
        def file_requested(self, event: FileGroup.OpenFileRequested) -> None:
            self.opened.append(event.filename)

    app = TestApp()
    async with app.run_test(size=(150, 40)) as pilot:
        info = app.query_one(PRInfo)

        def show(files: tuple[PRFile, ...]) -> None:
            store.state.file_summary.value = PRFilesSnapshot.from_metadata(
                PRFileMetadata("base", "head", files)
            )
            store.state.file_summary.loading = LoadingState.LOADED
            store.state.file_summary.error = None
            info.refresh_overview()

        docs = tuple(PRFile(filename=f"docs/guide-{index}.md") for index in range(30))
        show(docs)
        await pilot.pause()
        documentation = app.query_one("#pr-file-group-documentation", FileGroup)
        doc_options = documentation.query_one(SummaryOptionList)
        assert doc_options.display and doc_options.option_count == 6
        heading = documentation.query_one(Button)
        heading.focus()
        await pilot.pause()
        assert not heading.styles.border.top[0]
        assert not heading.styles.border.left[0]
        assert not heading.styles.text_style.reverse
        heading.press()
        await wait_until(lambda: not doc_options.display)
        show(docs + (PRFile(filename="docs/new.md"),))
        await pilot.pause()
        assert not doc_options.display
        show(
            tuple(PRFile(filename=f"api/service-{index}.pb.go") for index in range(30))
        )
        await pilot.pause()
        generated = app.query_one("#pr-file-group-generated", FileGroup)
        assert generated.query_one(SummaryOptionList).display
        assert "filename conventions" in str(generated.query_one(Button).tooltip)

        show(
            (
                PRFile(filename="pkg_a/config.py", additions=5, deletions=2),
                PRFile(filename="pkg_b/config.py", status="added", additions=3),
                PRFile(filename="assets/image.png", status="added"),
                PRFile(
                    filename="src/new_name.py",
                    status="renamed",
                    previous_filename="src/old_name.py",
                ),
                PRFile(filename="src/removed.py", status="removed", deletions=9),
                PRFile(filename="docs/guide.md"),
                PRFile(filename="src/url_test.go", additions=1),
                PRFile(filename="Cargo.lock", additions=1),
            )
        )
        await pilot.pause()
        visible = [group for group in app.query(FileGroup) if group.display]
        assert len(visible) == 4
        implementation = app.query_one("#pr-file-group-implementation", FileGroup)
        options = implementation.query_one(SummaryOptionList)
        assert options.option_count == 5 and options.virtual_size.height == 5
        options.focus()
        options.highlighted = options.get_option_index("file:src/new_name.py")
        await pilot.pause()
        assert "src/old_name.py -> src/new_name.py" in str(options.tooltip)
        await pilot.press("enter")
        await wait_until(lambda: app.opened == ["src/new_name.py"])
        show(())
        await pilot.pause()
        status = app.query_one("#pr-files-summary-status", Static)
        assert status.display and "No changed files" in str(status.content)
        assert not any(group.display for group in app.query(FileGroup))
        store.state.file_summary.loading = LoadingState.ERROR
        store.state.file_summary.error = "permission denied"
        info.refresh_overview()
        assert "Unable to load files" in str(status.content)
        assert "permission denied" in str(status.tooltip)
        store.state.file_summary.loading = LoadingState.LOADING
        store.state.file_summary.error = None
        info.refresh_overview()
        assert app.query_one("#refresh-file-summary", Button).disabled
        assert "Loading" in str(status.content)


async def test_shift_focus_and_first_file_selection_wait_for_lazy_diff_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []
    patch = "@@ -1 +1 @@\n-old\n+new"

    async def skip_overview(_store: PRStore) -> None:
        return None

    async def load_files(store: PRStore) -> None:
        calls.append("files")
        begin_file_ingest(store.state)
        started.set()
        await release.wait()
        snapshot = store.state.file_summary.loaded_value
        assert snapshot is not None
        store.state.files = [
            file.model_copy(update={"patch": patch}) for file in snapshot.metadata.files
        ]
        store.state.files_by_filename = {
            file.filename: file for file in store.state.files
        }
        store.state.files_loading = LoadingState.LOADED
        store._post_message(store.FilesLoaded(files=store.state.files))

    monkeypatch.setattr(PRStore, "load_overview", skip_overview)
    monkeypatch.setattr(PRStore, "load_pr_overview", skip_overview)
    monkeypatch.setattr(PRStore, "load_files", load_files)
    app = RitApp(owner="owner", repo="repo", pr_number=1)
    async with app.run_test(size=(150, 40)) as pilot:
        screen = cast(MainScreen, app.screen)
        store = screen.store
        store.state.pr = PR(number=1, title="Review", base_sha="base", head_sha="head")
        screen._pr_overview_refs = ("base", "head")
        files = tuple(
            PRFile(
                filename=f"src/{name}.py",
                additions=1,
                deletions=1,
                viewer_viewed_state=FileViewedState.VIEWED
                if name == "three"
                else FileViewedState.UNVIEWED,
            )
            for name in ("one", "two", "three")
        )
        store.state.file_summary.value = PRFilesSnapshot.from_metadata(
            PRFileMetadata("base", "head", files)
        )
        store.state.file_summary.loading = LoadingState.LOADED
        screen.pr_info.refresh_pr_data()
        screen.pr_info.refresh_overview()
        await pilot.pause()
        timeline_calls: list[str] = []
        monkeypatch.setattr(
            screen.pr_info._timeline_widget(),
            "next_item",
            lambda: timeline_calls.append("next"),
        )
        await pilot.press("L")
        assert screen.pr_info.sidebar_has_focus
        await pilot.press("j")
        assert not timeline_calls
        options = screen.query_one("#summary-files-implementation", SummaryOptionList)
        options.focus()
        options.highlighted = 1
        await pilot.press("H", "L")
        assert options.has_focus and options.highlighted == 1
        assert store.state.files_loading == LoadingState.IDLE
        assert not calls
        await pilot.press("enter")
        await asyncio.wait_for(started.wait(), timeout=2)
        assert screen.current_tab == 1 and calls == ["files"]
        assert screen.file_changes.diff_view.current_file is None

        screen.switch_tab(0)
        await wait_until(lambda: screen.tabbed_content.active == "pr-info")
        await pilot.pause()
        options.focus()
        options.highlighted = 2
        await pilot.press("enter")
        assert screen._pending_overview_file is not None
        assert screen._pending_overview_file[0] == "src/three.py"
        release.set()
        await wait_until(
            lambda: screen.file_changes.current_diff_file_target() == "src/three.py",
            timeout=3,
        )
        view = screen.file_changes.diff_view
        await wait_until(
            lambda: not view._fold_worker_active and not view._virt.render_pending,
            timeout=3,
        )
        await pilot.pause()
        assert calls == ["files"]
        assert view.has_focus
        assert "src/three.py" not in view._folded_file_paths
        assert store.state.files[-1].viewer_viewed_state == FileViewedState.VIEWED
        assert view.scroll_y == view._hunk_header_top_offsets[2]
        assert screen._pending_overview_file is None

        screen.switch_tab(0)
        await pilot.pause()
        patch = "@@ -1 +1 @@\n-old\n+latest head"
        store.state.pr = PR(number=1, title="Review", base_sha="base", head_sha="new")
        screen.on_pr_loaded(store.PRLoaded(pr=store.state.pr))
        assert store.state.files_loading == LoadingState.IDLE
        screen.on_workspace_ready()
        assert screen._files_revision_changed
        store.state.file_summary.value = PRFilesSnapshot.from_metadata(
            PRFileMetadata("base", "new", files)
        )
        screen.pr_info.refresh_overview()
        options.focus()
        options.highlighted = 2
        await pilot.press("enter")
        await wait_until(lambda: calls == ["files", "files"], timeout=3)
        await wait_until(lambda: screen.file_changes.workspace_ready, timeout=3)
        await wait_until(lambda: not screen._files_revision_changed, timeout=3)
        assert any(line.new_content == "latest head" for line in view._all_lines)
        assert screen.file_changes.current_diff_file_target() == "src/three.py"
