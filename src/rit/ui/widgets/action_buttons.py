from textual import events
from textual.containers import HorizontalGroup


class ActionButtons(HorizontalGroup):
    """Stack action buttons when their labels no longer fit in one row."""

    DEFAULT_CSS = """
    ActionButtons.-stacked {
        layout: vertical;
    }
    """

    def on_resize(self, event: events.Resize) -> None:
        row_width = sum(child.outer_size.width for child in self.children)
        self.set_class(row_width > event.size.width, "-stacked")
