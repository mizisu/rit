from datetime import datetime, timezone

from rit.state.discussion_signature import (
    discussion_render_signature,
    normalized_author_login,
)
from rit.state.models import (
    NodeList,
    PR,
    PRComment,
    PRIssueComment,
    PRReview,
    PRUser,
    ReviewThread,
    ReviewState,
)


def test_discussion_signature_ignores_metadata_only_thread_changes() -> None:
    fast_comment = PRComment(
        id=100,
        body="Same comment",
        user=PRUser(login="coderabbitai[bot]"),
        path="app.py",
        line=12,
        side="RIGHT",
        pull_request_review_id=10,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    full_comment = PRComment(
        id=100,
        body="Same comment",
        user=PRUser(login="coderabbitai"),
        path="app.py",
        line=None,
        original_line=12,
        side="",
        pull_request_review_id=10,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    fast_pr = PR(
        number=123,
        body="Same body",
        review_threads_connection=NodeList(
            nodes=[
                ReviewThread.model_validate(
                    {
                        "id": "",
                        "isResolved": False,
                        "path": "app.py",
                        "line": 12,
                        "comments": {"nodes": [fast_comment]},
                    }
                )
            ]
        ),
    )
    full_pr = PR(
        number=123,
        body="Same body",
        review_threads_connection=NodeList(
            nodes=[
                ReviewThread.model_validate(
                    {
                        "id": "thread-100",
                        "isResolved": True,
                        "path": "app.py",
                        "line": 12,
                        "comments": {"nodes": [full_comment]},
                    }
                )
            ]
        ),
    )

    assert discussion_render_signature(fast_pr) == discussion_render_signature(full_pr)


def test_discussion_signature_detects_rendered_content_changes() -> None:
    created_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    before = PR(
        number=123,
        body="Body",
        reviews_connection=NodeList(
            nodes=[
                PRReview(
                    id=10,
                    body="review",
                    user=PRUser(login="alice"),
                    state=ReviewState.COMMENTED,
                    created_at=created_at,
                    submitted_at=created_at,
                )
            ]
        ),
        issue_comments_connection=NodeList(
            nodes=[
                PRIssueComment(
                    id=20,
                    body="issue",
                    user=PRUser(login="bob"),
                    created_at=created_at,
                    updated_at=created_at,
                )
            ]
        ),
    )
    after = before.model_copy(update={"body": "Changed body"})

    assert discussion_render_signature(before) != discussion_render_signature(after)


def test_normalized_author_login_removes_bot_suffix() -> None:
    assert normalized_author_login(PRUser(login="coderabbitai[bot]")) == "coderabbitai"
    assert normalized_author_login(PRUser(login="alice")) == "alice"
    assert normalized_author_login(None) == ""
