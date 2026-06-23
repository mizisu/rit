from __future__ import annotations

from collections.abc import Sequence

from rit.state.models import PendingReviewComment

__all__ = (
    "pending_review_payload",
    "review_event_payload",
    "submit_review_payload",
)


def pending_review_payload(
    *,
    comments: Sequence[PendingReviewComment],
    body: str | None = None,
    commit_id: str | None = None,
) -> dict[str, object]:
    """Build a REST payload for creating a pending review."""
    payload: dict[str, object] = {
        "comments": [_comment_payload(comment) for comment in comments]
    }
    _add_optional(payload, "body", body)
    _add_optional(payload, "commit_id", commit_id)
    return payload


def review_event_payload(
    *,
    event: str,
    body: str | None = None,
) -> dict[str, object]:
    """Build a REST payload for submitting an existing pending review."""
    payload: dict[str, object] = {"event": event}
    _add_optional(payload, "body", body)
    return payload


def submit_review_payload(
    *,
    event: str,
    body: str | None = None,
    comments: Sequence[PendingReviewComment] | None = None,
    commit_id: str | None = None,
) -> dict[str, object]:
    """Build a REST payload for submitting a PR review."""
    payload = review_event_payload(event=event, body=body)
    _add_optional(payload, "commit_id", commit_id)
    if comments:
        payload["comments"] = [_comment_payload(comment) for comment in comments]
    return payload


def _comment_payload(comment: PendingReviewComment) -> dict[str, object]:
    return {
        "path": comment.path,
        "line": comment.line,
        "side": comment.side,
        "body": comment.body,
    }


def _add_optional(
    payload: dict[str, object],
    key: str,
    value: str | None,
) -> None:
    if value is not None and value != "":
        payload[key] = value
