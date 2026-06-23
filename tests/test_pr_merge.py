from rit.state.models import (
    PR,
    PRComment,
    PRIssueComment,
    PRReview,
    ReviewThread,
)
from rit.state.pr_merge import merge_pr_discussion, merge_pr_summary


def test_merge_pr_summary_keeps_loaded_discussion_state() -> None:
    review = PRReview(id=10, body="review")
    issue_comment = PRIssueComment(id=20, body="issue")
    thread_comment = PRComment(id=30, body="thread", path="app.py", line=1)
    thread = ReviewThread.model_validate(
        {
            "id": "thread-30",
            "path": "app.py",
            "line": 1,
            "comments": {"nodes": [thread_comment]},
        }
    )
    existing = PR(number=123, body="discussion body")
    summary = PR(number=123, title="new title", body="summary body", changedFiles=4)

    merged = merge_pr_summary(
        summary,
        existing=existing,
        reviews=[review],
        issue_comments=[issue_comment],
        review_threads=[thread],
    )

    assert merged.title == "new title"
    assert merged.changed_files == 4
    assert merged.body == "discussion body"
    assert merged.reviews == [review]
    assert merged.issue_comments == [issue_comment]
    assert merged.review_threads == [thread]


def test_merge_pr_summary_returns_summary_when_no_pr_exists() -> None:
    summary = PR(number=123, title="new title")

    assert merge_pr_summary(
        summary,
        existing=None,
        reviews=[],
        issue_comments=[],
        review_threads=[],
    ) == summary


def test_merge_pr_discussion_preserves_existing_body_when_discussion_body_is_empty() -> (
    None
):
    review = PRReview(id=10, body="review")
    existing = PR(number=123, body="existing body", title="Loaded")

    merged = merge_pr_discussion(
        existing=existing,
        pr_number=123,
        body="",
        reviews=[review],
        issue_comments=[],
        review_threads=[],
    )

    assert merged.title == "Loaded"
    assert merged.body == "existing body"
    assert merged.reviews == [review]


def test_merge_pr_discussion_builds_placeholder_when_pr_is_missing() -> None:
    issue_comment = PRIssueComment(id=20, body="issue")

    merged = merge_pr_discussion(
        existing=None,
        pr_number=123,
        body="discussion body",
        reviews=[],
        issue_comments=[issue_comment],
        review_threads=[],
    )

    assert merged.number == 123
    assert merged.body == "discussion body"
    assert merged.issue_comments == [issue_comment]
