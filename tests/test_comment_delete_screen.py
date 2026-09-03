import pytest
from textual.app import App
from textual.widgets import Static

from rit.state.models import PendingReviewComment, PRComment, PRUser
from rit.ui.screens.comment_delete import CommentDeleteScreen


@pytest.mark.asyncio
async def test_comment_delete_screen_confirms_and_cancels() -> None:
    comment = PRComment(
        id=10,
        node_id="PRRC_10",
        body="Delete this submitted review comment",
        user=PRUser(login="alice"),
        path="src/app.py",
        line=42,
        side="RIGHT",
    )
    draft = PendingReviewComment(
        body="Delete this pending draft",
        path="src/draft.py",
        line=7,
        side="RIGHT",
    )
    results: list[bool | None] = []
    app = App()

    async with app.run_test() as pilot:
        app.push_screen(CommentDeleteScreen(draft), results.append)
        await pilot.pause()

        assert "Delete this draft?" in str(
            app.screen.query_one("#comment-delete-title", Static).content
        )
        assert "Pending draft • src/draft.py:7" in str(
            app.screen.query_one("#comment-delete-meta", Static).content
        )
        await pilot.click("#comment-delete-cancel")
        await pilot.pause()
        assert results == [False]

        app.push_screen(CommentDeleteScreen(comment), results.append)
        await pilot.pause()
        assert "@alice • src/app.py:42" in str(
            app.screen.query_one("#comment-delete-meta", Static).content
        )
        await pilot.click("#comment-delete-confirm")
        await pilot.pause()

    assert results == [False, True]
