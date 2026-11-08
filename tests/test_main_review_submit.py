from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from textual.widgets import TextArea
from textual.worker import Worker

from rit.app import RitApp
from rit.core.diff import parse_patch
from rit.state.models import PR, PendingReviewComment, PRComment
from rit.state.store import GitHubError
from rit.ui.screens.main import MainScreen
from rit.ui.screens.review_submit import ReviewSubmitScreen
from rit.ui.widgets.comment_editor import CommentEditorBuffer, EditorKind
from rit.ui.widgets.diff_composer import ComposerSession
from rit.ui.widgets.diff_view import DiffView
from tests.conftest import wait_until
from tests.test_app import _stub_initial_loads
from tests.test_store_review_submit import FakeReviewService


class _WorkerStub:
    def __init__(self, error: BaseException | None) -> None:
        self.error = error


def test_review_submit_worker_does_not_exit_on_error(
    monkeypatch,
) -> None:
    screen = MainScreen(owner="owner", repo="repo", pr_number=123)
    calls: list[dict[str, object]] = []

    def run_worker(work: object, **kwargs: object) -> object:
        calls.append(kwargs)
        close = getattr(work, "close", None)
        if isinstance(close, Callable):
            close()
        return object()

    monkeypatch.setattr(screen, "run_worker", run_worker)

    screen._handle_review_submit(("APPROVE", ""))

    assert calls == [
        {
            "exclusive": False,
            "name": "_submit_review",
            "exit_on_error": False,
        }
    ]


@pytest.mark.parametrize("kind", ["inline", "file", "reply"])
@pytest.mark.parametrize(
    ("suspended", "submitting"), [(False, False), (True, False), (False, True)]
)
def test_review_ignores_unfinished_input_but_waits_for_comment_submission(
    monkeypatch: pytest.MonkeyPatch,
    kind: EditorKind,
    suspended: bool,
    submitting: bool,
) -> None:
    screen = MainScreen(owner="owner", repo="repo", pr_number=123)
    screen.current_tab = 1
    screen.store.state.pr = PR(number=123)
    saved = PendingReviewComment(body="Saved comment", path="test.py", line=1)
    screen.store.state.pending_review.comments = [saved]
    screen.store.state.pending_review.body = "Saved review summary"
    view = DiffView(screen.store)
    session = ComposerSession(
        kind=kind,
        title="Comment",
        context="test.py:1",
        buffer=CommentEditorBuffer(text="Unfinished input", submitting=submitting),
        suspended=suspended,
    )
    view._composer.session = session
    app = Mock(spec=["push_screen"])
    notify = Mock()
    monkeypatch.setattr(screen, "notify", notify)
    with (
        patch.object(MainScreen, "app", app),
        patch.object(MainScreen, "file_changes", SimpleNamespace(diff_view=view)),
    ):
        screen.action_review()

    if submitting:
        app.push_screen.assert_not_called()
        notify.assert_called_once_with(
            "Wait for the comment submission to finish", severity="warning"
        )
    else:
        app.push_screen.assert_called_once()
        review, callback = app.push_screen.call_args.args
        assert isinstance(review, ReviewSubmitScreen)
        assert review._pending_comments == [saved]
        assert review._pending_comments_count == 1
        assert review._initial_body == "Saved review summary"
        assert callback == screen._handle_review_submit
        notify.assert_not_called()
    assert view._composer.session is session
    assert session.buffer.text == "Unfinished input"
    assert screen.store.state.pending_review.comments == [saved]
    assert saved.body == "Saved comment"


@pytest.mark.asyncio
async def test_review_submits_saved_pending_without_losing_unfinished_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_initial_loads(monkeypatch)
    service = FakeReviewService()
    service.review_comments_result = [
        PRComment(
            id=501,
            body="Saved comment",
            path="test.py",
            line=1,
            side="RIGHT",
            pull_request_review_id=service.submitted_review.id,
        )
    ]
    submit = AsyncMock(side_effect=GitHubError("Network unavailable"))
    monkeypatch.setattr(service, "submit_review", submit)
    app = RitApp(owner="owner", repo="repo", pr_number=123)
    async with app.run_test(size=(110, 36)) as pilot:
        screen = cast(MainScreen, app.screen)
        store = screen.store
        monkeypatch.setattr(store, "_service", service)
        store.state.pr = PR(number=123)
        source = parse_patch("@@ -1 +1 @@\n-old\n+new", "test.py")
        store.state.file_diffs = {"test.py": source}
        saved = store.save_pending_inline_comment(
            "Saved comment", path="test.py", line=1, side="RIGHT"
        )
        store.state.pending_review.body = "Saved review summary"
        screen.switch_tab(1)
        view = screen.file_changes.diff_view
        await view.show_diff("test.py", source)
        view.cursor_line = 1
        view._comment_cursor_index = 1
        assert await view.open_inline_comment_editor(edit=True)
        body = view.query_one(TextArea)
        await wait_until(lambda: body.has_focus)
        body.move_cursor(body.document.end)
        body.insert(" with unfinished edits")
        history = body.history
        selection = body.selection
        await pilot.press("escape")
        session = view._composer.session
        assert session is not None and session.suspended

        for fail in (True, False):
            if not fail:
                submit.side_effect = None
                submit.return_value = service.submitted_review
            await pilot.press("ctrl+s")
            await wait_until(lambda: isinstance(app.screen, ReviewSubmitScreen))
            review = cast(ReviewSubmitScreen, app.screen)
            assert review._pending_comments == [saved]
            assert review._pending_comments_count == 1
            assert review.query_one("#review-submit-body", TextArea).text == (
                "Saved review summary"
            )
            await pilot.press("ctrl+enter")
            await wait_until(lambda: app.screen is screen)
            await screen.workers.wait_for_complete()
            assert submit.call_args.args == (123,)
            assert submit.call_args.kwargs == {
                "event": "COMMENT",
                "body": "Saved review summary",
                "comments": [saved],
            }
            assert store.state.pending_review.comments == ([saved] if fail else [])
            assert saved.body == "Saved comment"
            assert view._composer.session is session
            assert session.suspended
            assert session.buffer.text == "Saved comment with unfinished edits"
            assert session.buffer.history is history
            assert session.buffer.selection == selection

        assert submit.await_count == 2
        await pilot.press("c")
        await wait_until(lambda: bool(view.query(TextArea)))
        body = view.query_one(TextArea)
        await wait_until(lambda: body.has_focus)
        assert body.text == "Saved comment with unfinished edits"
        await pilot.press("ctrl+z")
        assert body.text == "Saved comment"


def test_review_submit_error_message_uses_github_error_detail() -> None:
    error = GitHubError(
        "gh: Could not approve for pull request review. "
        "Can not approve your own pull request"
    )

    assert (
        MainScreen._worker_error_message(
            cast(Worker[object], _WorkerStub(error)), "Failed to submit review"
        )
        == "gh: Could not approve for pull request review. "
        "Can not approve your own pull request"
    )
