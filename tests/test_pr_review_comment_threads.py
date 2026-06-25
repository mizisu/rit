import rit.services.pr_review_comment_threads as pr_review_comment_threads
from rit.services.pr_review_comment_threads import review_threads_from_rest_comments
from rit.state.models import PRComment, PRUser


def test_review_threads_from_rest_comments_groups_replies_and_normalizes_bot_login() -> (
    None
):
    root = PRComment(
        id=300,
        body="root",
        user=PRUser(login="coderabbitai[bot]"),
        path="app.py",
        line=12,
        side="RIGHT",
        pull_request_review_id=200,
    )
    reply = PRComment(
        id=301,
        body="reply",
        user=PRUser(login="alice"),
        path="app.py",
        line=12,
        side="RIGHT",
        in_reply_to_id=300,
        pull_request_review_id=200,
    )

    threads = review_threads_from_rest_comments([reply, root])

    assert len(threads) == 1
    thread = threads[0]
    assert thread.path == "app.py"
    assert thread.line == 12
    assert thread.diff_side == "RIGHT"
    assert [comment.id for comment in thread.comments] == [300, 301]
    assert thread.comments[0].user is not None
    assert thread.comments[0].user.login == "coderabbitai"


def test_review_threads_from_rest_comments_single_comment_skips_grouping(
    monkeypatch,
) -> None:
    def group_comments(_comments: object) -> object:
        raise AssertionError("single REST review comment should skip grouping")

    monkeypatch.setattr(
        pr_review_comment_threads,
        "group_comments_into_threads",
        group_comments,
    )
    comment = PRComment(
        id=300,
        body="root",
        user=PRUser(login="coderabbitai[bot]"),
        path="app.py",
        line=12,
        side="RIGHT",
        pull_request_review_id=200,
    )

    threads = review_threads_from_rest_comments([comment])

    assert len(threads) == 1
    thread = threads[0]
    assert thread.path == "app.py"
    assert thread.line == 12
    assert thread.diff_side == "RIGHT"
    assert thread.root_comment_id == 300
    assert thread.comments[0].user is not None
    assert thread.comments[0].user.login == "coderabbitai"
