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

