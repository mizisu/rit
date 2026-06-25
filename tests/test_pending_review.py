import pytest

from rit.core.types import DiffHunk, DiffLine, FileDiff
from rit.state import pending_review
from rit.state.models import PendingReviewComment, PRComment, PRReview, ReviewState
from rit.state.pending_review import (
    UnsupportedInlineCommentTarget,
    count_pending_file_comments,
    first_unsupported_comment,
    get_pending_file_comments,
    get_pending_inline_comment,
    plan_review_submission,
    remove_pending_comment,
    syncable_comments,
    upsert_pending_comment,
)


def _diff() -> FileDiff:
    return FileDiff(
        filename="a.py",
        hunks=[
            DiffHunk(
                old_start=4,
                old_count=2,
                new_start=7,
                new_count=2,
                lines=[
                    DiffLine(
                        old_line_no=4,
                        new_line_no=None,
                        old_content="old",
                        is_deleted=True,
                    ),
                    DiffLine(
                        old_line_no=None,
                        new_line_no=7,
                        new_content="new",
                        is_added=True,
                    ),
                ],
            )
        ],
    )


def test_upsert_pending_comment_replaces_target_and_sorts_by_anchor() -> None:
    comments = [
        PendingReviewComment(
            body="later",
            path="b.py",
            line=9,
            side="RIGHT",
        )
    ]

    comments, first = upsert_pending_comment(
        comments,
        body="first",
        path="a.py",
        line=7,
        side="RIGHT",
        is_diff_line=True,
    )
    comments, replacement = upsert_pending_comment(
        comments,
        body="replacement",
        path="a.py",
        line=7,
        side="RIGHT",
        is_diff_line=False,
    )

    assert first.body == "first"
    assert replacement.body == "replacement"
    assert replacement.is_diff_line is False
    assert [(item.path, item.line, item.side, item.body) for item in comments] == [
        ("a.py", 7, "RIGHT", "replacement"),
        ("b.py", 9, "RIGHT", "later"),
    ]


def test_upsert_pending_comment_skips_sort_for_first_draft(monkeypatch) -> None:
    monkeypatch.setattr(
        pending_review,
        "_sort_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("first pending draft should not be sorted")
        ),
    )

    comments, draft = upsert_pending_comment(
        [],
        body="first",
        path="a.py",
        line=7,
        side="RIGHT",
        is_diff_line=True,
    )

    assert comments == [draft]
    assert draft.body == "first"


def test_upsert_pending_comment_replaces_only_draft_without_sort(monkeypatch) -> None:
    existing = PendingReviewComment(
        body="old",
        path="a.py",
        line=7,
        side="RIGHT",
    )
    monkeypatch.setattr(
        pending_review,
        "_sort_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single pending draft replacement should not be sorted")
        ),
    )

    comments, draft = upsert_pending_comment(
        [existing],
        body="new",
        path="a.py",
        line=7,
        side="RIGHT",
        is_diff_line=True,
    )

    assert comments == [draft]
    assert draft.body == "new"


def test_upsert_pending_comment_appends_latest_without_full_sort(monkeypatch) -> None:
    first = PendingReviewComment(
        body="old",
        path="a.py",
        line=7,
        side="RIGHT",
    )
    second = PendingReviewComment(
        body="old",
        path="b.py",
        line=2,
        side="RIGHT",
    )
    sort_key_calls: list[tuple[str, int, str]] = []
    original_sort_key = pending_review._sort_key

    def recording_sort_key(
        comment: PendingReviewComment,
    ) -> tuple[str, int, str]:
        key = original_sort_key(comment)
        sort_key_calls.append(key)
        return key

    monkeypatch.setattr(pending_review, "_sort_key", recording_sort_key)

    comments, draft = upsert_pending_comment(
        [first, second],
        body="new",
        path="c.py",
        line=1,
        side="RIGHT",
        is_diff_line=True,
    )

    assert comments == [first, second, draft]
    assert sort_key_calls == [
        ("b.py", 2, "RIGHT"),
        ("c.py", 1, "RIGHT"),
    ]


def test_pending_comment_lookup_and_file_filter_are_targeted() -> None:
    comments = [
        PendingReviewComment(body="old", path="a.py", line=7, side="LEFT"),
        PendingReviewComment(body="new", path="a.py", line=7, side="RIGHT"),
        PendingReviewComment(body="other", path="b.py", line=1, side="RIGHT"),
    ]

    assert (
        get_pending_inline_comment(
            comments,
            path="a.py",
            line=7,
            side="LEFT",
        )
        == comments[0]
    )
    assert get_pending_file_comments(comments, "a.py") == comments[:2]
    assert count_pending_file_comments(comments, "a.py") == 2


def test_count_pending_file_comments_empty_and_single_skip_sum(monkeypatch) -> None:
    monkeypatch.setattr(
        pending_review,
        "sum",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty and single pending comment counts should not sum")
        ),
        raising=False,
    )

    assert count_pending_file_comments([], "a.py") == 0
    assert (
        count_pending_file_comments(
            [PendingReviewComment(body="note", path="a.py", line=1, side="RIGHT")],
            "a.py",
        )
        == 1
    )


def test_remove_pending_comment_returns_new_list_and_status() -> None:
    comments = [
        PendingReviewComment(body="keep", path="a.py", line=1, side="RIGHT"),
        PendingReviewComment(body="drop", path="a.py", line=2, side="RIGHT"),
    ]

    remaining, removed = remove_pending_comment(
        comments,
        path="a.py",
        line=2,
        side="RIGHT",
    )
    unchanged, missing_removed = remove_pending_comment(
        remaining,
        path="missing.py",
        line=2,
        side="RIGHT",
    )

    assert removed is True
    assert remaining == [comments[0]]
    assert missing_removed is False
    assert unchanged == remaining


def test_remove_pending_comment_reuses_list_when_target_is_missing() -> None:
    comments = [
        PendingReviewComment(body="keep", path="a.py", line=1, side="RIGHT"),
    ]

    unchanged, removed = remove_pending_comment(
        comments,
        path="missing.py",
        line=2,
        side="RIGHT",
    )

    assert removed is False
    assert unchanged is comments


def test_remove_pending_comment_empty_comments_skips_scan() -> None:
    class EmptyComments:
        def __len__(self) -> int:
            return 0

        def __getitem__(self, _index: int) -> PendingReviewComment:
            raise AssertionError("empty pending comments should not be scanned")

    unchanged, removed = remove_pending_comment(
        EmptyComments(),
        path="missing.py",
        line=2,
        side="RIGHT",
    )  # type: ignore[arg-type]

    assert unchanged == []
    assert removed is False


def test_delete_pending_comment_advances_version_when_removed() -> None:
    keep = PendingReviewComment(body="keep", path="a.py", line=1, side="RIGHT")
    drop = PendingReviewComment(body="drop", path="a.py", line=2, side="RIGHT")

    result = pending_review.delete_pending_comment(
        [keep, drop],
        path="a.py",
        line=2,
        side="RIGHT",
        current_version=4,
    )

    assert result.deleted is True
    assert result.comments == [keep]
    assert result.version == 5


def test_delete_pending_comment_preserves_version_when_missing() -> None:
    keep = PendingReviewComment(body="keep", path="a.py", line=1, side="RIGHT")

    result = pending_review.delete_pending_comment(
        [keep],
        path="a.py",
        line=2,
        side="RIGHT",
        current_version=4,
    )

    assert result.deleted is False
    assert result.comments == [keep]
    assert result.version == 4


def test_syncable_and_unsupported_comment_helpers_partition_drafts() -> None:
    diff_comment = PendingReviewComment(
        body="diff",
        path="a.py",
        line=1,
        side="RIGHT",
        is_diff_line=True,
    )
    unsupported = PendingReviewComment(
        body="full file",
        path="a.py",
        line=9,
        side="RIGHT",
        is_diff_line=False,
    )

    comments = [diff_comment, unsupported]

    assert syncable_comments(comments) == [diff_comment]
    assert first_unsupported_comment(comments) == unsupported


def test_save_pending_comment_normalizes_body_and_advances_version() -> None:
    existing = PendingReviewComment(
        body="later",
        path="b.py",
        line=9,
        side="RIGHT",
    )

    result = pending_review.save_pending_comment(
        [existing],
        body="  first  ",
        path="a.py",
        line=7,
        side="RIGHT",
        is_diff_line=True,
        current_version=4,
    )

    assert result.draft.body == "first"
    assert result.draft.is_diff_line is True
    assert result.comments == [result.draft, existing]
    assert result.version == 5


def test_save_pending_comment_rejects_empty_body() -> None:
    try:
        pending_review.save_pending_comment(
            [],
            body="   ",
            path="a.py",
            line=7,
            side="RIGHT",
            is_diff_line=True,
            current_version=4,
        )
    except ValueError as error:
        assert "empty" in str(error)
    else:
        raise AssertionError("empty pending comment should be rejected")


def test_pending_review_snapshot_captures_tuple_copy() -> None:
    comment = PendingReviewComment(
        body="draft",
        path="a.py",
        line=7,
        side="RIGHT",
    )
    comments = [comment]

    snapshot = pending_review.snapshot_pending_review(
        pending_review_id=91,
        pending_review_body="body",
        pending_review_comments=comments,
        version=4,
    )
    comments.clear()

    assert snapshot.pending_review_id == 91
    assert snapshot.pending_review_body == "body"
    assert snapshot.pending_review_comments == (comment,)
    assert snapshot.version == 4


def test_pending_review_snapshot_restore_returns_mutable_copy_and_next_version() -> (
    None
):
    comment = PendingReviewComment(
        body="draft",
        path="a.py",
        line=7,
        side="RIGHT",
    )
    snapshot = pending_review.snapshot_pending_review(
        pending_review_id=91,
        pending_review_body="body",
        pending_review_comments=[comment],
        version=4,
    )

    restored = pending_review.restore_pending_review_snapshot(
        snapshot,
        current_version=8,
    )
    restored.pending_review_comments.clear()

    assert restored.pending_review_id == 91
    assert restored.pending_review_body == "body"
    assert restored.version == 9
    assert snapshot.pending_review_comments == (comment,)


def test_pending_review_snapshot_restore_guard_requires_snapshot_and_matching_version() -> (
    None
):
    snapshot = pending_review.snapshot_pending_review(
        pending_review_id=91,
        pending_review_body="body",
        pending_review_comments=[],
        version=4,
    )

    assert pending_review.should_restore_pending_review_snapshot(
        snapshot,
        rollback_if_version=8,
        current_version=8,
    )
    assert not pending_review.should_restore_pending_review_snapshot(
        snapshot,
        rollback_if_version=8,
        current_version=9,
    )
    assert not pending_review.should_restore_pending_review_snapshot(
        None,
        rollback_if_version=8,
        current_version=8,
    )
    assert not pending_review.should_restore_pending_review_snapshot(
        snapshot,
        rollback_if_version=None,
        current_version=8,
    )


def test_pending_review_sync_result_uses_created_review_body_and_id() -> None:
    result = pending_review.project_pending_review_sync_result(
        PRReview(id=100, body="server body"),
        current_body="local body",
    )

    assert result.pending_review_id == 100
    assert result.pending_review_body == "server body"


def test_pending_review_sync_result_clears_state_without_review() -> None:
    result = pending_review.project_pending_review_sync_result(
        None,
        current_body="local body",
    )

    assert result.pending_review_id is None
    assert result.pending_review_body == ""


def test_pending_review_sync_result_preserves_local_body_when_review_body_empty() -> (
    None
):
    result = pending_review.project_pending_review_sync_result(
        PRReview(id=100, body=""),
        current_body="local body",
    )

    assert result.pending_review_id == 100
    assert result.pending_review_body == "local body"


def test_apply_pending_review_sync_result_advances_version() -> None:
    result = pending_review.project_pending_review_sync_result(
        PRReview(id=100, body="server body"),
        current_body="local body",
    )

    applied = pending_review.apply_pending_review_sync_result(
        result,
        current_version=4,
    )

    assert applied.pending_review_id == 100
    assert applied.pending_review_body == "server body"
    assert applied.version == 5


def test_clear_pending_review_resets_state_and_advances_version() -> None:
    result = pending_review.clear_pending_review(current_version=4)

    assert result.pending_review_id is None
    assert result.pending_review_body == ""
    assert result.pending_review_comments == []
    assert result.version == 5


def test_review_submission_plan_normalizes_direct_review_body_and_comments() -> None:
    draft = PendingReviewComment(
        body="inline",
        path="a.py",
        line=1,
        side="RIGHT",
    )

    plan = plan_review_submission(
        "COMMENT",
        "  summary  ",
        [draft],
        pending_review_id=None,
    )

    assert plan.body == "summary"
    assert plan.comments == [draft]
    assert plan.pending_review_id is None
    assert plan.uses_pending_review is False


def test_review_submission_plan_allows_empty_approve_body() -> None:
    plan = plan_review_submission(
        "APPROVE",
        "  ",
        [],
        pending_review_id=None,
    )

    assert plan.body is None


def test_review_submission_plan_requires_body_for_empty_comment_and_changes() -> None:
    for event in ["COMMENT", "REQUEST_CHANGES"]:
        try:
            plan_review_submission(
                event,  # type: ignore[arg-type]
                "  ",
                [],
                pending_review_id=None,
            )
        except ValueError as error:
            assert "empty" in str(error)
        else:
            raise AssertionError(f"{event} should require a body")


def test_review_submission_plan_uses_existing_pending_review_id() -> None:
    plan = plan_review_submission(
        "COMMENT",
        "summary",
        [],
        pending_review_id=91,
    )

    assert plan.pending_review_id == 91
    assert plan.uses_pending_review is True


def test_review_submission_plan_rejects_unsupported_draft_targets() -> None:
    unsupported = PendingReviewComment(
        body="full file",
        path="a.py",
        line=9,
        side="RIGHT",
        is_diff_line=False,
    )

    try:
        plan_review_submission(
            "COMMENT",
            "summary",
            [unsupported],
            pending_review_id=None,
        )
    except UnsupportedInlineCommentTarget as error:
        assert "a.py:9" in str(error)
    else:
        raise AssertionError("unsupported draft target should be rejected")


def test_review_submission_plan_stops_at_first_unsupported_draft() -> None:
    unsupported = PendingReviewComment(
        body="full file",
        path="a.py",
        line=9,
        side="RIGHT",
        is_diff_line=False,
    )

    class Drafts:
        def __iter__(self):
            yield unsupported
            raise AssertionError("unsupported draft should stop validation")

    with pytest.raises(UnsupportedInlineCommentTarget, match="a.py:9"):
        plan_review_submission(
            "COMMENT",
            "summary",
            Drafts(),  # type: ignore[arg-type]
            pending_review_id=None,
        )


def test_pending_review_sync_plan_deletes_existing_and_keeps_syncable_comments() -> (
    None
):
    diff_comment = PendingReviewComment(
        body="sync",
        path="a.py",
        line=1,
        side="RIGHT",
        is_diff_line=True,
    )
    unsupported = PendingReviewComment(
        body="local",
        path="a.py",
        line=2,
        side="RIGHT",
        is_diff_line=False,
    )

    plan = pending_review.plan_pending_review_sync(
        [diff_comment, unsupported],
        pending_review_id=91,
        pending_review_body="  pending body  ",
        head_sha="deadbeef",
    )

    assert plan.delete_review_id == 91
    assert plan.comments == [diff_comment]
    assert plan.body == "  pending body  "
    assert plan.commit_id == "deadbeef"
    assert plan.should_create is True


def test_pending_review_sync_plan_skips_create_without_syncable_comments() -> None:
    unsupported = PendingReviewComment(
        body="local",
        path="a.py",
        line=2,
        side="RIGHT",
        is_diff_line=False,
    )

    plan = pending_review.plan_pending_review_sync(
        [unsupported],
        pending_review_id=91,
        pending_review_body="",
        head_sha="",
    )

    assert plan.delete_review_id == 91
    assert plan.comments == []
    assert plan.body is None
    assert plan.commit_id is None
    assert plan.should_create is False


def test_select_pending_review_returns_latest_pending_review() -> None:
    first = PRReview(id=1, state=ReviewState.PENDING)
    approved = PRReview(id=2, state=ReviewState.APPROVED)
    latest = PRReview(id=3, state=ReviewState.PENDING)

    assert pending_review.select_pending_review([first, approved, latest]) == latest


def test_project_pending_review_resets_without_pending_review() -> None:
    projection = pending_review.project_pending_review(
        None,
        [PRComment(body="ignored", path="a.py", line=1, side="RIGHT")],
    )

    assert projection.review_id is None
    assert projection.body == ""
    assert projection.comments == []


def test_project_pending_review_filters_and_sorts_review_comments() -> None:
    review = PRReview(id=91, body="pending body")
    review_comments = [
        PRComment(body="new", path="b.py", line=2, side="RIGHT"),
        PRComment(body="missing path", line=1, side="RIGHT"),
        PRComment(body="missing line", path="c.py", side="RIGHT"),
        PRComment(body="unsupported side", path="d.py", line=3, side="BOTH"),
        PRComment(body="old", path="a.py", line=9, original_line=4, side="LEFT"),
    ]

    projection = pending_review.project_pending_review(review, review_comments)

    assert projection.review_id == 91
    assert projection.body == "pending body"
    assert projection.comments == [
        PendingReviewComment(body="old", path="a.py", line=4, side="LEFT"),
        PendingReviewComment(body="new", path="b.py", line=2, side="RIGHT"),
    ]


@pytest.mark.asyncio
async def test_load_pending_review_projection_fetches_comments_for_latest_pending() -> (
    None
):
    calls: list[tuple[int, int]] = []
    older = PRReview(id=90, state=ReviewState.PENDING, body="old")
    latest = PRReview(id=91, state=ReviewState.PENDING, body="latest")

    async def list_review_comments(
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        calls.append((pr_number, review_id))
        return [PRComment(body="note", path="a.py", line=7, side="RIGHT")]

    projection = await pending_review.load_pending_review_projection(
        [older, latest],
        pr_number=123,
        list_review_comments=list_review_comments,
    )

    assert calls == [(123, 91)]
    assert projection.review_id == 91
    assert projection.body == "latest"
    assert projection.comments == [
        PendingReviewComment(body="note", path="a.py", line=7, side="RIGHT")
    ]


@pytest.mark.asyncio
async def test_load_pending_review_projection_uses_empty_comments_when_fetch_fails() -> (
    None
):
    pending = PRReview(id=91, state=ReviewState.PENDING, body="pending")

    async def list_review_comments(
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        raise RuntimeError("boom")

    projection = await pending_review.load_pending_review_projection(
        [pending],
        pr_number=123,
        list_review_comments=list_review_comments,
    )

    assert projection.review_id == 91
    assert projection.body == "pending"
    assert projection.comments == []


@pytest.mark.asyncio
async def test_load_pending_review_projection_reraises_non_runtime_fetch_errors() -> (
    None
):
    pending = PRReview(id=91, state=ReviewState.PENDING, body="pending")

    async def list_review_comments(
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        raise ValueError("bad pending review comments adapter")

    with pytest.raises(ValueError, match="bad pending review comments adapter"):
        await pending_review.load_pending_review_projection(
            [pending],
            pr_number=123,
            list_review_comments=list_review_comments,
        )


@pytest.mark.asyncio
async def test_load_pending_review_projection_skips_fetch_without_pending_review() -> (
    None
):
    async def list_review_comments(
        pr_number: int,
        review_id: int,
    ) -> list[PRComment]:
        raise AssertionError("comments should not be fetched")

    projection = await pending_review.load_pending_review_projection(
        [PRReview(id=10, state=ReviewState.APPROVED, body="done")],
        pr_number=123,
        list_review_comments=list_review_comments,
    )

    assert projection.review_id is None
    assert projection.body == ""
    assert projection.comments == []


def test_apply_pending_review_projection_advances_version_and_copies_comments() -> (
    None
):
    draft = PendingReviewComment(
        body="draft",
        path="a.py",
        line=7,
        side="RIGHT",
    )
    projection = pending_review.PendingReviewProjection(
        review_id=91,
        body="pending body",
        comments=[draft],
    )

    applied = pending_review.apply_pending_review_projection(
        projection,
        current_version=4,
    )
    applied.comments.clear()

    assert applied.review_id == 91
    assert applied.body == "pending body"
    assert applied.version == 5
    assert projection.comments == [draft]


def test_inline_comment_diff_line_allows_unknown_diff() -> None:
    assert pending_review.is_inline_comment_diff_line(None, line=99, side="RIGHT")


def test_inline_comment_diff_line_matches_side_specific_line_numbers() -> None:
    diff = _diff()

    assert pending_review.is_inline_comment_diff_line(diff, line=7, side="RIGHT")
    assert pending_review.is_inline_comment_diff_line(diff, line=4, side="LEFT")
    assert not pending_review.is_inline_comment_diff_line(diff, line=7, side="LEFT")
    assert not pending_review.is_inline_comment_diff_line(diff, line=4, side="RIGHT")


def test_inline_comment_diff_line_skips_hunk_lines_outside_anchor_range() -> None:
    class UnreachableLines(list[DiffLine]):
        def __iter__(self):
            raise AssertionError("outside-range hunk lines should not be scanned")

    diff = FileDiff(
        filename="a.py",
        hunks=[
            DiffHunk(
                old_start=10,
                old_count=2,
                new_start=20,
                new_count=2,
                lines=UnreachableLines(
                    [
                        DiffLine(
                            old_line_no=10,
                            new_line_no=20,
                            old_content="same",
                            new_content="same",
                        )
                    ]
                ),
            )
        ],
    )

    assert not pending_review.is_inline_comment_diff_line(diff, line=99, side="RIGHT")


def test_require_inline_comment_diff_line_rejects_outside_diff_targets() -> None:
    try:
        pending_review.require_inline_comment_diff_line(
            _diff(),
            path="a.py",
            line=9,
            side="RIGHT",
        )
    except UnsupportedInlineCommentTarget as error:
        assert "a.py:9" in str(error)
    else:
        raise AssertionError("outside diff target should be rejected")


def test_inline_comment_submission_plan_normalizes_inputs() -> None:
    plan = pending_review.plan_inline_comment_submission(
        "  hello  ",
        head_sha="deadbeef",
        diff=_diff(),
        path="a.py",
        line=7,
        side="RIGHT",
    )

    assert plan.body == "hello"
    assert plan.commit_id == "deadbeef"
    assert plan.side == "RIGHT"


def test_inline_comment_submission_plan_rejects_empty_body() -> None:
    try:
        pending_review.plan_inline_comment_submission(
            "   ",
            head_sha="deadbeef",
            diff=None,
            path="a.py",
            line=7,
            side="RIGHT",
        )
    except ValueError as error:
        assert "empty" in str(error)
    else:
        raise AssertionError("empty inline comment should be rejected")


def test_inline_comment_submission_plan_requires_head_sha() -> None:
    try:
        pending_review.plan_inline_comment_submission(
            "hello",
            head_sha="",
            diff=None,
            path="a.py",
            line=7,
            side="RIGHT",
        )
    except ValueError as error:
        assert "head SHA" in str(error)
    else:
        raise AssertionError("inline comment should require a head SHA")


def test_inline_comment_submission_plan_rejects_unknown_side() -> None:
    try:
        pending_review.plan_inline_comment_submission(
            "hello",
            head_sha="deadbeef",
            diff=None,
            path="a.py",
            line=7,
            side="BOTH",
        )
    except ValueError as error:
        assert "side" in str(error)
    else:
        raise AssertionError("unknown inline comment side should be rejected")
