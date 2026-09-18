import pytest
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Button, Static, Tab, TabbedContent, TabPane

from rit.app import RitApp
from rit.state.models import PR
from rit.ui.widgets.header import Header, PRStatus


class BrokenStatus(Static):
    def update(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("status update failed")


def test_pr_update_refreshes_branches_before_mount() -> None:
    header = Header()
    assert header._branch_info.display is False

    header.update_from_pr(PR(number=1, base_ref="main", head_ref="feature"))
    assert header._branch_info.display is True
    assert str(header._branch_info._branch_label.content) == "main ← feature"

    header.update_from_pr(PR(number=1))
    assert header._branch_info.display is False


@pytest.mark.asyncio
async def test_header_preserves_literal_metadata_and_aligns_with_tabs() -> None:
    class TestApp(App[None]):
        CSS_PATH = RitApp.CSS_PATH

        def compose(self) -> ComposeResult:
            yield Header(owner="test", repo="repo", pr_number=1)
            with TabbedContent(initial="pr-info"):
                yield TabPane("PR Info", id="pr-info")
                yield TabPane("Files", id="files")

    app = TestApp()
    async with app.run_test(size=(100, 12)) as pilot:
        header = app.query_one(Header)
        header.update_from_pr(
            PR(
                number=1,
                title="[Refactor] 리뷰 화면 정리",
                base_ref="main",
                head_ref="feature/review",
            )
        )
        await pilot.pause()

        title = header.query_one("#header-title", Static)
        status = header.query_one("#header-status", Static)
        branches = header.query_one("#branch-info", Static)
        tab = app.query_one(Tab)
        rendered = app.screen._compositor.render_strips()
        title_line = rendered[title.region.y].text
        branch_line = rendered[branches.region.y].text
        tab_line = rendered[tab.region.y].text
        assert title_line.lstrip().startswith("[Refactor] 리뷰 화면 정리 - test/repo")
        assert branch_line.lstrip().startswith("◎ Open  main ← feature/review")
        assert (
            title_line.index("[Refactor]")
            == branch_line.index("◎ Open")
            == tab_line.index("PR Info")
        )

        status_labels: tuple[tuple[PRStatus, str, str], ...] = (
            ("Open", "◎ Open", "#a6da95"),
            ("Merged", "◉ Merged", "#c6a0f6"),
            ("Closed", "⊘ Closed", "#ed8796"),
            ("Draft", "◌ Draft", "#6e738d"),
        )
        for state, label, color in status_labels:
            header.update_pr_info(header.pr_title, state)
            await pilot.pause()

            status_line = status.render_line(0)
            assert status_line.text == label
            segment = next(iter(status_line))
            assert segment.style is not None
            assert segment.style.color is not None
            assert segment.style.color.name == color
            assert status.region.y == branches.region.y == title.region.bottom
            assert branches.region.x == status.region.right + 2
            assert status.content_region.x == title.content_region.x

        header.update_from_pr(
            PR(
                number=1,
                title="[Refactor] " + "리뷰 화면 정리 " * 10,
                state="MERGED",
                base_ref="main",
                head_ref="feature/" + "long-name-" * 8,
            )
        )
        await pilot.resize_terminal(40, 12)
        await pilot.pause()

        title_line = title.render_line(0).text
        assert title_line.startswith("[Refactor]")
        assert "…" in title_line
        assert status.render_line(0).text == "◉ Merged"
        assert "…" in branches.render_line(0).text
        assert (
            title.content_region.x == status.content_region.x == tab.content_region.x
        )
        assert status.region.y == branches.region.y == title.region.bottom
        assert branches.region.x == status.region.right + 2
        copy_button = header.query_one("#copy-branch", Button)
        assert copy_button.region.x == branches.region.right + 1
        assert copy_button.region.right <= header.content_region.right
        assert "\U000f018f" in copy_button.render_line(0).text

        header.update_from_pr(PR(number=1, state="CLOSED"))
        await pilot.pause()
        assert header.query_one("#header-branch-row").display is False
        assert status.render_line(0).text == "⊘ Closed"


def test_status_watcher_ignores_missing_widget_before_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    def missing_widget(*_args: object, **_kwargs: object) -> Static:
        raise NoMatches("missing")

    monkeypatch.setattr(header, "query_one", missing_widget)

    header.watch_pr_status("Merged")

    assert header._status_merged is True


def test_status_watcher_reraises_unexpected_widget_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    monkeypatch.setattr(header, "query_one", lambda *_args, **_kwargs: BrokenStatus())

    with pytest.raises(RuntimeError, match="status update failed"):
        header.watch_pr_status("Closed")


def test_title_watcher_ignores_missing_widget_before_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    def missing_widget(*_args: object, **_kwargs: object) -> Static:
        raise NoMatches("missing")

    monkeypatch.setattr(header, "query_one", missing_widget)

    header.watch_pr_title("Loaded")


def test_title_watcher_reraises_unexpected_display_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    def fail_display(_title: str) -> None:
        raise RuntimeError("title update failed")

    monkeypatch.setattr(header, "_update_title_display", fail_display)

    with pytest.raises(RuntimeError, match="title update failed"):
        header.watch_pr_title("Loaded")
