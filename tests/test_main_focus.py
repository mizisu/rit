from types import SimpleNamespace

import pytest
from textual.css.query import NoMatches

from rit.ui.screens.main import MainScreen


class MissingFileTree:
    has_focus_within = False

    def query_one(self, *_args) -> None:
        raise NoMatches("missing")


class ExplodingFileTree:
    has_focus_within = False

    def query_one(self, *_args) -> None:
        raise RuntimeError("query failed")


class ExplodingFocusTree:
    def focus(self) -> None:
        raise RuntimeError("focus failed")


class QueryingFileTree:
    has_focus_within = False

    def query_one(self, *_args) -> ExplodingFocusTree:
        return ExplodingFocusTree()


def test_current_files_focus_target_returns_none_when_file_tree_is_not_mounted() -> None:
    class TestScreen(MainScreen):
        @property
        def file_changes(self):
            return SimpleNamespace(
                diff_view=SimpleNamespace(has_focus_within=False),
                file_tree=MissingFileTree(),
            )

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.current_tab = 1

    assert screen._current_files_focus_target() is None


def test_current_files_focus_target_reraises_unexpected_query_errors() -> None:
    class TestScreen(MainScreen):
        @property
        def file_changes(self):
            return SimpleNamespace(
                diff_view=SimpleNamespace(has_focus_within=False),
                file_tree=ExplodingFileTree(),
            )

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.current_tab = 1

    with pytest.raises(RuntimeError, match="query failed"):
        screen._current_files_focus_target()


def test_focus_files_tree_ignores_missing_file_tree() -> None:
    class TestScreen(MainScreen):
        @property
        def file_changes(self):
            return SimpleNamespace(file_tree=MissingFileTree())

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.current_tab = 1

    screen._focus_files_tree()


def test_focus_files_tree_reraises_unexpected_focus_errors() -> None:
    class TestScreen(MainScreen):
        @property
        def file_changes(self):
            return SimpleNamespace(file_tree=QueryingFileTree())

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.current_tab = 1

    with pytest.raises(RuntimeError, match="focus failed"):
        screen._focus_files_tree()
