from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive, var
from textual.widgets import Input, Static, Tree
from textual.widgets.tree import TreeNode

from rit.state.models import PRFile
from rit.ui.protocols import NavigableProtocol

if TYPE_CHECKING:
    from rit.state.store import PRStore


class ReviewTree(Tree[str]):
    """Tree with space key unbound for leader key support."""

    def on_mount(self) -> None:
        super().on_mount()
        # Hack: remove default Tree bindings so they bubble up to MainScreen
        try:
            if hasattr(self._bindings, "keys"):
                keys_dict = getattr(self._bindings, "keys")
                if "space" in keys_dict:
                    del keys_dict["space"]
                if "enter" in keys_dict:
                    del keys_dict["enter"]
        except Exception:
            pass


class FileTree(Vertical):
    """File tree sidebar showing changed files."""

    DEFAULT_CSS = """
    FileTree {
        width: 35;
        min-width: 25;
        max-width: 80;
    }

    FileTree .tree-header {
        text-style: bold;
        padding: 0 1;
        background: $surface;
        height: 3;
        content-align: left middle;
    }

    FileTree #file-search {
        margin: 0 1;
        height: 1;
        min-height: 1;
        padding: 0 1;
        border: none;
        background: $surface;
        color: $foreground;
        display: none;
    }

    FileTree #file-search:focus {
        background: $primary 10%;
    }

    FileTree Tree {
        padding: 0 1;
    }

    """

    BINDINGS = [
        Binding("j", "cursor_down", "Next", show=False),
        Binding("k", "cursor_up", "Prev", show=False),
        Binding("h", "collapse_or_parent", "Collapse", show=False),
        Binding("l", "expand_or_child", "Expand", show=False),
        Binding("/", "start_search", "Search", show=False),
    ]

    @dataclass
    class FileSelected(Message):
        filename: str

    @dataclass
    class FilePreviewed(Message):
        filename: str

    file_count: reactive[int] = reactive(0)
    total_file_count: reactive[int] = reactive(0)
    selected_file: reactive[str | None] = reactive(None)

    _file_nodes: var[dict[str, TreeNode[str]]] = var({})
    _all_files: var[list[PRFile]] = var([])
    _filtered_files: var[list[PRFile]] = var([])
    _search_query: var[str] = var("")
    _search_open: var[bool] = var(False)

    def __init__(
        self,
        store: PRStore | None = None,
        *,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.store = store
        self._file_nodes = {}
        self._all_files = []
        self._filtered_files = []
        self._search_query = ""
        self._search_open = False

    def compose(self) -> ComposeResult:
        yield Static("Files (0)", classes="tree-header", id="file-count")
        yield Input(placeholder="Search files", id="file-search")
        tree: Tree[str] = Tree("Files", id="file-tree")
        tree.show_root = False
        tree.root.expand()
        yield tree

    def watch_file_count(self, _count: int) -> None:
        self._update_file_count_display()

    def watch_total_file_count(self, _count: int) -> None:
        self._update_file_count_display()

    def refresh_files(self, files: list[PRFile] | None = None) -> None:
        if files is None and self.store:
            files = self.store.state.files

        if files is None:
            files = []

        self._all_files = list(files)
        self.total_file_count = len(self._all_files)

        self._apply_search_filter()

    def select_file(self, filename: str, *, emit_message: bool = True) -> None:
        self.selected_file = filename
        emitted_via_tree = False

        node = self._file_nodes.get(filename)
        if node is not None:
            tree = self.query_one("#file-tree", Tree)
            if tree.cursor_node is not node:
                if emit_message:
                    tree.select_node(node)
                    emitted_via_tree = True
                else:
                    tree.move_cursor(node)

        if emit_message and not emitted_via_tree:
            self.post_message(self.FileSelected(filename=filename))

    def action_start_search(self) -> None:
        if not self._all_files:
            return

        if self._filtered_files:
            self._focus_file_in_tree(self._filtered_files[0].filename)

        search = self.query_one("#file-search", Input)
        search.display = True
        search.value = self._search_query
        search.focus()
        self._search_open = True

    def action_cancel_search(self) -> None:
        if not self._search_open:
            return

        self._close_search(clear_query=True)

    def action_cursor_down(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.action_cursor_down()

    def action_cursor_up(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.action_cursor_up()

    def action_collapse_or_parent(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        if tree.cursor_node:
            if tree.cursor_node.is_expanded and tree.cursor_node.allow_expand:
                tree.cursor_node.collapse()
            elif tree.cursor_node.parent and tree.cursor_node.parent != tree.root:
                tree.select_node(tree.cursor_node.parent)

    def action_expand_or_child(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        if tree.cursor_node:
            if not tree.cursor_node.is_expanded and tree.cursor_node.allow_expand:
                tree.cursor_node.expand()
            elif tree.cursor_node.is_expanded and tree.cursor_node.children:
                tree.select_node(tree.cursor_node.children[0])

    def next_item(self) -> None:
        current = self.get_current_index()
        total = self.get_item_count()
        if total > 0:
            next_index = (current + 1) % total
            self.select_item(next_index)

    def prev_item(self) -> None:
        current = self.get_current_index()
        total = self.get_item_count()
        if total > 0:
            prev_index = (current - 1) % total
            self.select_item(prev_index)

    def select_item(self, index: int) -> None:
        if self.store is None:
            return

        files = self.store.state.files
        if 0 <= index < len(files):
            self.select_file(files[index].filename)

    def get_current_index(self) -> int:
        if self.store is None or not self.selected_file:
            return -1

        files = self.store.state.files
        for i, f in enumerate(files):
            if f.filename == self.selected_file:
                return i
        return -1

    def get_item_count(self) -> int:
        if self.store is None:
            return 0
        return len(self.store.state.files)

    def on_key(self, event: events.Key) -> None:
        if event.key == "space" and not self._search_open:
            tree = self.query_one("#file-tree", Tree)
            node = tree.cursor_node
            if node is not None and node.data:
                self.selected_file = node.data
                self.post_message(self.FilePreviewed(filename=node.data))
                event.stop()
                event.prevent_default()
            return

        if not self._search_open:
            return

        tree = self.query_one("#file-tree", Tree)

        if event.key == "escape":
            self._close_search(clear_query=True)
            event.stop()
            return

        if event.key == "down":
            tree.action_cursor_down()
            event.stop()
            return

        if event.key == "up":
            tree.action_cursor_up()
            event.stop()

    @on(Tree.NodeSelected)
    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        if event.node.data:
            self.selected_file = event.node.data
            self.post_message(self.FileSelected(filename=event.node.data))

            if self.store:
                self.store.select_file(event.node.data)

    @on(Input.Changed, "#file-search")
    def on_search_input_changed(self, event: Input.Changed) -> None:
        if not self._search_open:
            return
        self._search_query = event.value.strip()
        self._apply_search_filter()

    @on(Input.Submitted, "#file-search")
    def on_search_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

        tree = self.query_one("#file-tree", Tree)
        node = tree.cursor_node
        if node is None:
            return

        if node.allow_expand:
            if node.is_expanded:
                node.collapse()
            else:
                node.expand()
            return

        if not node.data:
            return

        filename = node.data
        self.selected_file = filename
        self._close_search(clear_query=True)
        self.call_after_refresh(lambda: self.select_file(filename))

    def _render_files(self, files: list[PRFile]) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.clear()
        self._file_nodes = {}

        if not files:
            empty_label = (
                "No matching files" if self._search_query else "No files changed"
            )
            tree.root.add_leaf(empty_label)
            return

        dir_nodes: dict[str, TreeNode[str]] = {}

        for file in files:
            parts = file.filename.split("/")

            if len(parts) == 1:
                node = tree.root.add_leaf(
                    self._file_label(file),
                    data=file.filename,
                )
                self._file_nodes[file.filename] = node
            else:
                parent = tree.root
                for i, part in enumerate(parts[:-1]):
                    current_path = "/".join(parts[: i + 1])
                    if current_path not in dir_nodes:
                        dir_nodes[current_path] = parent.add(part, expand=True)
                    parent = dir_nodes[current_path]

                node = parent.add_leaf(
                    self._file_label(file, show_path=False),
                    data=file.filename,
                )
                self._file_nodes[file.filename] = node

    def _apply_search_filter(self) -> None:
        if self._search_query:
            query = self._search_query.lower()
            self._filtered_files = [
                file for file in self._all_files if query in file.filename.lower()
            ]
        else:
            self._filtered_files = list(self._all_files)

        self.file_count = len(self._filtered_files)
        self._render_files(self._filtered_files)

        if self.selected_file and self.selected_file in self._file_nodes:
            self.select_file(self.selected_file, emit_message=False)
            return

        if self._filtered_files:
            first_match = self._filtered_files[0].filename
            self.call_after_refresh(lambda: self._focus_file_in_tree(first_match))

    def _focus_file_in_tree(self, filename: str) -> None:
        node = self._file_nodes.get(filename)
        if node is None:
            return

        tree = self.query_one("#file-tree", Tree)
        if tree.cursor_node is not node:
            tree.move_cursor(node)

    def _close_search(self, *, clear_query: bool) -> None:
        search = self.query_one("#file-search", Input)
        search.display = False
        self._search_open = False

        if clear_query:
            self._search_query = ""
            if search.value:
                search.value = ""
            self._apply_search_filter()

        self.query_one("#file-tree", Tree).focus()

    def _update_file_count_display(self) -> None:
        try:
            count_widget = self.query_one("#file-count", Static)
            if self._search_query:
                count_widget.update(
                    f"Files ({self.file_count}/{self.total_file_count})"
                )
            else:
                count_widget.update(f"Files ({self.total_file_count})")
        except Exception:
            pass

    def _file_label(self, file: PRFile, show_path: bool = True) -> Text:
        name = file.filename if show_path else file.filename.split("/")[-1]

        status_colors = {
            "added": "green",
            "removed": "red",
            "modified": "yellow",
            "renamed": "blue",
        }
        color = status_colors.get(file.status, "white")

        text = Text()
        text.append(f"{file.status_icon} ", style=color)
        text.append(name)
        text.append(f" +{file.additions}", style="green")
        text.append(f" -{file.deletions}", style="red")

        if file.comments:
            text.append(f" [{len(file.comments)}]", style="cyan")

        return text
