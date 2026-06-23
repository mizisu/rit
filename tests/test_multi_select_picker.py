import pytest
from textual.app import App
from textual.widgets import Input, OptionList
from textual.widgets.option_list import OptionDoesNotExist

from rit.ui.screens.multi_select_picker import (
    MultiSelectItem,
    MultiSelectPickerScreen,
    MultiSelectResult,
)


def test_multi_select_restore_highlight_falls_back_when_previous_option_is_missing() -> (
    None
):
    screen = MultiSelectPickerScreen(
        title="Edit assignees",
        items=[],
        selected_keys=set(),
    )

    class MissingOptionList:
        highlighted: int | None = None
        first_selected = False

        def get_option_index(self, _option_id: str) -> int:
            raise OptionDoesNotExist("missing")

        def action_first(self) -> None:
            self.first_selected = True

    options = MissingOptionList()

    screen._restore_highlight(options, "missing")

    assert options.highlighted is None
    assert options.first_selected is True


def test_multi_select_restore_highlight_reraises_unexpected_option_errors() -> None:
    screen = MultiSelectPickerScreen(
        title="Edit assignees",
        items=[],
        selected_keys=set(),
    )

    class BrokenOptionList:
        def get_option_index(self, _option_id: str) -> int:
            raise RuntimeError("option list failed")

        def action_first(self) -> None:
            raise AssertionError("fallback should not hide unexpected errors")

    with pytest.raises(RuntimeError, match="option list failed"):
        screen._restore_highlight(BrokenOptionList(), "broken")


@pytest.mark.asyncio
async def test_multi_select_picker_filters_items() -> None:
    items = [
        MultiSelectItem(key="user:alice", label="@alice"),
        MultiSelectItem(
            key="team:backend", label="Backend", search_text="team backend"
        ),
    ]

    class TestApp(App):
        def on_mount(self) -> None:
            self.push_screen(
                MultiSelectPickerScreen(
                    title="Edit requested reviewers",
                    items=items,
                    selected_keys=set(),
                )
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        screen = app.screen
        search = screen.query_one("#multi-select-search", Input)
        options = screen.query_one("#multi-select-options", OptionList)

        search.value = "back"
        await pilot.pause()

        assert [option.id for option in options.options] == ["team:backend"]


@pytest.mark.asyncio
async def test_multi_select_picker_toggles_and_submits_selection() -> None:
    items = [
        MultiSelectItem(key="user:alice", label="@alice"),
        MultiSelectItem(key="user:bob", label="@bob"),
    ]

    class TestApp(App):
        def __init__(self) -> None:
            super().__init__()
            self.result: MultiSelectResult | None = None

        def on_mount(self) -> None:
            self.push_screen(
                MultiSelectPickerScreen(
                    title="Edit assignees",
                    items=items,
                    selected_keys={"user:alice"},
                ),
                self._capture,
            )

        def _capture(self, result: MultiSelectResult | None) -> None:
            self.result = result

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.press("j")
        await pilot.press("space")
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.result == MultiSelectResult(selected_keys=("user:bob",))
