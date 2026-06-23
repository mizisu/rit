from rit.services.pr_review_payload import (
    pending_review_payload,
    review_event_payload,
    submit_review_payload,
)
from rit.state.models import PendingReviewComment


def _draft(
    *,
    path: str = "src/app.py",
    line: int = 42,
    side: str = "RIGHT",
    body: str = "ship it",
) -> PendingReviewComment:
    return PendingReviewComment(
        path=path,
        line=line,
        side=side,  # type: ignore[arg-type]
        body=body,
    )


def test_pending_review_payload_serializes_comments_and_optional_fields() -> None:
    payload = pending_review_payload(
        comments=[_draft()],
        body="summary",
        commit_id="deadbeef",
    )

    assert payload == {
        "comments": [
            {
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ],
        "body": "summary",
        "commit_id": "deadbeef",
    }


def test_pending_review_payload_omits_empty_optional_fields() -> None:
    payload = pending_review_payload(comments=[_draft()], body="", commit_id="")

    assert payload == {
        "comments": [
            {
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ]
    }


def test_review_event_payload_omits_empty_body() -> None:
    assert review_event_payload(event="COMMENT", body="") == {"event": "COMMENT"}
    assert review_event_payload(event="COMMENT", body="looks good") == {
        "event": "COMMENT",
        "body": "looks good",
    }


def test_submit_review_payload_includes_event_comments_and_commit_id() -> None:
    payload = submit_review_payload(
        event="COMMENT",
        body=None,
        comments=[_draft()],
        commit_id="deadbeef",
    )

    assert payload == {
        "event": "COMMENT",
        "commit_id": "deadbeef",
        "comments": [
            {
                "path": "src/app.py",
                "line": 42,
                "side": "RIGHT",
                "body": "ship it",
            }
        ],
    }

