from datetime import datetime, timezone

import pytest
from textual.app import App, ComposeResult

from rit.state.models import PR, PRIssueComment, PRUser
from rit.state.store import PRStore
from rit.ui.components.pr_timeline import PRTimeline
from rit.ui.components.collapsible_markdown import CopyableCodeBlock


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


@pytest.mark.asyncio
async def test_pr_description_code_block_can_copy_raw_code() -> None:
    store = PRStore()
    store.state.pr = PR(
        number=123,
        body='''Description.

```bash
uv run pytest
```
''',
        author=PRUser(login="alice"),
    )

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield PRTimeline(store)

    app = TestApp()
    async with app.run_test() as pilot:
        timeline = app.query_one(PRTimeline)
        timeline.refresh_description(store.state.pr)
        await pilot.pause()

        code_block = app.query_one(CopyableCodeBlock)
        code_block.copy_code()
        await pilot.pause()

        assert app.clipboard == "uv run pytest"
