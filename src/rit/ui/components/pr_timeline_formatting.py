from __future__ import annotations

from collections.abc import Sequence

from rich.markup import escape

from rit.state.models import (
    PRComment,
    PRReview,
    PRTimelineEvent,
    PRUser,
    ReviewThreadInfo,
)
from rit.ui.icons import get_file_icon

__all__ = (
    "author_display_name",
    "format_thread_title",
    "pending_review_summary_header",
    "resolved_thread_title",
    "thread_title",
)


_EVENT_LABELS = {
    "HeadRefForcePushedEvent": "[#eed49f]↺ force-pushed[/]",
    "ReadyForReviewEvent": "[#a6da95]◎ marked ready for review[/]",
    "ConvertToDraftEvent": "[#a5adcb]◌ converted to draft[/]",
    "MergedEvent": "[#c6a0f6]◉ merged[/]",
    "ClosedEvent": "[#ed8796]⊘ closed this PR[/]",
    "ReopenedEvent": "[#a6da95]◎ reopened this PR[/]",
    "ReviewRequestedEvent": "[#eed49f]○ requested review from[/]",
    "ReviewRequestRemovedEvent": "[#a5adcb]— removed review request for[/]",
}


def timeline_event_header(events: Sequence[PRTimelineEvent], *, time_str: str) -> str:
    """Format an activity row, escaping GitHub-provided text as literal content."""
    event = events[-1]
    actor = escape((event.actor or "unknown").removesuffix("[bot]"))
    if event.kind == "PullRequestCommit":
        if len(events) > 1:
            action = f"[#8aadf4]{len(events)} commits[/]"
        else:
            action = (
                f"[#8aadf4]committed {escape(event.commit_oid[:7])}[/]"
                f" — {escape(event.commit_message)}"
            )
    else:
        action = _EVENT_LABELS[event.kind]
        if (
            event.kind == "HeadRefForcePushedEvent"
            and event.before_oid
            and event.after_oid
        ):
            action += f" {escape(event.before_oid[:7])} → {escape(event.after_oid[:7])}"
        elif event.kind == "MergedEvent":
            if event.merge_ref_name:
                action += f" into {escape(event.merge_ref_name)}"
            if event.commit_oid:
                action += f" · {escape(event.commit_oid[:7])}"
        elif event.kind in {"ReviewRequestedEvent", "ReviewRequestRemovedEvent"}:
            target = (
                f"@{event.reviewer}"
                if event.reviewer
                else event.reviewer_team or "unknown"
            )
            action += f" {escape(target)}"
    header = f"[bold]{actor}[/] {action}"
    return f"{header} · {time_str}" if time_str else header


def author_display_name(user: PRUser | None) -> str:
    """Return a compact author name for timeline headers."""
    if user is None or not user.login:
        return "unknown"
    if user.login.endswith("[bot]"):
        return user.login[: -len("[bot]")]
    return user.login


def thread_title(comment: PRComment, *, is_resolved: bool) -> str:
    """Return a review-thread title for a root comment."""
    return format_thread_title(
        path=comment.path,
        line=comment.anchor_line,
        author=author_display_name(comment.user),
        is_resolved=is_resolved,
    )


def resolved_thread_title(
    *,
    root_comment: PRComment | None,
    thread_info: ReviewThreadInfo | None,
    is_resolved: bool,
) -> str | None:
    """Return the title for an updated review thread card."""
    author = author_display_name(root_comment.user if root_comment else None)
    if thread_info is not None:
        return format_thread_title(
            path=thread_info.path,
            line=thread_info.line,
            author=author,
            is_resolved=is_resolved,
        )
    if root_comment is not None:
        return thread_title(root_comment, is_resolved=is_resolved)
    return None


def format_thread_title(
    *,
    path: str,
    line: int | None,
    author: str,
    is_resolved: bool,
) -> str:
    """Return a review-thread title from display parts."""
    line_info = f":{line}" if line else ""
    title = f"@{author} on {get_file_icon(path)} {path}{line_info}"
    if is_resolved:
        return f"{chr(0x2713)} Resolved: {title}"
    return title


def pending_review_summary_header(
    review: PRReview,
    *,
    thread_count: int,
    time_str: str,
) -> str:
    """Return the header for a pending review summary card."""
    label = "thread" if thread_count == 1 else "threads"
    title = (
        f"[bold]{author_display_name(review.user)}[/] "
        f"[#eed49f]pending[/] [#6e738d]{thread_count} {label}[/]"
    )
    if time_str:
        return f"{title} {time_str}"
    return title
