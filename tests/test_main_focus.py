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


class FocusTarget:
    has_focus_within = False
    split = False
    active_pane = "new"

    def __init__(self) -> None:
        self.focused = False

    def focus(self) -> None:
        self.focused = True


class ToggleFileChanges:
    def __init__(self, tree: FocusTarget) -> None:
        self.file_tree = SimpleNamespace(
            display=False,
            has_focus_within=False,
            query_one=lambda *_args: tree,
        )
        self.toggled = False

    def toggle_file_tree(self) -> None:
        self.toggled = True
        self.file_tree.display = True


def test_current_files_focus_target_returns_none_when_file_tree_is_not_mounted() -> (
    None
):
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


def test_focus_files_diff_focuses_diff_when_no_files_widget_has_focus() -> None:
    diff_view = FocusTarget()

    class TestScreen(MainScreen):
        @property
        def file_changes(self):
            return SimpleNamespace(diff_view=diff_view, file_tree=MissingFileTree())

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.current_tab = 1

    screen._focus_files_diff(preserve_existing_focus=True)

    assert diff_view.focused is True


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


def test_toggle_file_tree_focuses_tree_when_opening() -> None:
    tree = FocusTarget()
    file_changes = ToggleFileChanges(tree)

    class TestScreen(MainScreen):
        @property
        def file_changes(self):
            return file_changes

    screen = TestScreen(owner="test", repo="repo", pr_number=123)
    screen.current_tab = 1

    screen.action_toggle_file_tree()

    assert file_changes.toggled is True
    assert tree.focused is True
