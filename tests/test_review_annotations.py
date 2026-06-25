from rit.state.models import PendingReviewComment, ReviewThread
from rit.state.review_annotations import ReviewAnnotationIndex


def test_review_annotation_index_counts_and_finds_pending_drafts() -> None:
    first = PendingReviewComment(body="first", path="a.py", line=7, side="RIGHT")
    second = PendingReviewComment(body="second", path="a.py", line=7, side="RIGHT")
    other = PendingReviewComment(body="other", path="b.py", line=1, side="LEFT")

    index = ReviewAnnotationIndex.from_parts(
        pending_comments=[first, second, other],
        review_threads=[ReviewThread(path="a.py")],
    )

    assert index.count_pending_for_file("a.py") == 2
    assert index.pending_for_file("a.py") == [first, second]
    assert index.pending_index(path="a.py", line=7, side="RIGHT") == 0
    assert index.index_for_comment(second) == 1
    assert (
        index.pending_for_sync(
            path="a.py",
            line=7,
            side="RIGHT",
            draft_index=1,
        )
        == second
    )
