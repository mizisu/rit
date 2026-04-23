import pytest
from textual.app import App, ComposeResult

from rit.state.store import PRStore
from rit.ui.components.pr_timeline import PRTimeline


@pytest.mark.asyncio
async def test_pr_timeline_places_editors_after_comments_container() -> None:
    store = PRStore()

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        timeline = app.query_one(PRTimeline)
        children = list(timeline.children)

        assert children[1].id == "comments-container"
        assert [child.id for child in children[2:]] == ["issue-comment-editor"]
