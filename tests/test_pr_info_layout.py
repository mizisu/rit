"""Tests for PR Info layout."""

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches

from rit.state.models import PR, PRLabel, PRUser
from rit.state.reviewer_status import ReviewerDisplayState
from rit.state.store import PRStore
import rit.ui.components.pr_info as pr_info_module
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


def test_pr_info_reuses_mounted_timeline_for_hot_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr_info = PRInfo(PRStore())
    timeline = PRTimeline(PRStore())
    calls: set[str] = set()

    def query_one(*args: object, **_kwargs: object) -> object:
        if args == ("#main-scroll", VerticalScroll):
            return object()
        if args == (PRTimeline,):
            return timeline
        raise NoMatches("unexpected query")

    monkeypatch.setattr(pr_info, "query_one", query_one)
    pr_info.on_mount()
    monkeypatch.setattr(timeline, "next_item", lambda: calls.add("next_item"))
    monkeypatch.setattr(timeline, "prev_item", lambda: calls.add("prev_item"))
    monkeypatch.setattr(timeline, "next_comment", lambda: calls.add("next_comment"))
    monkeypatch.setattr(timeline, "prev_comment", lambda: calls.add("prev_comment"))

    def fail_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("cached timeline navigation should not query")

    monkeypatch.setattr(pr_info, "query_one", fail_query)

    pr_info.next_item()
    pr_info.prev_item()
    pr_info.next_comment()
    pr_info.prev_comment()

    assert calls == {"next_item", "prev_item", "next_comment", "prev_comment"}


def test_pr_info_header_uses_shared_status_label_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=1, title="Add diff cache")
    pr_info = PRInfo(store)
    widgets = {
        "#pr-title": _CaptureStatic(),
        "#pr-status": _CaptureStatic(),
        "#branch-info": _CaptureStatic(),
        "#pr-stats": _CaptureStatic(),
    }

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        return widgets[selector]

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)
    monkeypatch.setattr(
        pr_info_module,
        "_PR_STATUS_LABELS",
        {"Open": "open-from-shared-map"},
        raising=False,
    )

    pr_info._update_header()

    assert widgets["#pr-status"].content == "open-from-shared-map"


def test_pr_info_reuses_header_rendering_for_unchanged_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(
        number=1,
        title="Add diff cache",
        additions=10,
        deletions=2,
        baseRefName="main",
        headRefName="feature",
    )
    pr_info = PRInfo(store)
    widgets = {
        "#pr-title": _CaptureStatic(),
        "#pr-status": _CaptureStatic(),
        "#branch-info": _CaptureStatic(),
        "#pr-stats": _CaptureStatic(),
    }

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        return widgets[selector]

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)

    pr_info._update_header()
    pr_info._update_header()

    assert {selector: widget.update_count for selector, widget in widgets.items()} == {
        "#pr-title": 1,
        "#pr-status": 1,
        "#branch-info": 1,
        "#pr-stats": 1,
    }

    store.state.pr.additions = 11
    pr_info._update_header()

    assert widgets["#pr-stats"].update_count == 2
    assert "+11" in widgets["#pr-stats"].content


def test_pr_info_reuses_reviewer_rendering_for_unchanged_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=1, author={"login": "author"})
    pr_info = PRInfo(store)
    reviewers_widget = _CaptureStatic()
    calls = 0

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        assert selector == "#pr-reviewers"
        return reviewers_widget

    def derive_reviewers(_pr: PR, _reviews: list[object]) -> list[ReviewerDisplayState]:
        nonlocal calls
        calls += 1
        return [
            ReviewerDisplayState(
                display_name="alice",
                kind="approved",
                latest_review_at=None,
                is_requested=False,
                is_team=False,
            )
        ]

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)
    monkeypatch.setattr(pr_info_module, "derive_reviewer_states", derive_reviewers)

    pr_info._update_reviewers()
    pr_info._update_reviewers()

    assert calls == 1
    assert reviewers_widget.content == "[#a6da95]✓[/] @alice"


def test_pr_info_single_reviewer_rendering_skips_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SingleReviewerList(list[ReviewerDisplayState]):
        def __iter__(self):
            raise AssertionError("single reviewer rendering should not iterate")

    store = PRStore()
    store.state.pr = PR(number=1, author={"login": "author"})
    pr_info = PRInfo(store)
    reviewers_widget = _CaptureStatic()

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        assert selector == "#pr-reviewers"
        return reviewers_widget

    def derive_reviewers(_pr: PR, _reviews: list[object]) -> list[ReviewerDisplayState]:
        return SingleReviewerList(
            [
                ReviewerDisplayState(
                    display_name="alice",
                    kind="approved",
                    latest_review_at=None,
                    is_requested=False,
                    is_team=False,
                )
            ]
        )

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)
    monkeypatch.setattr(pr_info_module, "derive_reviewer_states", derive_reviewers)

    pr_info._update_reviewers()

    assert reviewers_widget.content == "[#a6da95]✓[/] @alice"


def test_pr_info_reuses_label_rendering_for_unchanged_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=1, labels={"nodes": [PRLabel(name="bug")]})
    pr_info = PRInfo(store)
    labels_widget = _CaptureStatic()

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        assert selector == "#pr-labels"
        return labels_widget

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)

    pr_info._update_labels()
    pr_info._update_labels()

    assert labels_widget.update_count == 1
    assert labels_widget.content == "● bug"


def test_pr_info_single_label_rendering_skips_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SingleLabelList(list[PRLabel]):
        def __iter__(self):
            raise AssertionError("single label rendering should not iterate")

    store = PRStore()
    store.state.pr = PR(number=1)
    store.state.pr.labels_connection.nodes = SingleLabelList([PRLabel(name="bug")])
    pr_info = PRInfo(store)
    labels_widget = _CaptureStatic()

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        assert selector == "#pr-labels"
        return labels_widget

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)

    pr_info._update_labels()

    assert labels_widget.content == "● bug"


def test_pr_info_reuses_assignee_rendering_for_unchanged_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(number=1, assignees={"nodes": [PRUser(login="alice")]})
    pr_info = PRInfo(store)
    assignees_widget = _CaptureStatic()

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        assert selector == "#pr-assignees"
        return assignees_widget

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)

    pr_info._update_assignees()
    pr_info._update_assignees()

    assert assignees_widget.update_count == 1
    assert assignees_widget.content == "@alice"


def test_pr_info_single_assignee_rendering_skips_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SingleAssigneeList(list[PRUser]):
        def __iter__(self):
            raise AssertionError("single assignee rendering should not iterate")

    store = PRStore()
    store.state.pr = PR(number=1)
    store.state.pr.assignees_connection.nodes = SingleAssigneeList(
        [PRUser(login="alice")]
    )
    pr_info = PRInfo(store)
    assignees_widget = _CaptureStatic()

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        assert selector == "#pr-assignees"
        return assignees_widget

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)

    pr_info._update_assignees()

    assert assignees_widget.content == "@alice"


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


class _CaptureStatic:
    def __init__(self) -> None:
        self.content = ""
        self.update_count = 0

    def update(self, content: str) -> None:
        self.update_count += 1
        self.content = content
