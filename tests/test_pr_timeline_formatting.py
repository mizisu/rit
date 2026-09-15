from datetime import UTC, datetime

import pytest
from rich.text import Text

from rit.state.models import (
    PRComment,
    PRReview,
    PRTimelineEvent,
    PRUser,
    ReviewThreadInfo,
)
from rit.ui.components.pr_timeline_formatting import (
    author_display_name,
    pending_review_summary_header,
    resolved_thread_title,
    thread_title,
    timeline_event_header,
)
from rit.ui.icons import get_file_icon


@pytest.mark.parametrize(
    "kind, fields, expected",
    [
        ("ReadyForReviewEvent", {}, "alice ◎ marked ready for review"),
        ("ConvertToDraftEvent", {}, "alice ◌ converted to draft"),
        ("ClosedEvent", {}, "alice ⊘ closed this PR"),
        ("ReopenedEvent", {}, "alice ◎ reopened this PR"),
        (
            "MergedEvent",
            {"merge_ref_name": "release/[red]", "commit_oid": "abcdef1234"},
            "alice ◉ merged into release/[red] · abcdef1",
        ),
        (
            "HeadRefForcePushedEvent",
            {"before_oid": "abcdef1234", "after_oid": "1234567abc"},
            "alice ↺ force-pushed abcdef1 → 1234567",
        ),
        ("HeadRefForcePushedEvent", {"actor": None}, "unknown ↺ force-pushed"),
        (
            "ReviewRequestedEvent",
            {"reviewer": "bob"},
            "alice ○ requested review from @bob",
        ),
        (
            "ReviewRequestedEvent",
            {"reviewer_team": "Backend [red]"},
            "alice ○ requested review from Backend [red]",
        ),
        (
            "ReviewRequestRemovedEvent",
            {"reviewer": "bob"},
            "alice — removed review request for @bob",
        ),
        ("ReviewRequestRemovedEvent", {}, "alice — removed review request for unknown"),
        (
            "PullRequestCommit",
            {
                "actor": "Guest [red]",
                "commit_oid": "abcdef1234",
                "commit_message": "Keep [red]literal[/]",
            },
            "Guest [red] committed abcdef1 — Keep [red]literal[/]",
        ),
    ],
)
def test_activity_labels_keep_state_and_external_text_visible(
    kind, fields, expected
) -> None:
    event = PRTimelineEvent.model_validate(
        {
            "id": "event-1",
            "kind": kind,
            "actor": "alice",
            "created_at": datetime(2026, 6, 18, tzinfo=UTC),
            **fields,
        }
    )

    header = timeline_event_header([event], time_str="2h ago")

    assert Text.from_markup(header).plain == f"{expected} · 2h ago"


def test_author_display_name_normalizes_missing_and_bot_logins() -> None:
    assert author_display_name(None) == "unknown"
    assert author_display_name(PRUser(login="")) == "unknown"
    assert author_display_name(PRUser(login="coderabbitai[bot]")) == "coderabbitai"
    assert author_display_name(PRUser(login="alice")) == "alice"


def test_thread_title_includes_author_icon_path_line_and_resolved_prefix() -> None:
    comment = PRComment(
        body="note",
        user=PRUser(login="coderabbitai[bot]"),
        path="src/app.py",
        line=42,
        side="RIGHT",
    )

    assert (
        thread_title(comment, is_resolved=False)
        == f"@coderabbitai on {get_file_icon('src/app.py')} src/app.py:42"
    )
    assert thread_title(comment, is_resolved=True).startswith(
        f"{chr(0x2713)} Resolved: "
    )


def test_file_thread_title_ignores_github_line_one_placeholder() -> None:
    comment = PRComment(
        body="whole file",
        user=PRUser(login="alice"),
        path="src/app.py",
        line=1,
        side="RIGHT",
        subject_type="file",
    )

    assert (
        thread_title(comment, is_resolved=False)
        == f"@alice on {get_file_icon('src/app.py')} src/app.py"
    )


def test_pending_review_summary_header_includes_count_and_optional_time() -> None:
    review = PRReview(
        user=PRUser(login="alice[bot]"),
        created_at=datetime(2026, 6, 18, 6, 25, tzinfo=UTC),
    )

    assert (
        pending_review_summary_header(review, thread_count=1, time_str="2h ago")
        == "[bold]alice[/] [#eed49f]pending[/] [#6e738d]1 thread[/] 2h ago"
    )
    assert (
        pending_review_summary_header(review, thread_count=2, time_str="")
        == "[bold]alice[/] [#eed49f]pending[/] [#6e738d]2 threads[/]"
    )


def test_resolved_thread_title_prefers_authoritative_thread_metadata() -> None:
    root = PRComment(
        id=100,
        body="root",
        user=PRUser(login="alice[bot]"),
        path="old.py",
        line=5,
    )
    thread_info = ReviewThreadInfo(
        thread_id="thread-100",
        is_resolved=True,
        path="new.py",
        line=42,
        root_comment_id=100,
    )

    assert (
        resolved_thread_title(
            root_comment=root,
            thread_info=thread_info,
            is_resolved=True,
        )
        == f"{chr(0x2713)} Resolved: @alice on {get_file_icon('new.py')} new.py:42"
    )


def test_resolved_thread_title_falls_back_to_root_comment() -> None:
    root = PRComment(
        id=100,
        body="root",
        user=PRUser(login="bob"),
        path="src/app.py",
        line=9,
    )

    assert (
        resolved_thread_title(
            root_comment=root,
            thread_info=None,
            is_resolved=False,
        )
        == f"@bob on {get_file_icon('src/app.py')} src/app.py:9"
    )
    assert (
        resolved_thread_title(
            root_comment=None,
            thread_info=None,
            is_resolved=False,
        )
        is None
    )
