from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive, var
from textual.widgets import Input, Static, Tree
from textual.widgets.tree import TreeNode

from rit.state.models import FileViewedState, PRFile
from rit.ui.messages import Flash

if TYPE_CHECKING:
    from rit.state.store import PRStore


__all__ = ("FileTree",)


@dataclass
class _DirectoryContents:
    child_dirs: dict[str, str] = field(default_factory=dict)
    entries: list[tuple[Literal["directory", "file"], str]] = field(
        default_factory=list
    )
    direct_file_count: int = 0


class ReviewTree(Tree[str]):
    """Tree with space key unbound for leader key support."""

    def on_mount(self) -> None:
        super().on_mount()
        _remove_review_tree_default_bindings(self._bindings)


def _remove_review_tree_default_bindings(bindings: object) -> None:
    keys_dict = getattr(bindings, "keys", None)
    if not isinstance(keys_dict, dict):
        return

    keys_dict.pop("space", None)
    keys_dict.pop("enter", None)


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
        Binding("g", "go_top", "Go to Top", show=False),
        Binding("G", "go_bottom", "Go to Bottom", show=False),
        Binding("h", "collapse_or_parent", "Collapse", show=False),
        Binding("l", "expand_or_child", "Expand", show=False),
        Binding("ctrl+d", "half_page_down", "Half Page Down", show=False),
        Binding("ctrl+u", "half_page_up", "Half Page Up", show=False),
        Binding("y", "copy_filename", "Copy Filename", show=False),
        Binding("Y", "copy_path", "Copy Path", show=False),
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
        tree.guide_depth = 2
        tree.root.expand()
        yield tree

    def watch_file_count(self, _count: int) -> None:
        self._update_file_count_display()

    def watch_total_file_count(self, _count: int) -> None:
        self._update_file_count_display()

    def refresh_files(self, files: list[PRFile] | None = None) -> None:
        total_file_count = 0
        if files is None and self.store:
            files = self.store.state.files
            total_file_count = self.store.state.files_total_count

        if files is None:
            files = []

        self._all_files = list(files)
        self.total_file_count = max(total_file_count, len(self._all_files))

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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self._search_open and isinstance(self.screen.focused, Input):
            return False
        return super().check_action(action, parameters)

    def action_cursor_down(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.action_cursor_down()

    def action_cursor_up(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.action_cursor_up()

    def action_go_top(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.action_scroll_home()

    def action_go_bottom(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.action_scroll_end()

    def action_half_page_down(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        step = max(1, tree.scrollable_content_region.height // 2)
        for _ in range(step):
            tree.action_cursor_down()

    def action_half_page_up(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        step = max(1, tree.scrollable_content_region.height // 2)
        for _ in range(step):
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

    def _copy_current_file(self, *, basename_only: bool) -> None:
        tree = self.query_one("#file-tree", Tree)
        node = tree.cursor_node
        if node is None or not node.data:
            self.post_message(Flash("No file selected", style="warning", duration=2.0))
            return

        filename = node.data.split("/")[-1] if basename_only else node.data
        label = "filename" if basename_only else "file path"
        self.app.copy_to_clipboard(filename)
        self.post_message(
            Flash(
                f"Copied {label}: {filename}",
                style="success",
                duration=2.0,
            )
        )

    def action_copy_filename(self) -> None:
        self._copy_current_file(basename_only=True)

    def action_copy_path(self) -> None:
        self._copy_current_file(basename_only=False)

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

        contents_by_path, files_by_path = self._build_directory_contents(files)
        self._render_directory_contents(
            tree.root,
            "",
            contents_by_path,
            files_by_path,
        )

    def _build_directory_contents(
        self, files: list[PRFile]
    ) -> tuple[dict[str, _DirectoryContents], dict[str, PRFile]]:
        contents_by_path = {"": _DirectoryContents()}
        files_by_path: dict[str, PRFile] = {}

        for file in files:
            files_by_path[file.filename] = file
            parts = file.filename.split("/")
            parent_path = ""

            if len(parts) == 1:
                contents_by_path[parent_path].entries.append(("file", file.filename))
                contents_by_path[parent_path].direct_file_count += 1
                continue

            for i, part in enumerate(parts[:-1]):
                current_path = "/".join(parts[: i + 1])
                parent_contents = contents_by_path.setdefault(
                    parent_path, _DirectoryContents()
                )

                if part not in parent_contents.child_dirs:
                    parent_contents.child_dirs[part] = current_path
                    parent_contents.entries.append(("directory", current_path))

                contents_by_path.setdefault(current_path, _DirectoryContents())
                parent_path = current_path

            contents_by_path[parent_path].entries.append(("file", file.filename))
            contents_by_path[parent_path].direct_file_count += 1

        return contents_by_path, files_by_path

    def _render_directory_contents(
        self,
        parent: TreeNode[str],
        directory_path: str,
        contents_by_path: dict[str, _DirectoryContents],
        files_by_path: dict[str, PRFile],
    ) -> None:
        for kind, path in contents_by_path[directory_path].entries:
            if kind == "file":
                file = files_by_path[path]
                show_path = "/" not in file.filename
                node = parent.add_leaf(
                    self._file_label(file, show_path=show_path),
                    data=file.filename,
                )
                self._file_nodes[file.filename] = node
                continue

            label, compacted_path = self._compact_directory_path(
                path,
                contents_by_path,
            )
            node = parent.add(label, expand=True)
            self._render_directory_contents(
                node,
                compacted_path,
                contents_by_path,
                files_by_path,
            )

    def _compact_directory_path(
        self,
        directory_path: str,
        contents_by_path: dict[str, _DirectoryContents],
    ) -> tuple[str, str]:
        path = directory_path
        label_parts = [path.rsplit("/", 1)[-1]]

        while True:
            contents = contents_by_path[path]
            if contents.direct_file_count or len(contents.child_dirs) != 1:
                break

            child_path = next(iter(contents.child_dirs.values()))
            label_parts.append(child_path.rsplit("/", 1)[-1])
            path = child_path

        return "/".join(label_parts), path

    def _apply_search_filter(self) -> None:
        tree_was_focused, cursor_file, cursor_line = self._focused_cursor_state()

        if self._search_query:
            query = self._search_query.lower()
            self._filtered_files = [
                file for file in self._all_files if query in file.filename.lower()
            ]
        else:
            self._filtered_files = list(self._all_files)

        self.file_count = len(self._filtered_files)
        self._render_files(self._filtered_files)

        if tree_was_focused:
            if cursor_file and cursor_file in self._file_nodes:
                self.call_after_refresh(lambda: self._focus_file_in_tree(cursor_file))
                return
            if cursor_line is not None:
                self.call_after_refresh(lambda: self._focus_tree_line(cursor_line))
                return

        if self.selected_file and self.selected_file in self._file_nodes:
            self.select_file(self.selected_file, emit_message=False)
            return

        if self._filtered_files:
            first_match = self._filtered_files[0].filename
            self.call_after_refresh(lambda: self._focus_file_in_tree(first_match))

    def _focused_cursor_state(self) -> tuple[bool, str | None, int | None]:
        try:
            tree = self.query_one("#file-tree", Tree)
        except NoMatches:
            return (False, None, None)

        if not tree.has_focus:
            return (False, None, None)

        node = tree.cursor_node
        cursor_file = node.data if node is not None and node.data else None
        return (True, cursor_file, tree.cursor_line)

    def _focus_tree_line(self, line: int) -> None:
        tree = self.query_one("#file-tree", Tree)
        target_line = max(0, min(line, tree.last_line))
        tree.move_cursor_to_line(target_line)
        tree.focus()

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
            is_partial_file_list = self.file_count < self.total_file_count
            if self._search_query or is_partial_file_list:
                count_widget.update(
                    f"Files ({self.file_count}/{self.total_file_count})"
                )
            else:
                count_widget.update(f"Files ({self.total_file_count})")
        except NoMatches:
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

        if file.viewer_viewed_state == FileViewedState.VIEWED:
            text.append("✓ ", style="green")
        elif file.viewer_viewed_state == FileViewedState.DISMISSED:
            text.append("! ", style="yellow")
        else:
            text.append("○ ", style="dim")

        text.append(f"{file.status_icon} ", style=color)
        text.append(name)
        text.append(f" +{file.additions}", style="green")
        text.append(f" -{file.deletions}", style="red")

        if file.comments:
            text.append(f" [{len(file.comments)}]", style="cyan")

        pending_count = self._pending_comment_count(file.filename)
        if pending_count:
            text.append(f" [draft {pending_count}]", style="yellow")

        return text

    def _pending_comment_count(self, filename: str) -> int:
        if self.store is None:
            return 0
        return len(self.store.get_pending_file_comments(filename))

    def update_view_state(self, filename: str) -> None:
        """Re-render the label for a single file node (no tree rebuild)."""
        node = self._file_nodes.get(filename)
        if node is None:
            return
        file = next((f for f in self._all_files if f.filename == filename), None)
        if file is None:
            return
        show_path = "/" not in filename
        node.set_label(self._file_label(file, show_path=show_path))
