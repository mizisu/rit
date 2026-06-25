from datetime import datetime, timezone

from rit.core.datetime_utils import datetime_sort_key
from rit.state.models import PR, PRIssueComment


def _issue_comments_module():
    import rit.state.issue_comments as issue_comments

    return issue_comments


def test_normalize_issue_comment_body_trims_text() -> None:
    issue_comments = _issue_comments_module()

    assert issue_comments.normalize_issue_comment_body("  hello  ") == "hello"


def test_normalize_issue_comment_body_rejects_empty_text() -> None:
    issue_comments = _issue_comments_module()

    try:
        issue_comments.normalize_issue_comment_body("   ")
    except ValueError as error:
        assert "empty" in str(error)
    else:
        raise AssertionError("empty issue comment should be rejected")


def test_insert_issue_comment_returns_created_at_sorted_list() -> None:
    issue_comments = _issue_comments_module()
    missing_date = PRIssueComment(id=1, body="missing date")
    latest = PRIssueComment(
        id=3,
        body="latest",
        created_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
    )
    middle = PRIssueComment(
        id=2,
        body="middle",
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    inserted = issue_comments.insert_issue_comment(
        [missing_date, latest],
        middle,
    )

    assert inserted == [missing_date, middle, latest]


def test_insert_issue_comment_skips_sort_for_first_comment(monkeypatch) -> None:
    issue_comments = _issue_comments_module()
    submitted = PRIssueComment(id=1, body="submitted")

    monkeypatch.setattr(
        issue_comments,
        "datetime_sort_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("first issue comment should not be sorted")
        ),
    )

    assert issue_comments.insert_issue_comment([], submitted) == [submitted]


def test_insert_issue_comment_appends_latest_without_full_sort(monkeypatch) -> None:
    issue_comments = _issue_comments_module()
    first = PRIssueComment(
        id=1,
        body="first",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    second = PRIssueComment(
        id=2,
        body="second",
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    submitted = PRIssueComment(
        id=3,
        body="submitted",
        created_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
    )
    sort_key_calls: list[datetime | None] = []

    def recording_sort_key(value: datetime | None) -> datetime:
        sort_key_calls.append(value)
        return datetime_sort_key(value)

    monkeypatch.setattr(issue_comments, "datetime_sort_key", recording_sort_key)

    assert issue_comments.insert_issue_comment([first, second], submitted) == [
        first,
        second,
        submitted,
    ]
    assert sort_key_calls == [second.created_at, submitted.created_at]


def test_apply_submitted_issue_comment_updates_list_and_pr_model() -> None:
    issue_comments = _issue_comments_module()
    existing = PRIssueComment(id=1, body="existing")
    submitted = PRIssueComment(
        id=2,
        body="submitted",
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    pr = PR(number=123, issue_comments_connection={"nodes": [existing]})

    projection = issue_comments.apply_submitted_issue_comment(
        pr=pr,
        comments=[existing],
        comment=submitted,
    )

    assert projection.issue_comments == [existing, submitted]
    assert projection.pr is not None
    assert projection.pr.issue_comments == [existing, submitted]
    assert pr.issue_comments == [existing]
