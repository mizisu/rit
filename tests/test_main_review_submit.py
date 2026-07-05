from collections.abc import Callable
from typing import cast

from textual.worker import Worker

from rit.state.store import GitHubError
from rit.ui.screens.main import MainScreen


class _WorkerStub:
    def __init__(self, error: BaseException | None) -> None:
        self.error = error


def test_review_submit_worker_does_not_exit_on_error(
    monkeypatch,
) -> None:
    screen = MainScreen(owner="owner", repo="repo", pr_number=123)
    calls: list[dict[str, object]] = []

    def run_worker(work: object, **kwargs: object) -> object:
        calls.append(kwargs)
        close = getattr(work, "close", None)
        if isinstance(close, Callable):
            close()
        return object()

    monkeypatch.setattr(screen, "run_worker", run_worker)

    screen._handle_review_submit(("APPROVE", ""))

    assert calls == [
        {
            "exclusive": False,
            "name": "_submit_review",
            "exit_on_error": False,
        }
    ]


def test_review_submit_error_message_uses_github_error_detail() -> None:
    error = GitHubError(
        "gh: Could not approve for pull request review. "
        "Can not approve your own pull request"
    )

    assert (
        MainScreen._worker_error_message(
            cast(Worker[object], _WorkerStub(error)), "Failed to submit review"
        )
        == "gh: Could not approve for pull request review. "
        "Can not approve your own pull request"
    )
