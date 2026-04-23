from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult

from rit.state.models import PRIssueComment, PRUser
from rit.state.store import PRStore
from rit.ui.components.pr_timeline import PRTimeline


@pytest.mark.asyncio
async def test_issue_comment_left_aligns_markdown_h1() -> None:
    store = PRStore()
    store.state.issue_comments = [
        PRIssueComment(
            id=1,
            body="# Test",
            user=PRUser(login="alice"),
            created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
        )
    ]

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        timeline.refresh_timeline()
        await pilot.pause()
        await pilot.pause()

        h1 = app.query_one("MarkdownH1")
        assert h1.styles.content_align == ("left", "middle")
