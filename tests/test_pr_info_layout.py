"""Tests for PR Info layout."""

from itertools import pairwise
from pathlib import Path

import pytest
from rich.cells import cell_len
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.css.styles import Styles
from textual.widgets import Button, Markdown, Static

import rit.ui.components.pr_info as pr_info_module
from rit.state.models import (
    PR,
    NodeList,
    PRLabel,
    PRReview,
    PRTeam,
    PRUser,
    ReviewRequest,
    ReviewState,
)
from rit.state.reviewer_status import ReviewerDisplayState
from rit.state.store import PRStore
from rit.ui.components.pr_info import PRInfo
from rit.ui.components.pr_timeline import PRTimeline
from tests.conftest import wait_until

ROOT = Path(__file__).parents[1]


@pytest.mark.asyncio
async def test_pr_info_groups_main_and_sidebar_in_centered_layout() -> None:
    store = PRStore()
    pr = PR(number=1, title="Review navigation", state="MERGED")
    store.state.pr = pr

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

        title_row = app.query_one("#pr-title-row")
        title = app.query_one("#pr-title", Static)
        assert title.parent is title_row
        assert title.region.width == cell_len("Review navigation #1")
        assert not app.query("#pr-status")
        assert not app.query("#branch-info")
        assert not app.query("#copy-branch")

        pr.title = "1:1 미팅 메모 삭제 지원"
        app.query_one(PRInfo).refresh_summary()
        await pilot.pause()
        assert title.region.width == cell_len(f"{pr.title} #1")
        rendered = app.screen._compositor.render_strips()[title.region.y].text
        assert f"{pr.title} #1" in rendered
        assert "Merged" not in rendered

        pr.title = "Review navigation " * 12
        app.query_one(PRInfo).refresh_summary()
        await pilot.resize_terminal(40, 30)
        await pilot.pause()
        assert title.region.height > 1
        assert title_row.region.height == title.region.height
        assert title.region.right <= title_row.content_region.right


@pytest.mark.asyncio
async def test_pr_info_summary_actions_are_clickable() -> None:
    store = PRStore()
    store.state.pr = PR(number=1, head_ref="feature", base_ref="main")

    class TestApp(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.requests: list[str] = []

        def compose(self) -> ComposeResult:
            yield PRInfo(store)

        @on(PRInfo.EditReviewersRequested)
        def capture_reviewers(self) -> None:
            self.requests.append("reviewers")

        @on(PRInfo.EditAssigneesRequested)
        def capture_assignees(self) -> None:
            self.requests.append("assignees")

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        reviewer_button = app.query_one("#edit-reviewers", Button)
        assignee_button = app.query_one("#edit-assignees", Button)

        assert reviewer_button.tooltip == "Edit reviewers"
        assert assignee_button.tooltip == "Edit assignees"
        assert reviewer_button.parent is not None
        assert reviewer_button.region.x == (
            reviewer_button.parent.query_one(".sidebar-section-title").region.right + 1
        )

        app.query_one(PRInfo).document_scroll.scroll_to_widget(
            reviewer_button, animate=False
        )
        await pilot.pause()
        await pilot.click("#edit-reviewers")
        await pilot.click("#edit-assignees")
        await pilot.pause()

    assert app.requests == ["reviewers", "assignees"]


@pytest.mark.asyncio
async def test_pr_info_stacks_sidebar_using_available_width() -> None:
    store = PRStore()
    store.state.pr = PR(number=1, body="Document paragraph.\n\n" * 40)

    class TestApp(App[None]):
        CSS = "#inbox { width: 64; } PRInfo { width: 1fr; }"

        def compose(self) -> ComposeResult:
            with Horizontal():
                yield Static(id="inbox")
                yield PRInfo(store)

    app = TestApp()
    async with app.run_test(size=(164, 40)) as pilot:
        pr_info = app.query_one(PRInfo)
        await wait_until(lambda: pr_info.compact)
        await pilot.pause()

        main_scroll = app.query_one("#main-scroll")
        sidebar = app.query_one("#sidebar")

        page = app.query_one("#pr-info-layout", VerticalScroll)
        facts = app.query_one("#compact-review-facts", Static)
        assert facts.display and facts.region.height == 3
        assert app.query_one("#pr-title").region.bottom <= facts.region.y
        assert facts.region.bottom <= app.query_one(PRTimeline).region.y
        assert "Open" in str(facts.content)
        assert main_scroll.region.width == page.scrollable_content_region.width
        assert sidebar.region.y >= main_scroll.region.bottom
        assert page.show_vertical_scrollbar
        assert not main_scroll.show_vertical_scrollbar
        assert not sidebar.show_vertical_scrollbar
        assert pr_info.document_scroll is page
        assert app.query_one(PRTimeline)._scroll_container is page
        main_scroll.focus()
        await pilot.press("pagedown")
        await wait_until(lambda: page.scroll_y > 0)
        assert main_scroll.scroll_y == 0 and sidebar.scroll_y == 0
        position = page.scroll_y
        assert pr_info.move_focus("right")
        await wait_until(
            lambda: page.scrollable_content_region.overlaps(
                app.query_one("#edit-reviewers").region
            )
        )
        assert pr_info.active_scroll is page
        pr_info.move_focus("left")
        await wait_until(lambda: page.scroll_y == position)
        page.scroll_to(y=10, animate=False)
        await wait_until(lambda: page.scroll_y == 10)

        assert pr_info.size.width == 100
        app.query_one("#inbox").display = False
        await wait_until(lambda: not pr_info.compact)
        await pilot.pause()
        assert sidebar.region.x >= main_scroll.region.right
        assert not facts.display
        assert not page.show_vertical_scrollbar and page.scroll_y == 0
        assert main_scroll.show_vertical_scrollbar
        assert pr_info.document_scroll is main_scroll
        assert app.query_one(PRTimeline)._scroll_container is main_scroll
        await wait_until(lambda: main_scroll.scroll_y == 10)

        await pilot.resize_terminal(119, 40)
        await wait_until(lambda: pr_info.compact)
        await pilot.pause()
        assert sidebar.region.y >= main_scroll.region.bottom
        await pilot.resize_terminal(120, 40)
        await wait_until(lambda: not pr_info.compact)
        await pilot.pause()
        assert sidebar.region.x >= main_scroll.region.right


@pytest.mark.asyncio
async def test_pr_info_keeps_long_reviewers_on_one_line_with_full_tooltip() -> None:
    long_login = "copilot-pull-request-reviewer"
    long_team = "플랫폼 인프라 리뷰어 팀 with spaces"
    store = PRStore()
    pr = PR(number=1)
    pr.review_requests_connection.nodes = [
        ReviewRequest(requested_reviewer=PRUser(login=long_login)),
        ReviewRequest(requested_reviewer=PRTeam(name=long_team)),
        ReviewRequest(requested_reviewer=PRUser(login="alice")),
    ]
    store.state.pr = pr
    store.state.reviews = [
        PRReview(user=PRUser(login="alice"), state=ReviewState.APPROVED)
    ]
    full_lines = [f"● @{long_login}", f"● {long_team}", "✓ @alice"]

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PRInfo(store)

    app = TestApp()
    async with app.run_test(size=(120, 30)) as pilot:
        reviewers = app.query_one("#pr-reviewers", Static)
        for width in (120, 80, 40, 120):
            await pilot.resize_terminal(width, 30)
            await pilot.pause()

            assert reviewers.size.height == len(full_lines)
            rendered = [
                reviewers.render_line(index).text.rstrip()
                for index in range(len(full_lines))
            ]
            assert rendered == [
                Content(line)
                .truncate(reviewers.content_size.width, ellipsis=True)
                .plain
                for line in full_lines
            ]
            for full_line, rendered_line in zip(full_lines[:2], rendered[:2]):
                assert rendered_line.endswith("…") == (
                    cell_len(full_line) > reviewers.content_size.width
                )
            assert rendered[2] == "✓ @alice"
            assert isinstance(reviewers.tooltip, str)
            assert (
                Content.from_markup(reviewers.tooltip).plain.splitlines() == full_lines
            )

        pr.review_requests_connection.nodes = []
        store.state.reviews = []
        app.query_one(PRInfo).refresh_reviewers()
        await pilot.pause()

        assert reviewers.size.height == 1
        assert reviewers.render_line(0).text.strip() == "None yet"
        assert reviewers.tooltip is None


@pytest.mark.asyncio
async def test_pr_description_uses_document_width_and_block_rhythm() -> None:
    body = (ROOT / "tests/fixtures/pr_description_22546.md").read_text()
    store = PRStore()
    store.state.pr = PR(
        number=1,
        title="Improve description rendering",
        body=body,
        user=PRUser(login="alice"),
    )

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield PRInfo(store)

    app = TestApp(css_path=ROOT / "src/rit/rit.tcss")
    app.theme = "catppuccin-macchiato"
    async with app.run_test(size=(180, 40)) as pilot:
        await wait_until(lambda: len(app.query("MarkdownParagraph")) == 26)
        await pilot.pause()

        description = app.query_one("#pr-description-card")
        description_body = description.query_one(".comment-content")
        main_content = app.query_one("#main-content")
        main_scroll = app.query_one("#main-scroll", VerticalScroll)
        header = description.query_one(".comment-header", Static)
        paragraphs = list(description.query("MarkdownParagraph").results(Static))
        headings = list(description.query("MarkdownH3").results(Static))
        ordered = list(description.query("MarkdownOrderedList MarkdownParagraph"))
        endpoint = next(p for p in paragraphs if "GET /api" in str(p.content))
        cause = next(p for p in paragraphs if str(p.content).startswith("Cause:"))

        assert str(header.content) == "Description \u00b7 alice"
        assert description.has_class("reading")
        assert description.query_one(Markdown).source == body.strip()
        assert description.styles.background.hex == "#1E2030"
        assert description.styles.border.top[0] == "solid"
        assert description.styles.border.right == description.styles.border.top
        assert description.styles.border.bottom == description.styles.border.top
        assert description.styles.border.left[0] == "solid"
        assert description.styles.border.left != description.styles.border.top
        assert description.styles.padding.top == description.styles.padding.bottom == 1
        assert [str(heading.content) for heading in headings] == [
            "Background",
            "Changes",
            "Review checklist",
            "Notes",
        ]
        for index, heading in enumerate(headings):
            assert heading.styles.margin.top == (0 if index == 0 else 2)
            assert heading.styles.margin.bottom == 1
            assert heading.styles.text_style.bold
        for paragraph in paragraphs:
            assert paragraph.styles.margin.bottom == 1
        for bullet_list in description.query("MarkdownList"):
            assert bullet_list.styles.margin.bottom == 0
        assert len(ordered) == 3
        assert [
            bullet.render_line(0).text.strip()
            for bullet in description.query(
                "MarkdownOrderedList > Horizontal > MarkdownBullet"
            )
        ] == ["1.", "2.", "3."]

        for width in (240, 180, 120, 119, 100, 94, 93, 80, 60, 40, 30, 80, 180):
            await pilot.resize_terminal(width, 40)
            await pilot.pause()
            assert description.region.x == main_content.content_region.x
            assert description.region.right == main_content.content_region.right
            side_padding = 3 if width >= 120 else 1
            assert (
                description.styles.padding.left
                == description.styles.padding.right
                == side_padding
            )
            assert description_body.content_region.width == min(
                100, description.content_region.width
            )
            assert header.region.y == description.region.y + 2
            assert header.region.x == description_body.region.x
            if width >= 120:
                comment_header = app.query_one("#comments-container .comment-header")
                assert header.region.x == comment_header.region.x
            assert description_body.region.x == description.content_region.x
            assert description_body.region.right <= description.content_region.right
            assert main_scroll.max_scroll_x == 0
            assert description.max_scroll_x == 0
            assert cause.size.height > 1
            assert (endpoint.size.height > 1) == (
                cell_len(str(endpoint.content)) > endpoint.content_size.width
            )

            for paragraph in paragraphs:
                lines = [
                    paragraph.render_line(i).text for i in range(paragraph.size.height)
                ]
                assert "".join("".join(lines).split()) == "".join(
                    str(paragraph.content).split()
                )
                assert all(
                    cell_len(line) <= paragraph.content_size.width for line in lines
                )
                assert paragraph.region.right <= description_body.content_region.right
                assert paragraph.region.bottom <= description_body.content_region.bottom
                assert paragraph.max_scroll_x == 0

            for row in description.query("MarkdownList > Horizontal"):
                bullet = row.query_one("MarkdownBullet")
                paragraph = row.query_one("MarkdownParagraph")
                assert paragraph.region.x == bullet.region.right
                assert paragraph.region.y == bullet.region.y
            assert len({p.region.x for p in ordered}) == 1
            nested_list = description.query_one("MarkdownOrderedList")
            parent_item = nested_list.parent
            assert parent_item is not None
            parent_paragraph = parent_item.query_one("MarkdownParagraph")
            assert nested_list.region.x == parent_paragraph.region.x
            assert ordered[0].region.x == parent_paragraph.region.x + 4
            assert nested_list.region.y == parent_paragraph.region.bottom + 1
            for first, second in pairwise(ordered):
                assert second.region.y == first.region.bottom + 1

            app.query_one(PRInfo).document_scroll.scroll_to_widget(
                endpoint, top=True, animate=False
            )
            await pilot.pause()
            screen = app.screen._compositor.render_strips()
            painted = [
                screen[y].crop(endpoint.region.x, endpoint.region.right).text.rstrip()
                for y in range(endpoint.region.y, endpoint.region.bottom)
            ]
            assert painted == [
                endpoint.render_line(i).text.rstrip()
                for i in range(endpoint.size.height)
            ]


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


def test_pr_info_reuses_header_rendering_for_unchanged_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = PRStore()
    store.state.pr = PR(
        number=1,
        title="Add diff cache",
        additions=10,
        deletions=2,
        base_ref="main",
        head_ref="feature",
    )
    pr_info = PRInfo(store)
    widgets = {
        "#pr-title": _CaptureStatic(),
        "#pr-stats": _CaptureStatic(),
    }

    def static_widget(_attr_name: str, selector: str) -> _CaptureStatic:
        return widgets[selector]

    monkeypatch.setattr(pr_info, "_static_widget", static_widget)

    pr_info._update_header()
    pr_info._update_header()

    assert {selector: widget.update_count for selector, widget in widgets.items()} == {
        "#pr-title": 1,
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
    store.state.pr = PR(number=1, user=PRUser(login="author"))
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
    store.state.pr = PR(number=1, user=PRUser(login="author"))
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
    store.state.pr = PR(
        number=1, labels_connection=NodeList(nodes=[PRLabel(name="bug")])
    )
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
    store.state.pr = PR(
        number=1, assignees_connection=NodeList(nodes=[PRUser(login="alice")])
    )
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


def test_pr_info_css_uses_document_sized_center_column() -> None:
    css = (ROOT / "src/rit/ui/components/pr_info.tcss").read_text()

    pr_info_block = css.split("PRInfo {", 1)[1].split("}", 1)[0]
    layout_block = css.split("PRInfo #pr-info-layout {", 1)[1].split("}", 1)[0]
    main_scroll_block = css.split("PRInfo #main-scroll {", 1)[1].split("}", 1)[0]
    wide_block = css.split("PRInfo.-wide {", 1)[1].split("}", 1)[0]

    assert "align: center top;" in pr_info_block
    assert "max-width: 183;" in layout_block
    assert "width: 100%;" in layout_block
    assert "width: 1fr;" in main_scroll_block
    assert "scrollbar-gutter: stable;" in main_scroll_block
    assert "padding: 0;" in wide_block


class _CaptureStatic:
    def __init__(self) -> None:
        self.content = ""
        self.update_count = 0
        self.styles = Styles()

    def update(self, content: str) -> None:
        self.update_count += 1
        self.content = content
