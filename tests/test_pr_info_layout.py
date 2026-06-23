"""Tests for PR Info layout."""

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.css.query import NoMatches

from rit.state.store import PRStore
from rit.ui.components.pr_info import PRInfo
from rit.ui.components.pr_timeline import PRTimeline

ROOT = Path(__file__).parents[1]


@pytest.mark.asyncio
async def test_pr_info_groups_main_and_sidebar_in_centered_layout() -> None:
    store = PRStore()

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PRInfo(store)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        layout = app.query_one("#pr-info-layout")
        main_scroll = app.query_one("#main-scroll")
        sidebar = app.query_one("#sidebar")

        assert main_scroll.parent is layout
        assert sidebar.parent is layout


def test_pr_info_mount_ignores_missing_scroll_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr_info = PRInfo(PRStore())

    def missing_widget(*_args: object, **_kwargs: object) -> object:
        raise NoMatches("missing")

    monkeypatch.setattr(pr_info, "query_one", missing_widget)

    pr_info.on_mount()


def test_pr_info_mount_reraises_unexpected_timeline_wiring_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr_info = PRInfo(PRStore())

    class BrokenTimeline:
        def set_scroll_container(self, _container: object) -> None:
            raise RuntimeError("timeline wiring failed")

    def query_one(*args: object, **_kwargs: object) -> object:
        if args == (PRTimeline,):
            return BrokenTimeline()
        return object()

    monkeypatch.setattr(pr_info, "query_one", query_one)

    with pytest.raises(RuntimeError, match="timeline wiring failed"):
        pr_info.on_mount()


def test_pr_info_css_uses_github_like_center_column() -> None:
    css = (ROOT / "src/rit/ui/components/pr_info.tcss").read_text()

    pr_info_block = css.split("PRInfo {", 1)[1].split("}", 1)[0]
    layout_block = css.split("PRInfo #pr-info-layout {", 1)[1].split("}", 1)[0]
    main_scroll_block = css.split("PRInfo #main-scroll {", 1)[1].split("}", 1)[0]
    wide_block = css.split("PRInfo.-wide {", 1)[1].split("}", 1)[0]

    assert "align: center top;" in pr_info_block
    assert "max-width: 152;" in layout_block
    assert "width: 100%;" in layout_block
    assert "width: 1fr;" in main_scroll_block
    assert "padding: 0;" in wide_block
