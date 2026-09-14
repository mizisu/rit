"""Tests for shared branch comparison metadata."""

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Button, Static

from rit.ui.widgets.branch_info import BranchInfo


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        ("main", "feature/review", "main ← feature/review"),
        ("main", "feature/[bold]review", "main ← feature/[bold]review"),
        ("", "feature", "(unavailable) ← feature"),
        ("main", "", "main ← (unavailable)"),
        ("", "", "(unavailable) ← (unavailable)"),
    ],
)
def test_branch_info_preserves_names_and_handles_missing_refs(
    base: str, head: str, expected: str
) -> None:
    info = BranchInfo()
    info.update_branches(base, head)

    assert str(info._branch_label.content) == expected
    visual = info._branch_label.visual
    assert isinstance(visual, Content)
    assert visual.plain == expected
    assert info.display is bool(base or head)
    assert info._copy_button.disabled is not bool(base or head)


@pytest.mark.asyncio
async def test_long_branches_keep_copy_visible_when_resized() -> None:
    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield BranchInfo()

    app = TestApp()
    async with app.run_test(size=(80, 12)) as pilot:
        info = app.query_one(BranchInfo)
        base = "release/" + "base-" * 20
        head = "feature/" + "head-" * 30
        info.update_branches(base, head)
        await pilot.pause()

        label = info.query_one(Static)
        copy_button = info.query_one(Button)
        assert label.tooltip == f"Base: {base}\nHead: {head}"
        assert copy_button.region.x == label.region.right + 1
        assert copy_button.region.right <= info.content_region.right

        await pilot.resize_terminal(40, 12)
        await pilot.pause()

        assert info.region.height == 1
        assert label.region.height == 1
        assert copy_button.region.height == 1
        assert "\U000f018f" in copy_button.render_line(0).text
        assert copy_button.region.x == label.region.right + 1
        assert copy_button.region.right <= info.content_region.right
        rendered = app.screen._compositor.render_strips()[0].text
        assert "…" in rendered
        assert "Ctrl+B" not in rendered
        assert str(label.content) == f"{base} ← {head}"
