"""Tests for the go-to-file fuzzy picker."""

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from rit.state.models import PRFile
from rit.ui.screens.file_picker import FilePickerScreen, rank_file_matches


@pytest.fixture
def picker_files() -> list[PRFile]:
    return [
        PRFile(filename="src/client/http.py", additions=2, deletions=1),
        PRFile(filename="src/http/client.py", additions=12, deletions=3),
        PRFile(filename="tests/client/test_http.py", additions=5, deletions=0),
    ]


def test_fuzzy_ranking_prefers_basename_over_path_segment(
    picker_files: list[PRFile],
) -> None:
    matches = rank_file_matches(picker_files, "client")

    assert [match.file.filename for match in matches[:2]] == [
        "src/http/client.py",
        "src/client/http.py",
    ]


def test_fuzzy_ranking_rewards_path_segment_matches() -> None:
    files = [
        PRFile(filename="src/rit/ui/widgets/file_tree.py"),
        PRFile(filename="src/rit/widgets/ui_file_tree.py"),
        PRFile(filename="docs/file-tree-ui.md"),
    ]

    matches = rank_file_matches(files, "ui tree")

    assert matches[0].file.filename == "src/rit/ui/widgets/file_tree.py"


def test_picker_option_prompt_includes_highlight_index_and_stats(
    picker_files: list[PRFile],
) -> None:
    match = rank_file_matches(picker_files, "client")[0]
    prompt = FilePickerScreen.option_prompt(match, total_count=len(picker_files))

    assert isinstance(prompt, Text)
    assert "2/3" in prompt.plain
    assert "src/http/client.py" in prompt.plain
    assert "+12" in prompt.plain
    assert "-3" in prompt.plain
    assert any("bold cyan" in str(span.style) for span in prompt.spans)


@pytest.mark.asyncio
async def test_file_picker_filters_and_submits_highlighted_file(
    picker_files: list[PRFile],
) -> None:
    result: list[str | None] = []

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield Input(id="sentinel")

        def on_mount(self) -> None:
            self.push_screen(
                FilePickerScreen(files=picker_files, selected_file=None),
                result.append,
            )

    app = TestApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        search = app.screen.query_one("#file-picker-search", Input)
        options = app.screen.query_one("#file-picker-options", OptionList)

        assert search.has_focus

        await pilot.press("h", "t", "t", "p")
        await pilot.pause()

        highlighted = options.highlighted_option
        assert highlighted is not None
        assert highlighted.id == "src/client/http.py"

        await pilot.press("enter")
        await pilot.pause()

        assert result == ["src/client/http.py"]
