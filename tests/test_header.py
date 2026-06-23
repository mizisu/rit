import pytest
from textual.css.query import NoMatches
from textual.widgets import Static

from rit.ui.widgets.header import Header


class BrokenStatus(Static):
    def update(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("status update failed")


def test_status_watcher_ignores_missing_widget_before_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    def missing_widget(*_args: object, **_kwargs: object) -> Static:
        raise NoMatches("missing")

    monkeypatch.setattr(header, "query_one", missing_widget)

    header.watch_pr_status("Merged")

    assert header._status_merged is True


def test_status_watcher_reraises_unexpected_widget_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    monkeypatch.setattr(header, "query_one", lambda *_args, **_kwargs: BrokenStatus())

    with pytest.raises(RuntimeError, match="status update failed"):
        header.watch_pr_status("Closed")


def test_title_watcher_ignores_missing_widget_before_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    def missing_widget(*_args: object, **_kwargs: object) -> Static:
        raise NoMatches("missing")

    monkeypatch.setattr(header, "query_one", missing_widget)

    header.watch_pr_title("Loaded")


def test_title_watcher_reraises_unexpected_display_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = Header()

    def fail_display(_title: str) -> None:
        raise RuntimeError("title update failed")

    monkeypatch.setattr(header, "_update_title_display", fail_display)

    with pytest.raises(RuntimeError, match="title update failed"):
        header.watch_pr_title("Loaded")
