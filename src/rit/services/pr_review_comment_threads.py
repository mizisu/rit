from __future__ import annotations

from rit.state.models import (
    NodeList,
    PRComment,
    ReviewThread,
    group_comments_into_threads,
)

__all__ = ("review_threads_from_rest_comments",)


def review_threads_from_rest_comments(
    comments: list[PRComment],
) -> list[ReviewThread]:
    """Build review threads from REST review comments."""
    threads: list[ReviewThread] = []
    normalized_comments = [_comment_with_normalized_author(comment) for comment in comments]
    for thread in group_comments_into_threads(normalized_comments):
        root = thread.root_comment
        threads.append(
            ReviewThread.model_validate(
                {
                    "id": "",
                    "isResolved": False,
                    "path": root.path,
                    "line": root.line,
                    "originalLine": root.original_line,
                    "diffSide": root.side,
                    "comments": NodeList(nodes=thread.all_comments),
                }
            )
        )
    return threads


def _comment_with_normalized_author(comment: PRComment) -> PRComment:
    user = comment.user
    if user is None or not user.login.endswith("[bot]"):
        return comment

    return comment.model_copy(
        update={"user": user.model_copy(update={"login": user.login[: -len("[bot]")]})}
    )
