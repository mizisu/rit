import inspect

import pytest
from textual.app import App

import rit.ui.screens.branch_picker as branch_picker_module
from rit.ui.screens.branch_picker import BranchPickerScreen


def test_branch_picker_does_not_use_runtime_casts() -> None:
    source = inspect.getsource(branch_picker_module)

    assert "cast(" not in source


@pytest.mark.asyncio
async def test_branch_picker_cancel_button_dismisses_without_selection() -> None:
    results: list[str | None] = []
    app = App()

    async with app.run_test() as pilot:
        app.push_screen(
            BranchPickerScreen(head="feature", base="main"),
            results.append,
        )
        await pilot.pause()
        await pilot.click("#branch-picker-cancel")
        await pilot.pause()

    assert results == [None]
