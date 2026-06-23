from __future__ import annotations

from rit.state.models import PRComment, PRReview, PRUser, ReviewThreadInfo
from rit.ui.icons import get_file_icon

__all__ = (
    "author_display_name",
    "format_thread_title",
    "pending_review_summary_header",
    "resolved_thread_title",
    "thread_title",
)


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
