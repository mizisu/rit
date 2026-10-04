"""Cell-safe file labels and status-aware check presentation."""

import pytest
from rich.color import Color
from textual.content import Content
from textual.style import Style
from textual.visual import RenderOptions

from rit.services.pr_checks import parse_pr_check
from rit.state.models import PRFile
from rit.state.pr_overview import PRChecksSnapshot
from rit.ui.components.pr_overview import FileSummaryLabel, PRChecks


@pytest.mark.parametrize(
    "path",
    [
        "a_really_long_filename_with_underscores.py",
        "directory/name with spaces.py",
        "한글과🧪아주긴파일이름.py",
        "a\nfilename\twith\rcontrols.py",
        "[bold]not_markup.py",
    ],
)
def test_file_labels_are_cell_safe_single_lines_with_counts_and_no_markup(
    path: str,
) -> None:
    file = PRFile(filename=path, status="renamed", additions=123, deletions=45)
    label = FileSummaryLabel(file, path)

    def get_style(style: str | Style) -> Style:
        return Style.parse(style) if isinstance(style, str) else style

    options = RenderOptions(
        get_style=get_style,
        rules={"text_wrap": "nowrap", "text_overflow": "ellipsis"},
    )
    for width in (0, 1, 7, 14, 20, 38):
        assert label.get_height({}, width) == 1
        strips = label.render_strips(width, None, Style(), options)
        assert len(strips) == 1
        row = strips[0]
        assert row.cell_length <= width
        assert not any(character in row.text for character in "\n\r\t")
        if width >= 20:
            assert "+123 -45" in row.text
        if width == 38 and path.startswith("[bold]"):
            assert "[bold]" in row.text


@pytest.mark.parametrize(
    ("status", "conclusion", "state", "label", "color"),
    [
        ("COMPLETED", "SUCCESS", "SUCCESS", "All passed", "#a6da95"),
        ("IN_PROGRESS", None, "PENDING", "Checks running", "#eed49f"),
        ("COMPLETED", "FAILURE", "FAILURE", "Checks failed", "#ed8796"),
        ("COMPLETED", "CANCELLED", "SUCCESS", "Checks cancelled", "#eed49f"),
        ("COMPLETED", "STALE", "SUCCESS", "Unknown check results", "#eed49f"),
        ("COMPLETED", None, "SUCCESS", "Unknown check results", "#eed49f"),
        ("COMPLETED", "SKIPPED", "SUCCESS", "Checks completed", "#939ab7"),
        ("COMPLETED", "NEUTRAL", "SUCCESS", "Checks completed", "#939ab7"),
    ],
)
def test_check_status_colors_match_summary_and_details(
    status: str, conclusion: str | None, state: str, label: str, color: str
) -> None:
    check = parse_pr_check(
        {
            "__typename": "CheckRun",
            "id": "ci",
            "name": "CI",
            "status": status,
            "conclusion": conclusion,
        }
    )
    summary = PRChecks.summary(PRChecksSnapshot("head", state, (check,)))
    assert label in summary.plain
    prompt = PRChecks._check_option(check).prompt
    assert isinstance(prompt, Content)

    def get_style(style: str | Style) -> Style:
        return Style.parse(style) if isinstance(style, str) else style

    options = RenderOptions(get_style=get_style, rules={})
    for content, text in ((summary, label), (prompt, check.detail.lower())):
        strip = content.render_strips(80, 1, Style.parse("#cad3f5"), options)[0]
        segments = [segment for segment in strip if text in segment.text]
        assert len(segments) == 1
        style = segments[0].style
        assert style is not None and style.color == Color.parse(color)


def test_successful_checks_with_skipped_jobs_keep_a_green_rollup() -> None:
    checks = tuple(
        parse_pr_check(
            {
                "__typename": "CheckRun",
                "id": str(index),
                "name": f"CI {index}",
                "status": "COMPLETED",
                "conclusion": "SUCCESS" if index < 14 else "SKIPPED",
            }
        )
        for index in range(15)
    )
    snapshot = PRChecksSnapshot("head", "SUCCESS", checks)
    assert snapshot.outcome == "success"
    summary = PRChecks.summary(snapshot)
    assert summary.plain == "✓ Checks passed · 15"
    assert summary.spans[0].style == "#a6da95"
    assert "All passed" not in summary.plain
