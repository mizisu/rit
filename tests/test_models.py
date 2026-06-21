"""Tests for data models."""

from datetime import datetime, timezone

from rit.state.models import (
    PRComment,
    PRReview,
    PRUser,
    CommentThread,
    NodeList,
    ReviewThread,
    group_comments_into_threads,
)


def make_comment(
    id: int,
    body: str,
    user: str = "user1",
    path: str = "test.py",
    line: int = 10,
    in_reply_to_id: int | None = None,
    created_at: datetime | None = None,
) -> PRComment:
    """Helper to create a PRComment for testing."""
    return PRComment(
        id=id,
        body=body,
        user=PRUser(login=user),
        path=path,
        line=line,
        original_line=None,
        side="RIGHT",
        created_at=created_at or datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        in_reply_to_id=in_reply_to_id,
        diff_hunk="@@ -10,3 +10,3 @@\n-old\n+new",
    )


class TestPRComment:
    """Tests for PRComment helpers."""

    def test_anchor_line_prefers_original_line_for_left_side(self) -> None:
        comment = PRComment(
            id=1,
            body="test",
            user=PRUser(login="user"),
            path="test.py",
            line=20,
            original_line=15,
            side="LEFT",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        assert comment.anchor_side == "old"
        assert comment.anchor_line == 15

    def test_anchor_line_prefers_new_line_for_right_side(self) -> None:
        comment = PRComment(
            id=1,
            body="test",
            user=PRUser(login="user"),
            path="test.py",
            line=20,
            original_line=15,
            side="RIGHT",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        assert comment.anchor_side == "new"
        assert comment.anchor_line == 20

    def test_anchor_side_infers_old_when_graphql_side_is_absent(self) -> None:
        comment = PRComment(
            id=1,
            body="test",
            user=PRUser(login="user"),
            path="test.py",
            line=None,
            original_line=15,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        assert comment.anchor_side == "old"
        assert comment.anchor_line == 15

    def test_anchor_side_infers_new_when_graphql_side_is_absent(self) -> None:
        comment = PRComment(
            id=1,
            body="test",
            user=PRUser(login="user"),
            path="test.py",
            line=20,
            original_line=15,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

        assert comment.anchor_side == "new"
        assert comment.anchor_line == 20

    def test_rest_subject_type_is_preserved(self) -> None:
        comment = PRComment.model_validate(
            {
                "id": 1,
                "body": "file note",
                "path": "test.py",
                "subject_type": "file",
            }
        )

        assert comment.subject_type == "file"


class TestPRReview:
    """Tests for PR review API compatibility helpers."""

    def test_rest_node_id_is_preserved(self) -> None:
        review = PRReview.model_validate(
            {"id": 10, "node_id": "PRR_node", "state": "PENDING"}
        )

        assert review.id == 10
        assert review.node_id == "PRR_node"

    def test_graphql_created_at_is_preserved(self) -> None:
        review = PRReview.model_validate(
            {
                "databaseId": 10,
                "state": "PENDING",
                "createdAt": "2026-06-18T06:25:36Z",
                "submittedAt": None,
            }
        )

        assert review.created_at == datetime(
            2026, 6, 18, 6, 25, 36, tzinfo=timezone.utc
        )


class TestReviewThread:
    """Tests for GraphQL review thread anchor helpers."""

    def test_anchor_side_prefers_graphql_diff_side(self) -> None:
        comment = PRComment(
            body="test",
            path="test.py",
            line=20,
            original_line=10,
        )
        thread = ReviewThread(
            path="test.py",
            line=20,
            original_line=10,
            diff_side="LEFT",
            comments_connection=NodeList(nodes=[comment]),
        )

        assert thread.anchor_side == "old"
        assert thread.anchor_line == 10

    def test_anchor_side_falls_back_to_root_comment_side(self) -> None:
        comment = PRComment(
            body="test",
            path="test.py",
            line=20,
            original_line=10,
            side="LEFT",
        )
        thread = ReviewThread(
            path="test.py",
            line=20,
            original_line=10,
            comments_connection=NodeList(nodes=[comment]),
        )

        assert thread.anchor_side == "old"
        assert thread.anchor_line == 10

    def test_nullable_graphql_start_diff_side_is_allowed(self) -> None:
        thread = ReviewThread.model_validate(
            {
                "path": "test.py",
                "line": 20,
                "originalLine": 10,
                "diffSide": "RIGHT",
                "startDiffSide": None,
                "comments": {"nodes": []},
            }
        )

        assert thread.start_diff_side is None
        assert thread.anchor_side == "new"


class TestCommentThread:
    """Tests for CommentThread dataclass."""

    def test_all_comments_includes_root_and_replies(self) -> None:
        """Test that all_comments returns root and replies in order."""
        root = make_comment(1, "Root comment")
        reply1 = make_comment(
            2,
            "Reply 1",
            in_reply_to_id=1,
            created_at=datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc),
        )
        reply2 = make_comment(
            3,
            "Reply 2",
            in_reply_to_id=1,
            created_at=datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc),
        )

        thread = CommentThread(root_comment=root, replies=[reply2, reply1])
        all_comments = thread.all_comments

        assert len(all_comments) == 3
        assert all_comments[0].id == 1  # root
        assert all_comments[1].id == 2  # reply1 (earlier)
        assert all_comments[2].id == 3  # reply2 (later)

    def test_all_comments_sorts_missing_and_aware_reply_dates(self) -> None:
        root = make_comment(1, "Root comment")
        missing_date_reply = PRComment(
            id=2,
            body="Missing date",
            user=PRUser(login="user"),
            path="test.py",
            line=10,
            in_reply_to_id=1,
        )
        aware_reply = make_comment(
            3,
            "Aware date",
            in_reply_to_id=1,
            created_at=datetime(2024, 1, 1, 13, tzinfo=timezone.utc),
        )

        thread = CommentThread(
            root_comment=root,
            replies=[aware_reply, missing_date_reply],
        )

        assert [comment.id for comment in thread.all_comments] == [1, 2, 3]

    def test_file_path_property(self) -> None:
        """Test file_path property returns root comment's path."""
        root = make_comment(1, "Root", path="src/main.py")
        thread = CommentThread(root_comment=root)

        assert thread.file_path == "src/main.py"

    def test_line_property_prefers_new_line(self) -> None:
        """Test line property prefers new line over original."""
        root = PRComment(
            id=1,
            body="test",
            user=PRUser(login="user"),
            path="test.py",
            line=20,
            original_line=15,
            side="RIGHT",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        thread = CommentThread(root_comment=root)

        assert thread.line == 20

    def test_line_property_falls_back_to_original(self) -> None:
        """Test line property falls back to original_line if line is None."""
        root = PRComment(
            id=1,
            body="test",
            user=PRUser(login="user"),
            path="test.py",
            line=None,
            original_line=15,
            side="LEFT",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        thread = CommentThread(root_comment=root)

        assert thread.line == 15


class TestGroupCommentsIntoThreads:
    """Tests for group_comments_into_threads function."""

    def test_empty_list(self) -> None:
        """Test with empty comment list."""
        threads = group_comments_into_threads([])
        assert threads == []

    def test_single_root_comment(self) -> None:
        """Test with single root comment (no replies)."""
        comment = make_comment(1, "Single comment")
        threads = group_comments_into_threads([comment])

        assert len(threads) == 1
        assert threads[0].root_comment.id == 1
        assert threads[0].replies == []

    def test_multiple_root_comments(self) -> None:
        """Test with multiple independent root comments."""
        c1 = make_comment(
            1, "Comment 1", created_at=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        )
        c2 = make_comment(
            2, "Comment 2", created_at=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc)
        )
        c3 = make_comment(
            3, "Comment 3", created_at=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
        )

        threads = group_comments_into_threads([c1, c2, c3])

        assert len(threads) == 3
        # Should be sorted by creation time
        assert threads[0].root_comment.id == 3  # earliest
        assert threads[1].root_comment.id == 1
        assert threads[2].root_comment.id == 2  # latest

    def test_missing_and_aware_root_comment_dates_sort_together(self) -> None:
        missing_date = PRComment(
            id=1,
            body="Missing date",
            user=PRUser(login="user"),
            path="test.py",
            line=10,
        )
        aware_date = make_comment(
            2,
            "Aware date",
            created_at=datetime(2024, 1, 1, 10, tzinfo=timezone.utc),
        )

        threads = group_comments_into_threads([aware_date, missing_date])

        assert [thread.root_comment.id for thread in threads] == [1, 2]

    def test_thread_with_replies(self) -> None:
        """Test grouping replies with their root comment."""
        root = make_comment(
            1, "Root", created_at=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        )
        reply1 = make_comment(
            2,
            "Reply 1",
            in_reply_to_id=1,
            created_at=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
        )
        reply2 = make_comment(
            3,
            "Reply 2",
            in_reply_to_id=1,
            created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        )

        threads = group_comments_into_threads([reply2, root, reply1])

        assert len(threads) == 1
        assert threads[0].root_comment.id == 1
        assert len(threads[0].replies) == 2
        assert {r.id for r in threads[0].replies} == {2, 3}

    def test_nested_replies(self) -> None:
        """Test that nested replies are grouped to the root."""
        root = make_comment(
            1, "Root", created_at=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        )
        reply1 = make_comment(
            2,
            "Reply to root",
            in_reply_to_id=1,
            created_at=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
        )
        reply2 = make_comment(
            3,
            "Reply to reply",
            in_reply_to_id=2,  # Reply to reply1, not root
            created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        )

        threads = group_comments_into_threads([root, reply1, reply2])

        assert len(threads) == 1
        assert threads[0].root_comment.id == 1
        # Both replies should be grouped under the root
        assert len(threads[0].replies) == 2
        assert {r.id for r in threads[0].replies} == {2, 3}

    def test_multiple_threads(self) -> None:
        """Test multiple independent threads."""
        root1 = make_comment(
            1,
            "Thread 1 root",
            path="file1.py",
            created_at=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
        )
        reply1 = make_comment(
            2,
            "Thread 1 reply",
            path="file1.py",
            in_reply_to_id=1,
            created_at=datetime(2024, 1, 1, 11, 0, tzinfo=timezone.utc),
        )
        root2 = make_comment(
            3,
            "Thread 2 root",
            path="file2.py",
            created_at=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc),
        )

        threads = group_comments_into_threads([root1, reply1, root2])

        assert len(threads) == 2
        # Sorted by creation time
        assert threads[0].root_comment.id == 3  # Thread 2 (earlier)
        assert threads[1].root_comment.id == 1  # Thread 1 (later)
        assert len(threads[1].replies) == 1

    def test_orphan_reply_becomes_root(self) -> None:
        """Test that replies to non-existent comments become roots."""
        orphan = make_comment(
            1,
            "Orphan reply",
            in_reply_to_id=999,  # Non-existent parent
        )

        threads = group_comments_into_threads([orphan])

        assert len(threads) == 1
        assert threads[0].root_comment.id == 1
        assert threads[0].replies == []
