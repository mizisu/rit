import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from textual.command import CommandPalette
from textual.widgets import Input, OptionList, Static, Tabs

from rit.app import RitApp
from rit.state.models import PR, PendingReviewComment
from rit.state.store import PRListStore, PRStore
from rit.ui.screens.main import MainScreen
from rit.ui.screens.pr_list import PRListScreen
from rit.ui.widgets.comment_editor import CommentEditorBuffer
from rit.ui.widgets.diff_composer import ComposerSession
from rit.ui.widgets.pr_sidebar import PRSidebar
from tests.conftest import wait_until
from tests.test_pr_list import item, snapshot


@pytest.mark.parametrize("width", [12, 28, 48, 70, 95])
def test_rows_fit_terminal_cells_and_keep_ci_at_the_right(width: int) -> None:
    pr = item(
        214, title="리뷰 요청 상태 처리 개선 " * 8, checks="FAILURE", requested=True
    )
    row = PRSidebar.row(pr, width, prefix="├─ ")
    assert row.cell_len == width
    assert "\n" not in row.plain
    assert "×" in row.plain[-8:]
    assert "…" in row.plain


def test_review_submission_during_initial_list_load_requests_a_followup() -> None:
    app = RitApp("org", "repo", 1)
    app.pr_list_store.loading = True
    app.review_submitted(1)
    assert app._pr_list_refresh_pending


def test_row_context_and_untrusted_titles_stay_single_line() -> None:
    pr = item(1, title="[bold]title\nnext\tline[/bold]")
    row = PRSidebar.row(pr, 80, context=True)
    assert row.cell_len == 80
    assert "[ctx]" in row.plain
    assert "[bold]" in row.plain
    assert "\n" not in row.plain and "\t" not in row.plain


@pytest.mark.parametrize(
    ("state", "draft", "icon"),
    [
        ("OPEN", False, "◎"),
        ("OPEN", True, "◌"),
        ("MERGED", False, "◉"),
        ("CLOSED", True, "⊘"),
    ],
)
def test_row_uses_shared_pr_status_icons(state: str, draft: bool, icon: str) -> None:
    row = PRSidebar.row(item(1, state=state, draft=draft), 60)
    assert row.plain.startswith(icon)
    assert ("Draft " in row.plain) == (state == "OPEN" and draft)


@pytest.mark.parametrize(
    ("seconds", "age"),
    [
        (0, "now"),
        (120, "2m"),
        (10800, "3h"),
        (172800, "2d"),
        (1814400, "3w"),
        (7776000, "3mo"),
        (63072000, "2y"),
    ],
)
def test_row_has_compact_relative_age(seconds: int, age: str) -> None:
    pr = item(1, updated_at=datetime.now(UTC) - timedelta(seconds=seconds))
    assert PRSidebar.row(pr, 60).plain.endswith(age)


def test_row_prioritizes_title_over_author_at_sidebar_width() -> None:
    pr = item(1, title="긴 제목 " * 20, author="reviewer-long-login")
    assert "reviewer" not in PRSidebar.row(pr, 61).plain
    assert "reviewer" in PRSidebar.row(pr, 95).plain


async def test_inbox_layout_preview_and_initial_selection_skip_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title = "[bold] 긴 제목도 선택 미리보기에서 확인합니다 [/bold]"
    inbox = snapshot(
        item(1),
        item(
            2,
            base_ref="branch-1",
            title=title,
            requested=True,
            author="reviewer-long-login",
        ),
    )

    async def load_list(store: PRListStore) -> None:
        store.snapshot = inbox
        store.loading = False
        store.attempted = True

    monkeypatch.setattr(PRListStore, "load", load_list)
    app = RitApp()
    async with app.run_test(size=(180, 40)) as pilot:
        await wait_until(lambda: app.pr_list_store.snapshot is not None)
        sidebar = app.screen.query_one(PRSidebar)
        options = sidebar.query_one(OptionList)
        preview = sidebar.query_one("#pr-list-preview", Static)
        assert not sidebar.query("#pr-list-help")
        tabs = sidebar.query_one(Tabs)
        await wait_until(lambda: title in str(preview.content))
        assert options.highlighted_option is not None
        assert options.highlighted_option.id == "2"
        assert "reviewer-long-login" in str(preview.content)
        assert tabs.region.y >= sidebar.query_one("#pr-list-title").region.bottom
        assert options.region.y >= tabs.region.bottom
        assert "org/repo" in str(sidebar.query_one("#pr-list-title", Static).content)
        width = options.content_region.width
        assert width == options.region.width

        sidebar.focus_list()
        await pilot.press("k")
        await wait_until(lambda: "Stack context" in str(preview.content))
        assert options.content_region.width == width
        assert options.content_region.width == options.region.width
        assert app.pr_number == 0
        await pilot.press("j")
        await wait_until(lambda: title in str(preview.content))

        app.settings.set("ui.theme", "textual-light")
        await wait_until(lambda: app.theme == "textual-light")
        assert options.highlighted_option is not None
        assert options.highlighted_option.id == "2"
        await pilot.press("h")
        await wait_until(lambda: not preview.display)
        assert app.pr_list_tab == "created"
        assert options.highlighted_option is None
        assert "No PRs authored by you" in str(options.get_option_at_index(0).prompt)
        await pilot.press("l")
        await wait_until(lambda: preview.display)
        assert title in str(preview.content)


async def test_direct_launch_keeps_hidden_inbox_out_of_focus_and_loads_it_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[bool] = []

    async def skip(_store: PRStore) -> None:
        pass

    async def load_list(store: PRListStore) -> None:
        loads.append(True)
        store.snapshot = snapshot(item(1, requested=True))
        store.loading = False
        store.attempted = True

    monkeypatch.setattr(PRStore, "load_overview", skip)
    monkeypatch.setattr(PRListStore, "load", load_list)
    app = RitApp("org", "repo", 1)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = app._get_main_screen()
        assert screen is not None
        sidebar = screen.query_one(PRSidebar)
        assert not sidebar.display and not sidebar.has_focus_within
        assert not loads
        await pilot.press("ctrl+l")
        await wait_until(lambda: screen.pr_info.sidebar_has_focus)
        await pilot.press("ctrl+e")
        await wait_until(lambda: sidebar.has_focus_within and bool(loads))
        await pilot.press("ctrl+shift+p")
        await wait_until(lambda: not sidebar.display)
        assert not sidebar.has_focus_within
        await pilot.press("ctrl+e")
        await wait_until(lambda: sidebar.has_focus_within)
        screen.focus_review()
        await pilot.press("ctrl+e")
        await wait_until(lambda: not sidebar.display)


async def test_done_groups_start_collapsed_and_remember_expansion_per_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = snapshot(
        item(1, review_count=1),
        item(2, author="viewer", state="MERGED", closed_at=datetime.now(UTC)),
    )

    async def load_list(store: PRListStore) -> None:
        store.snapshot = inbox
        store.loading = False
        store.attempted = True

    monkeypatch.setattr(PRListStore, "load", load_list)
    app = RitApp()
    async with app.run_test(size=(160, 40)) as pilot:
        await wait_until(lambda: app.pr_list_store.snapshot is not None)
        sidebar = app.screen.query_one(PRSidebar)
        options = sidebar.query_one(OptionList)

        def listed(number: int) -> bool:
            return any(
                options.get_option_at_index(index).id == str(number)
                for index in range(options.option_count)
            )

        assert not listed(1)
        assert options.highlighted_option is not None
        assert options.highlighted_option.id == "group-done"
        assert "▸ Done  1" in str(options.highlighted_option.prompt)
        await pilot.press("enter")
        await wait_until(lambda: listed(1))
        assert app.pr_number == 0
        sidebar.refresh_list()
        assert listed(1)
        await pilot.press("h")
        await wait_until(lambda: app.pr_list_tab == "created")
        assert not listed(2)
        await pilot.press("enter")
        await wait_until(lambda: listed(2))
        await pilot.press("l")
        await wait_until(lambda: listed(1))
        await pilot.click(
            "#pr-list-options",
            offset=(2, options.get_option_index("group-done")),
        )
        await wait_until(lambda: not listed(1))
        await pilot.press("ctrl+e")
        await wait_until(lambda: not sidebar.display)
        assert app.pr_number == 0 and isinstance(app.screen, PRListScreen)
        await pilot.press("ctrl+e")
        await wait_until(lambda: sidebar.has_focus_within)
        assert not listed(1)


async def test_inbox_switching_retains_drafts_and_isolates_keys_and_pr_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[int] = []
    list_loads: list[bool] = []
    browser_calls: list[list[str]] = []
    finish_context = asyncio.Event()
    inbox = snapshot(
        item(1, requested=True), item(2, review_count=1), item(3, author="viewer")
    )

    async def load_pr(store: PRStore) -> None:
        loads.append(store.pr_number)

    async def load_files(_store: PRStore) -> None:
        pass

    async def load_list(store: PRListStore) -> None:
        list_loads.append(True)
        store.attempted = True
        if len(list_loads) == 1:
            store._accept_initial(replace(inbox, context_complete=False))
            await finish_context.wait()
        store.snapshot = inbox
        store.loading = False

    async def browser(args: list[str]) -> str:
        browser_calls.append(args)
        return ""

    monkeypatch.setattr(PRStore, "load_overview", load_pr)
    monkeypatch.setattr(PRStore, "load_files", load_files)
    monkeypatch.setattr(PRListStore, "load", load_list)
    monkeypatch.setattr("rit.app.run_gh", browser)
    app = RitApp()
    async with app.run_test(size=(180, 45)) as pilot:
        await wait_until(lambda: app.owner == "org" and app.repo == "repo")
        assert app.pr_list_store.loading
        assert isinstance(app.screen, PRListScreen)
        assert loads == []
        sidebar = app.screen.query_one(PRSidebar)
        options = sidebar.query_one(OptionList)
        options.highlighted = options.get_option_index("1")
        options.focus()
        await pilot.press("j", "k")
        assert isinstance(app.screen, PRListScreen)
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, MainScreen) and app.screen.pr_number == 1
        )
        await pilot.pause()
        first = app._get_main_screen()
        assert first is not None
        assert app.pr_list_visible
        assert app.owner == "org" and app.repo == "repo"
        assert first.store.pr_number == 1
        assert first.store._service._owner == "org"
        assert first.store._service._repo == "repo"
        finish_context.set()
        await wait_until(lambda: not app.pr_list_store.loading)
        first.store.state.selected_file = "saved.py"
        first.store.state.pending_review.set_body("Unsent summary")
        first.store.state.pending_review.comments = [
            PendingReviewComment(
                path="saved.py", line=1, body="Local-only draft", is_diff_line=False
            )
        ]
        composer = first.file_changes.diff_view._composer
        composer.session = ComposerSession(
            kind="inline",
            title="Comment",
            context="saved.py:1",
            buffer=CommentEditorBuffer(text="Unsent editor"),
            suspended=True,
        )
        first.query_one(PRSidebar).focus_list()
        options = first.query_one("#pr-list-options", OptionList)
        options.highlighted = options.get_option_index("group-done")
        await pilot.press("enter")
        options.highlighted = options.get_option_index("2")
        await pilot.press("enter")
        await wait_until(
            lambda: isinstance(app.screen, MainScreen) and app.screen.pr_number == 2
        )
        await pilot.pause()
        second = app._get_main_screen()
        assert second is not None and second is not first
        assert first.is_mounted
        assert second.store.state.pending_review.body == ""
        first.on_pr_loaded(
            PRStore.PRLoaded(PR(number=1, title="Late first PR response"))
        )
        assert second.header.pr_title != "Late first PR response"
        await app.action_open_pr()
        assert browser_calls[-1] == ["pr", "view", "2", "--web", "-R", "org/repo"]
        await app.open_pr_number(1)
        await pilot.pause()
        assert app.screen is first
        assert first.store.state.selected_file == "saved.py"
        assert first.store.state.pending_review.body == "Unsent summary"
        assert first.store.state.pending_review.comments[0].body == "Local-only draft"
        assert first.file_changes.diff_view._composer is composer
        assert (
            composer.session is not None
            and composer.session.buffer.text == "Unsent editor"
        )
        assert loads == [1, 2]
        await app.action_open_pr()
        assert browser_calls[-1][2] == "1"

        sidebar = first.query_one(PRSidebar)
        sidebar.focus_list()
        await wait_until(lambda: sidebar.has_focus_within)
        before_tab = first.current_tab
        await pilot.press("left")
        assert app.pr_list_tab == "review"
        await pilot.press("right")
        assert app.pr_list_tab == "review"
        await pilot.press("h")
        await wait_until(lambda: app.pr_list_tab == "created")
        assert first.current_tab == before_tab
        await pilot.press("l", "ctrl+r")
        await wait_until(lambda: len(list_loads) == 2)
        assert app.pr_list_tab == "review"

        app.review_submitted(1)
        assert app.pr_list_store.snapshot is not None
        updated = next(pr for pr in app.pr_list_store.snapshot.items if pr.number == 1)
        assert updated.review_count and not updated.requested
        await pilot.press("ctrl+p")
        await wait_until(lambda: isinstance(app.screen, CommandPalette))
        await pilot.press("ctrl+shift+p", "ctrl+e")
        assert isinstance(app.screen, CommandPalette)
        await pilot.press("escape")
        await wait_until(lambda: app.screen is first)
        editor = Input()
        await first.query_one("#pr-workspace").mount(editor)
        editor.focus()
        await pilot.press("ctrl+shift+p", "ctrl+e", "j", "h", "l")
        assert app.pr_list_visible and editor.value == "jhl"
        await editor.remove()

        await pilot.resize_terminal(100, 35)
        await wait_until(lambda: not first.query_one("#pr-workspace").display)
        assert first.has_class("pr-list-overlay")
        sidebar.focus_list()
        options.highlighted = options.get_option_index("1")
        await pilot.press("enter")
        await wait_until(lambda: not app.pr_list_visible)
        assert app.screen is first
        await wait_until(lambda: first.query_one("#pr-workspace").display)
        await pilot.press("ctrl+e")
        await wait_until(lambda: app.pr_list_visible and sidebar.has_focus_within)
        await pilot.press("escape")
        await wait_until(lambda: not app.pr_list_visible)
        assert app.screen is first
