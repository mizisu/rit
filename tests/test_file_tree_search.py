"""Tests for FileTree slash-search behavior."""

from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Input, Static, Tree

from rit.state.models import PRFile
from rit.ui.widgets import file_tree as file_tree_module
from rit.ui.widgets.file_tree import FileTree


@pytest.fixture
def sample_files() -> list[PRFile]:
    """Provide a small file list for tree search tests."""
    return [
        PRFile(filename="src/app.py", additions=10, deletions=2),
        PRFile(filename="src/utils/file_tree.py", additions=5, deletions=1),
        PRFile(filename="tests/test_app.py", additions=3, deletions=0),
        PRFile(filename="docs/usage.md", additions=7, deletions=4),
    ]


def test_focused_cursor_state_ignores_missing_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_tree = FileTree()

    def missing_tree(*_args: object, **_kwargs: object) -> object:
        raise NoMatches("missing")

    monkeypatch.setattr(file_tree, "query_one", missing_tree)

    assert file_tree._focused_cursor_state() == (False, None, None)


def test_focused_cursor_state_reraises_unexpected_tree_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_tree = FileTree()

    def fail_tree(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("tree lookup failed")

    monkeypatch.setattr(file_tree, "query_one", fail_tree)

    with pytest.raises(RuntimeError, match="tree lookup failed"):
        file_tree._focused_cursor_state()


def test_update_file_count_display_ignores_missing_count_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_tree = FileTree()

    def missing_count(*_args: object, **_kwargs: object) -> object:
        raise NoMatches("missing")

    monkeypatch.setattr(file_tree, "query_one", missing_count)

    file_tree._update_file_count_display()


def test_update_file_count_display_reraises_unexpected_update_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_tree = FileTree()

    class BrokenCount(Static):
        def update(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("count update failed")

    monkeypatch.setattr(file_tree, "query_one", lambda *_args, **_kwargs: BrokenCount())

    with pytest.raises(RuntimeError, match="count update failed"):
        file_tree._update_file_count_display()


def test_review_tree_binding_cleanup_removes_space_and_enter_keys() -> None:
    class Bindings:
        keys = {"space": object(), "enter": object(), "j": object()}

    bindings = Bindings()

    file_tree_module._remove_review_tree_default_bindings(bindings)

    assert sorted(bindings.keys) == ["j"]


def test_review_tree_binding_cleanup_ignores_unknown_private_shape() -> None:
    file_tree_module._remove_review_tree_default_bindings(object())
    file_tree_module._remove_review_tree_default_bindings(type("Bindings", (), {"keys": []})())


def test_tree_actions_reuse_cached_tree_without_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTree:
        def __init__(self) -> None:
            self.down_calls = 0

        def action_cursor_down(self) -> None:
            self.down_calls += 1

    file_tree = FileTree()
    tree = FakeTree()
    file_tree._tree_widget = tree  # type: ignore[assignment]

    def fail_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("tree action should reuse cached tree widget")

    monkeypatch.setattr(file_tree, "query_one", fail_query)

    file_tree.action_cursor_down()

    assert tree.down_calls == 1


def test_empty_file_search_reuses_all_files_without_copy(
    sample_files: list[PRFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_tree = FileTree()
    file_tree._all_files = sample_files
    file_tree._search_query = ""
    rendered: list[list[PRFile]] = []

    monkeypatch.setattr(
        file_tree,
        "_focused_cursor_state",
        lambda: (False, None, None),
    )
    monkeypatch.setattr(file_tree, "_render_files", rendered.append)
    monkeypatch.setattr(
        file_tree_module,
        "list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty file search should reuse the full file list")
        ),
        raising=False,
    )

    file_tree._apply_search_filter()

    assert file_tree._filtered_files is sample_files
    assert rendered == [sample_files]
    assert file_tree.file_count == len(sample_files)


def test_file_search_reuses_cached_lowercase_filenames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Filename(str):
        fail_lower = False

        def lower(self) -> str:
            if self.fail_lower:
                raise AssertionError("file search should reuse cached lowercase names")
            return super().lower()

    class FakeFile:
        def __init__(self, filename: str) -> None:
            self.filename = Filename(filename)

    files = [FakeFile("SRC/App.py"), FakeFile("docs/Usage.md")]
    file_tree = FileTree()
    rendered: list[list[object]] = []

    monkeypatch.setattr(
        file_tree,
        "_focused_cursor_state",
        lambda: (False, None, None),
    )
    monkeypatch.setattr(file_tree, "_render_files", rendered.append)

    file_tree.refresh_files(files)  # type: ignore[arg-type]
    Filename.fail_lower = True
    file_tree._set_search_query("app")

    file_tree._apply_search_filter()

    assert file_tree._filtered_files == [files[0]]
    assert rendered[-1] == [files[0]]


def test_file_search_reuses_cached_lowercase_query(
    sample_files: list[PRFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Query(str):
        fail_lower = False

        def lower(self) -> str:
            if self.fail_lower:
                raise AssertionError("file search should reuse cached lowercase query")
            return super().lower()

    file_tree = FileTree()
    file_tree._all_files = sample_files
    file_tree._file_search_names = [file.filename.lower() for file in sample_files]
    file_tree._search_query = Query("APP")
    file_tree._search_query_lower = "app"
    rendered: list[list[PRFile]] = []

    monkeypatch.setattr(
        file_tree,
        "_focused_cursor_state",
        lambda: (False, None, None),
    )
    monkeypatch.setattr(file_tree, "_render_files", rendered.append)
    Query.fail_lower = True

    file_tree._apply_search_filter()

    assert file_tree._filtered_files == [sample_files[0], sample_files[2]]


def test_search_input_change_skips_filter_when_normalized_query_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_tree = FileTree()
    file_tree._search_open = True
    file_tree._set_search_query("app")
    monkeypatch.setattr(
        file_tree,
        "_apply_search_filter",
        lambda: (_ for _ in ()).throw(
            AssertionError("unchanged normalized query should not re-filter")
        ),
    )

    file_tree.on_search_input_changed(SimpleNamespace(value=" app "))

    assert file_tree._search_query == "app"


def test_directory_contents_reuses_cached_path_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Filename(str):
        fail_split = False

        def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
            if self.fail_split:
                raise AssertionError("file tree rendering should reuse cached path parts")
            return super().split(sep, maxsplit)

    class FakeFile:
        def __init__(self, filename: str) -> None:
            self.filename = Filename(filename)

    files = [FakeFile("src/app.py"), FakeFile("src/utils/helpers.py")]
    file_tree = FileTree()
    monkeypatch.setattr(
        file_tree,
        "_focused_cursor_state",
        lambda: (False, None, None),
    )
    monkeypatch.setattr(file_tree, "_render_files", lambda _files: None)

    file_tree.refresh_files(files)  # type: ignore[arg-type]
    Filename.fail_split = True

    contents_by_path, files_by_path = file_tree._build_directory_contents(
        files  # type: ignore[arg-type]
    )

    assert contents_by_path[""].child_dirs == {"src": "src"}
    assert contents_by_path["src"].child_dirs == {"utils": "src/utils"}
    assert files_by_path["src/app.py"] is files[0]


def test_directory_contents_reads_each_filename_once() -> None:
    class FakeFile:
        def __init__(self, filename: str) -> None:
            self._filename = filename
            self.filename_reads = 0

        @property
        def filename(self) -> str:
            self.filename_reads += 1
            if self.filename_reads > 1:
                raise AssertionError(
                    "directory contents should read each filename once"
                )
            return self._filename

    file = FakeFile("src/ui/file_tree.py")
    file_tree = FileTree()
    file_tree._file_path_parts_by_filename = {
        "src/ui/file_tree.py": ("src", "ui", "file_tree.py")
    }

    contents_by_path, files_by_path = file_tree._build_directory_contents(
        [file]  # type: ignore[list-item]
    )

    assert file.filename_reads == 1
    assert contents_by_path["src"].child_dirs == {"ui": "src/ui"}
    assert files_by_path["src/ui/file_tree.py"] is file


def test_directory_contents_builds_directory_paths_without_prefix_slices() -> None:
    class PathParts(tuple[str, ...]):
        def __getitem__(self, index: object) -> str | tuple[str, ...]:
            if isinstance(index, slice):
                raise AssertionError(
                    "directory path building should avoid prefix slices"
                )
            return super().__getitem__(index)

    class FakeFile:
        filename = "src/rit/ui/widgets/file_tree.py"

    file_tree = FileTree()
    file_tree._file_path_parts_by_filename = {
        FakeFile.filename: PathParts(("src", "rit", "ui", "widgets", "file_tree.py"))
    }

    contents_by_path, files_by_path = file_tree._build_directory_contents(
        [FakeFile()]  # type: ignore[list-item]
    )

    assert contents_by_path["src/rit/ui"].child_dirs == {
        "widgets": "src/rit/ui/widgets"
    }
    assert files_by_path[FakeFile.filename].filename == FakeFile.filename


def test_compact_directory_path_reuses_cached_directory_names() -> None:
    class Path(str):
        fail_rsplit = False

        def rsplit(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
            if self.fail_rsplit:
                raise AssertionError("directory labels should reuse cached names")
            return super().rsplit(sep, maxsplit)

    root = Path("src")
    child = Path("src/widgets")
    file_tree = FileTree()
    file_tree._directory_name_by_path = {root: "src", child: "widgets"}
    contents_by_path = {
        root: file_tree_module._DirectoryContents(
            child_dirs={"widgets": child},
        ),
        child: file_tree_module._DirectoryContents(direct_file_count=1),
    }
    Path.fail_rsplit = True

    label, compacted_path = file_tree._compact_directory_path(
        root,
        contents_by_path,
    )

    assert label == "src/widgets"
    assert compacted_path == child


def test_file_label_reuses_cached_path_parts_for_basename() -> None:
    class Filename(str):
        fail_split = False

        def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
            if self.fail_split:
                raise AssertionError("file labels should reuse cached path parts")
            return super().split(sep, maxsplit)

    class FakeFile:
        filename = Filename("src/app.py")
        status = "modified"
        status_icon = "M"
        additions = 1
        deletions = 0
        comments: list[object] = []
        viewer_viewed_state = "UNVIEWED"

    file_tree = FileTree()
    file_tree._file_path_parts_by_filename = {FakeFile.filename: ("src", "app.py")}
    Filename.fail_split = True

    label = file_tree._file_label(FakeFile(), show_path=False)  # type: ignore[arg-type]

    assert "app.py" in label.plain


def test_file_label_uses_pending_comment_count_without_collecting_comments() -> None:
    class FakeStore:
        def count_pending_file_comments(self, filename: str) -> int:
            assert filename == "src/app.py"
            return 2

        def get_pending_file_comments(self, _filename: str) -> list[object]:
            raise AssertionError("file labels should use the count-only draft path")

    class FakeFile:
        filename = "src/app.py"
        status = "modified"
        status_icon = "M"
        additions = 1
        deletions = 0
        comments: list[object] = []
        viewer_viewed_state = "UNVIEWED"

    file_tree = FileTree()
    file_tree.store = FakeStore()  # type: ignore[assignment]

    label = file_tree._file_label(FakeFile(), show_path=True)  # type: ignore[arg-type]

    assert "[draft 2]" in label.plain


def test_copy_current_file_reuses_cached_path_parts_for_basename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Filename(str):
        fail_split = False

        def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]:
            if self.fail_split:
                raise AssertionError("basename copy should reuse cached path parts")
            return super().split(sep, maxsplit)

    class FakeNode:
        data = Filename("src/app.py")

    class FakeTree:
        cursor_node = FakeNode()

    class FakeApp:
        copied = ""

        def copy_to_clipboard(self, value: str) -> None:
            self.copied = value

    file_tree = FileTree()
    app = FakeApp()
    messages: list[object] = []
    file_tree._file_path_parts_by_filename = {FakeNode.data: ("src", "app.py")}
    monkeypatch.setattr(file_tree, "_tree", lambda: FakeTree())
    monkeypatch.setattr(type(file_tree), "app", property(lambda _self: app))
    monkeypatch.setattr(file_tree, "post_message", messages.append)
    Filename.fail_split = True

    file_tree._copy_current_file(basename_only=True)

    assert app.copied == "app.py"


def test_refresh_files_builds_filename_caches_in_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFile:
        def __init__(self, filename: str) -> None:
            self._filename = filename
            self.filename_reads = 0

        @property
        def filename(self) -> str:
            self.filename_reads += 1
            if self.filename_reads > 1:
                raise AssertionError(
                    "file refresh should read each filename once while caching"
                )
            return self._filename

    files = [FakeFile("src/app.py"), FakeFile("tests/test_app.py")]
    file_tree = FileTree()
    monkeypatch.setattr(file_tree, "_apply_search_filter", lambda: None)

    file_tree.refresh_files(files)  # type: ignore[arg-type]

    assert file_tree._file_search_names == ["src/app.py", "tests/test_app.py"]
    assert file_tree._file_path_parts_by_filename == {
        "src/app.py": ("src", "app.py"),
        "tests/test_app.py": ("tests", "test_app.py"),
    }
    assert file_tree._files_by_filename == {
        "src/app.py": files[0],
        "tests/test_app.py": files[1],
    }
    assert file_tree._file_index_by_filename == {
        "src/app.py": 0,
        "tests/test_app.py": 1,
    }


def test_refresh_files_reuses_list_input_without_copying(
    sample_files: list[PRFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_tree = FileTree()
    monkeypatch.setattr(file_tree, "_apply_search_filter", lambda: None)
    monkeypatch.setattr(
        file_tree_module,
        "list",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("file refresh should reuse list inputs")
        ),
        raising=False,
    )

    file_tree.refresh_files(sample_files)

    assert file_tree._all_files is sample_files


@pytest.mark.asyncio
async def test_slash_opens_file_search_input(sample_files: list[PRFile]) -> None:
    """Pressing `/` on the file tree should open and focus search input."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is True
        assert search.has_focus


@pytest.mark.asyncio
async def test_search_enter_selects_highlighted_result(
    sample_files: list[PRFile],
) -> None:
    """Enter in search mode should directly select the highlighted result."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        await pilot.press("u", "t", "i", "l", "s")
        await pilot.pause()

        assert file_tree.file_count == 1
        assert set(file_tree._file_nodes.keys()) == {"src/utils/file_tree.py"}

        await pilot.press("enter")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is False
        assert tree.has_focus
        assert file_tree.selected_file == "src/utils/file_tree.py"

        # Query clears after selection, so full list is shown again.
        assert file_tree.file_count == len(sample_files)


@pytest.mark.asyncio
async def test_escape_cancels_search_and_restores_list(
    sample_files: list[PRFile],
) -> None:
    """Escape should close search mode and restore the full file list."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        await pilot.press("d", "o", "c", "s")
        await pilot.pause()

        assert file_tree.file_count == 1

        await pilot.press("escape")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.display is False
        assert tree.has_focus
        assert file_tree.file_count == len(sample_files)


@pytest.mark.asyncio
async def test_j_and_k_type_in_search_instead_of_moving_tree(
    sample_files: list[PRFile],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """j/k should belong to the search input while file filtering is active."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()

        calls: list[str] = []
        monkeypatch.setattr(
            file_tree,
            "action_cursor_down",
            lambda: calls.append("down"),
        )
        monkeypatch.setattr(
            file_tree,
            "action_cursor_up",
            lambda: calls.append("up"),
        )

        await pilot.press("j", "k")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.value == "jk"
        assert calls == []


@pytest.mark.asyncio
async def test_y_copies_basename_of_current_file(sample_files: list[PRFile]) -> None:
    """Pressing `y` should copy the basename of the current file."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        tree.select_node(file_tree._file_nodes["src/utils/file_tree.py"])
        await pilot.pause()

        await pilot.press("y")
        await pilot.pause()

        assert app.clipboard == "file_tree.py"


@pytest.mark.asyncio
async def test_Y_copies_repo_relative_path_of_current_file(
    sample_files: list[PRFile],
) -> None:
    """Pressing `Y` should copy the repo-relative path of the current file."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        tree.select_node(file_tree._file_nodes["src/utils/file_tree.py"])
        await pilot.pause()

        await pilot.press("Y")
        await pilot.pause()

        assert app.clipboard == "src/utils/file_tree.py"


@pytest.mark.asyncio
async def test_g_and_G_move_file_tree_cursor_to_top_and_bottom(
    sample_files: list[PRFile],
) -> None:
    """Pressing `g`/`G` should move the file tree cursor to top/bottom."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        await pilot.pause()

        await pilot.press("G")
        await pilot.pause()

        assert tree.cursor_line == tree.last_line

        await pilot.press("g")
        await pilot.pause()

        assert tree.cursor_line == 0


@pytest.mark.asyncio
async def test_refresh_preserves_focused_tree_cursor(
    sample_files: list[PRFile],
) -> None:
    """Incremental file refresh should not jump tree navigation back to selection."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        file_tree.select_file("src/app.py", emit_message=False)
        tree.focus()
        tree.move_cursor(file_tree._file_nodes["tests/test_app.py"])
        await pilot.pause()

        file_tree.refresh_files(sample_files)
        await pilot.pause()
        await pilot.pause()

        assert tree.cursor_node is file_tree._file_nodes["tests/test_app.py"]
        assert file_tree.selected_file == "src/app.py"


@pytest.mark.asyncio
async def test_search_input_keeps_y_text_instead_of_copying(
    sample_files: list[PRFile],
) -> None:
    """Typing `y` in search mode should update the input instead of copying."""

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield FileTree(id="file-tree-sidebar")

    app = TestApp()
    async with app.run_test() as pilot:
        file_tree = app.query_one(FileTree)
        file_tree.refresh_files(sample_files)
        await pilot.pause()

        tree = file_tree.query_one("#file-tree", Tree)
        tree.focus()
        tree.select_node(file_tree._file_nodes["src/utils/file_tree.py"])
        await pilot.pause()

        await pilot.press("/")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        search = file_tree.query_one("#file-search", Input)
        assert search.value == "y"
        assert app.clipboard == ""
