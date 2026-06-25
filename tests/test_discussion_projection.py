import rit.state.discussion_projection as discussion_projection
from rit.state.discussion_projection import (
    RecentDiscussion,
    merge_recent_submitted_discussion,
    project_discussion_state,
    thread_from_submitted_comment,
    update_thread_resolution,
)
from rit.state.models import (
    NodeList,
    PR,
    PRComment,
    PRReview,
    ReviewThread,
    ReviewState,
)


def test_project_discussion_state_flattens_comments_and_builds_file_thread_indexes() -> (
    None
):
    first = PRComment(id=101, body="one", path="src/app.py", line=7, side="RIGHT")
    second = PRComment(id=102, body="two", path="src/lib.py", line=3, side="RIGHT")
    pr = PR(
        number=123,
        review_threads_connection=NodeList(
            nodes=[
                ReviewThread.model_validate(
                    {
                        "id": "thread-101",
                        "isResolved": True,
                        "path": "src/app.py",
                        "line": 7,
                        "comments": {"nodes": [first]},
                    }
                ),
                ReviewThread.model_validate(
                    {
                        "id": "thread-102",
                        "isResolved": False,
                        "path": "src/lib.py",
                        "line": 3,
                        "comments": {"nodes": [second]},
                    }
                ),
            ]
        ),
    )

    projection = project_discussion_state(pr)

    assert projection.comments == [first, second]
    assert projection.comments_by_file == {
        "src/app.py": [first],
        "src/lib.py": [second],
    }
    assert projection.thread_info_cache[101].thread_id == "thread-101"
    assert projection.thread_info_cache[101].is_resolved is True
    assert projection.thread_info_cache[101].line == 7
    assert projection.thread_cache[102].root_comment == second


def test_project_discussion_state_single_comment_skips_comments_by_file_loop(
    monkeypatch,
) -> None:
    comment = PRComment(id=101, body="one", path="src/app.py", line=7, side="RIGHT")
    pr = PR(
        number=123,
        review_threads_connection=NodeList(
            nodes=[
                ReviewThread.model_validate(
                    {
                        "id": "thread-101",
                        "isResolved": True,
                        "path": "src/app.py",
                        "line": 7,
                        "comments": {"nodes": [comment]},
                    }
                )
            ]
        ),
    )
    monkeypatch.setattr(
        discussion_projection,
        "_comments_by_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single projected comment should not group via loop")
        ),
    )

    projection = project_discussion_state(pr)

    assert projection.comments == [comment]
    assert projection.comments_by_file == {"src/app.py": [comment]}


def test_project_discussion_state_keeps_recent_submitted_discussion_visible() -> None:
    review = PRReview(id=91, state=ReviewState.COMMENTED)
    comment = PRComment(
        id=501,
        body="submitted",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )

    projection = project_discussion_state(
        PR(number=123),
        recent=RecentDiscussion(
            reviews={91: review},
            review_comments={91: [comment]},
        ),
    )

    assert projection.reviews == [review]
    assert projection.review_threads[0].root_comment == comment
    assert projection.review_threads[0].line == 7
    assert projection.comments_by_file["src/app.py"] == [comment]


def test_project_discussion_state_deduplicates_recent_submitted_discussion() -> None:
    review = PRReview(id=91, state=ReviewState.COMMENTED)
    comment = PRComment(
        id=501,
        body="submitted",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    pr = PR(
        number=123,
        reviews_connection=NodeList(nodes=[review]),
        review_threads_connection=NodeList(
            nodes=[
                ReviewThread.model_validate(
                    {
                        "id": "thread-501",
                        "isResolved": False,
                        "path": "src/app.py",
                        "line": 7,
                        "comments": {"nodes": [comment]},
                    }
                )
            ]
        ),
    )

    projection = project_discussion_state(
        pr,
        recent=RecentDiscussion(
            reviews={91: review},
            review_comments={91: [comment]},
        ),
    )

    assert projection.reviews == [review]
    assert projection.review_threads == pr.review_threads
    assert projection.comments == [comment]


def test_project_discussion_state_reuses_discussion_lists_without_recent_discussion(
    monkeypatch,
) -> None:
    review = PRReview(id=91, state=ReviewState.COMMENTED)
    comment = PRComment(id=501, body="existing", path="src/app.py", line=7)
    thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "isResolved": False,
            "path": "src/app.py",
            "line": 7,
            "comments": {"nodes": [comment]},
        }
    )
    pr = PR(
        number=123,
        reviews_connection=NodeList(nodes=[review]),
        review_threads_connection=NodeList(nodes=[thread]),
    )

    monkeypatch.setattr(
        discussion_projection,
        "list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("projection should reuse PR discussion lists")
        ),
        raising=False,
    )

    projection = project_discussion_state(pr)

    assert projection.pr is pr
    assert projection.reviews is pr.reviews
    assert projection.review_threads is pr.review_threads


def test_project_discussion_state_empty_threads_skip_thread_scans() -> None:
    class EmptyThreads(list[ReviewThread]):
        def __iter__(self):
            raise AssertionError("empty discussion threads should not be scanned")

    pr = PR(number=123)
    pr.review_threads_connection.nodes = EmptyThreads()

    projection = project_discussion_state(pr)

    assert projection.review_threads is pr.review_threads
    assert projection.comments == []
    assert projection.comments_by_file == {}
    assert projection.thread_info_cache == {}
    assert projection.thread_cache == {}


def test_merge_recent_submitted_discussion_skips_thread_comments_without_recent_comments() -> (
    None
):
    class UnreachableComments(list[PRComment]):
        def __iter__(self):
            raise AssertionError("review comments are not needed for review-only merge")

    review = PRReview(id=91, state=ReviewState.COMMENTED)
    existing_thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "isResolved": False,
            "path": "src/app.py",
            "line": 7,
            "comments": {
                "nodes": [
                    PRComment(
                        id=501,
                        body="existing",
                        path="src/app.py",
                        line=7,
                        side="RIGHT",
                    )
                ]
            },
        }
    )
    existing_thread.comments_connection.nodes = UnreachableComments(
        existing_thread.comments
    )
    reviews: list[PRReview] = []
    threads = [existing_thread]

    merged_reviews, merged_threads = merge_recent_submitted_discussion(
        reviews,
        threads,
        recent=RecentDiscussion(reviews={91: review}),
    )

    assert merged_reviews == [review]
    assert merged_threads is threads


def test_merge_recent_submitted_discussion_empty_reviews_skip_existing_review_scan() -> (
    None
):
    class EmptyReviews(list[PRReview]):
        def __iter__(self):
            raise AssertionError("empty existing reviews should not be scanned")

    review = PRReview(id=91, state=ReviewState.COMMENTED)
    reviews = EmptyReviews()
    threads: list[ReviewThread] = []

    merged_reviews, merged_threads = merge_recent_submitted_discussion(
        reviews,
        threads,
        recent=RecentDiscussion(reviews={91: review}),
    )

    assert merged_reviews is reviews
    assert merged_reviews == [review]
    assert merged_threads is threads


def test_merge_recent_submitted_discussion_skips_reviews_without_recent_reviews() -> (
    None
):
    class UnreachableReviews(list[PRReview]):
        def __iter__(self):
            raise AssertionError("reviews are not needed for comment-only merge")

    comment = PRComment(
        id=501,
        body="submitted",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    reviews = UnreachableReviews([PRReview(id=1, state=ReviewState.COMMENTED)])
    threads: list[ReviewThread] = []

    merged_reviews, merged_threads = merge_recent_submitted_discussion(
        reviews,
        threads,
        recent=RecentDiscussion(review_comments={91: [comment]}),
    )

    assert merged_reviews is reviews
    assert merged_threads[0].root_comment == comment


def test_merge_recent_submitted_discussion_empty_threads_skip_existing_comment_scan() -> (
    None
):
    class EmptyThreads(list[ReviewThread]):
        def __iter__(self):
            raise AssertionError("empty existing review threads should not be scanned")

    comment = PRComment(
        id=501,
        body="submitted",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    reviews: list[PRReview] = []
    threads = EmptyThreads()

    merged_reviews, merged_threads = merge_recent_submitted_discussion(
        reviews,
        threads,
        recent=RecentDiscussion(review_comments={91: [comment]}),
    )

    assert merged_reviews is reviews
    assert merged_threads is threads
    assert merged_threads[0].root_comment == comment


def test_remember_submitted_review_records_review_and_normalizes_comments() -> None:
    review = PRReview(id=91, state=ReviewState.COMMENTED)
    comment = PRComment(
        id=501,
        body="submitted",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )

    recent = discussion_projection.remember_submitted_review(
        RecentDiscussion(),
        review,
        [comment],
    )

    assert recent.reviews == {91: review}
    assert len(recent.review_comments[91]) == 1
    assert recent.review_comments[91][0].pull_request_review_id == 91


def test_remember_submitted_review_ignores_missing_review_ids() -> None:
    recent = discussion_projection.remember_submitted_review(
        RecentDiscussion(),
        PRReview(id=0, state=ReviewState.COMMENTED),
        [
            PRComment(
                id=501,
                body="submitted",
                path="src/app.py",
                line=7,
                side="RIGHT",
            )
        ],
    )

    assert recent == RecentDiscussion()


def test_remember_submitted_review_only_replaces_target_review_comments() -> None:
    class UnreachableComments(list[PRComment]):
        def __iter__(self):
            raise AssertionError("unrelated review comments should not be copied")

    unrelated = PRComment(
        id=401,
        body="unrelated",
        path="src/app.py",
        line=6,
        side="RIGHT",
        pull_request_review_id=90,
    )
    submitted = PRComment(
        id=501,
        body="submitted",
        path="src/app.py",
        line=7,
        side="RIGHT",
    )
    review = PRReview(id=91, state=ReviewState.COMMENTED)
    recent = RecentDiscussion(
        review_comments={90: UnreachableComments([unrelated])}
    )

    updated = discussion_projection.remember_submitted_review(
        recent,
        review,
        [submitted],
    )

    assert updated.review_comments[90] is recent.review_comments[90]
    assert updated.review_comments[91][0].pull_request_review_id == 91


def test_remember_submitted_comment_requires_review_id_and_deduplicates() -> None:
    first = PRComment(
        id=501,
        body="first",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    duplicate = first.model_copy(update={"body": "duplicate"})
    second = PRComment(
        id=502,
        body="second",
        path="src/app.py",
        line=8,
        side="RIGHT",
        pull_request_review_id=91,
    )

    recent = discussion_projection.remember_submitted_comment(
        RecentDiscussion(),
        PRComment(id=500, body="ignored", path="src/app.py", line=6, side="RIGHT"),
    )
    recent = discussion_projection.remember_submitted_comment(recent, first)
    recent = discussion_projection.remember_submitted_comment(recent, duplicate)
    recent = discussion_projection.remember_submitted_comment(recent, second)

    assert recent.review_comments[91] == [first, second]


def test_remember_submitted_comment_reuses_recent_for_duplicate_comment() -> None:
    comment = PRComment(
        id=501,
        body="first",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    recent = RecentDiscussion(review_comments={91: [comment]})

    duplicate = discussion_projection.remember_submitted_comment(recent, comment)

    assert duplicate is recent


def test_remember_submitted_comment_single_duplicate_skips_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comment = PRComment(
        id=501,
        body="first",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    recent = RecentDiscussion(review_comments={91: [comment]})
    monkeypatch.setattr(
        discussion_projection,
        "any",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("single duplicate submitted comment should not call any")
        ),
        raising=False,
    )

    duplicate = discussion_projection.remember_submitted_comment(recent, comment)

    assert duplicate is recent


def test_remember_submitted_comment_only_copies_target_review_comments() -> None:
    class UnreachableComments(list[PRComment]):
        def __iter__(self):
            raise AssertionError("unrelated review comments should not be copied")

    unrelated = PRComment(
        id=401,
        body="unrelated",
        path="src/app.py",
        line=6,
        side="RIGHT",
        pull_request_review_id=90,
    )
    existing = PRComment(
        id=501,
        body="existing",
        path="src/app.py",
        line=7,
        side="RIGHT",
        pull_request_review_id=91,
    )
    added = PRComment(
        id=502,
        body="added",
        path="src/app.py",
        line=8,
        side="RIGHT",
        pull_request_review_id=91,
    )
    recent = RecentDiscussion(
        review_comments={
            90: UnreachableComments([unrelated]),
            91: [existing],
        }
    )

    updated = discussion_projection.remember_submitted_comment(recent, added)

    assert updated.review_comments[90] is recent.review_comments[90]
    assert updated.review_comments[91] == [existing, added]


def test_thread_from_submitted_comment_preserves_old_side_anchor() -> None:
    comment = PRComment(
        id=501,
        body="old line",
        path="src/app.py",
        original_line=4,
        side="LEFT",
    )

    thread = thread_from_submitted_comment(comment)

    assert thread.line is None
    assert thread.original_line == 4
    assert thread.root_comment == comment


def test_update_thread_resolution_preserves_existing_thread_metadata() -> None:
    comment = PRComment(
        id=501,
        body="old line",
        path="src/app.py",
        original_line=4,
        side="LEFT",
    )
    thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "isResolved": False,
            "path": "src/app.py",
            "originalLine": 4,
            "diffSide": "LEFT",
            "comments": {"nodes": [comment]},
        }
    )
    projection = project_discussion_state(
        PR(
            number=123,
            review_threads_connection=NodeList(nodes=[thread]),
        )
    )

    updated = update_thread_resolution(
        review_threads=projection.review_threads,
        thread_info_cache=projection.thread_info_cache,
        thread_cache=projection.thread_cache,
        root_comment_id=501,
        is_resolved=True,
    )

    updated_thread = updated.review_threads[0]
    assert updated_thread.is_resolved is True
    assert updated_thread.line is None
    assert updated_thread.original_line == 4
    assert updated_thread.diff_side == "LEFT"
    assert updated.thread_info_cache[501].is_resolved is True
    assert updated.thread_cache[501] == updated_thread


def test_update_thread_resolution_reuses_state_when_thread_is_missing() -> None:
    comment = PRComment(
        id=501,
        body="old line",
        path="src/app.py",
        original_line=4,
        side="LEFT",
    )
    thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "isResolved": False,
            "path": "src/app.py",
            "originalLine": 4,
            "diffSide": "LEFT",
            "comments": {"nodes": [comment]},
        }
    )
    projection = project_discussion_state(
        PR(
            number=123,
            review_threads_connection=NodeList(nodes=[thread]),
        )
    )

    updated = update_thread_resolution(
        review_threads=projection.review_threads,
        thread_info_cache=projection.thread_info_cache,
        thread_cache=projection.thread_cache,
        root_comment_id=999,
        is_resolved=True,
    )

    assert updated.review_threads is projection.review_threads
    assert updated.thread_info_cache is projection.thread_info_cache
    assert updated.thread_cache is projection.thread_cache


def test_update_thread_resolution_reuses_state_when_resolution_is_unchanged() -> None:
    comment = PRComment(
        id=501,
        body="old line",
        path="src/app.py",
        original_line=4,
        side="LEFT",
    )
    thread = ReviewThread.model_validate(
        {
            "id": "thread-501",
            "isResolved": True,
            "path": "src/app.py",
            "originalLine": 4,
            "diffSide": "LEFT",
            "comments": {"nodes": [comment]},
        }
    )
    projection = project_discussion_state(
        PR(
            number=123,
            review_threads_connection=NodeList(nodes=[thread]),
        )
    )

    updated = update_thread_resolution(
        review_threads=projection.review_threads,
        thread_info_cache=projection.thread_info_cache,
        thread_cache=projection.thread_cache,
        root_comment_id=501,
        is_resolved=True,
    )

    assert updated.review_threads is projection.review_threads
    assert updated.thread_info_cache is projection.thread_info_cache
    assert updated.thread_cache is projection.thread_cache
