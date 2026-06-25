import builtins
from datetime import datetime, timezone

import rit.state.discussion_signature as discussion_signature_module
from rit.state.discussion_signature import (
    discussion_render_signature,
    normalized_author_login,
    thread_render_signature,
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


def test_discussion_signature_reuses_empty_sections(monkeypatch) -> None:
    def tuple_for_non_empty(values=()):
        materialized = builtins.tuple(values)
        if not materialized:
            raise AssertionError("empty discussion signature sections should be reused")
        return materialized

    monkeypatch.setattr(
        discussion_signature_module,
        "tuple",
        tuple_for_non_empty,
        raising=False,
    )

    assert discussion_render_signature(PR(number=123, body="Body")) == (
        "Body",
        (),
        (),
        (),
    )


def test_thread_signature_reuses_empty_comment_section(monkeypatch) -> None:
    def tuple_for_non_empty(values=()):
        materialized = builtins.tuple(values)
        if not materialized:
            raise AssertionError("empty thread comment signatures should be reused")
        return materialized

    monkeypatch.setattr(
        discussion_signature_module,
        "tuple",
        tuple_for_non_empty,
        raising=False,
    )

    assert thread_render_signature(ReviewThread(path="app.py", line=12)) == (
        "app.py",
        12,
        (),
    )


def test_discussion_signature_reuses_singleton_sections(monkeypatch) -> None:
    created_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    review = PRReview(
        id=10,
        body="review",
        user=PRUser(login="alice"),
        state=ReviewState.COMMENTED,
        created_at=created_at,
        submitted_at=created_at,
    )
    issue_comment = PRIssueComment(
        id=20,
        body="issue",
        user=PRUser(login="bob"),
        created_at=created_at,
        updated_at=created_at,
    )
    review_comment = PRComment(
        id=30,
        body="thread",
        user=PRUser(login="carol"),
        path="app.py",
        line=12,
        created_at=created_at,
        updated_at=created_at,
    )
    thread = ReviewThread.model_validate(
        {
            "path": "app.py",
            "line": 12,
            "comments": {"nodes": [review_comment]},
        }
    )
    pr = PR(
        number=123,
        body="Body",
        reviews_connection=NodeList(nodes=[review]),
        issue_comments_connection=NodeList(nodes=[issue_comment]),
        review_threads_connection=NodeList(nodes=[thread]),
    )

    monkeypatch.setattr(
        discussion_signature_module,
        "tuple",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("singleton discussion signature sections should be reused")
        ),
        raising=False,
    )

    signature = discussion_render_signature(pr)

    assert signature[1][0][0] == 10
    assert signature[2][0][0] == 20
    assert signature[3][0][2][0][0] == 30


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
