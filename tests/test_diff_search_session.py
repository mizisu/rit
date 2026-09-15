"""Search ownership outcomes without a Textual app driver."""

import asyncio
import threading

import pytest
from textual.content import Content

from rit.core.types import DiffLine
from rit.ui.widgets import diff_search
from rit.ui.widgets.diff_search import DiffSearchSession, SearchCursor, SearchResult
from rit.ui.widgets.diff_types import DiffSearchMatch, RenderedRow
from tests.conftest import wait_until


def _document(*texts: str) -> tuple[list[DiffLine], list[RenderedRow]]:
    lines = [
        DiffLine(old_line_no=i + 1, new_line_no=i + 1, new_content=text)
        for i, text in enumerate(texts)
    ]
    rows = [
        RenderedRow(
            mode="unified",
            row_index=i,
            line_index=i,
            hunk_index=0,
            kind="context",
            side="auto",
            anchor_id=f"line-{i}",
            old_line_no=i + 1,
            new_line_no=i + 1,
        )
        for i in range(len(lines))
    ]
    return lines, rows


def test_search_session_typing_reveals_submission_activates_and_jump_syncs_cursor() -> (
    None
):
    cursor = SearchCursor(0, 0, "auto", 0)
    results: list[SearchResult] = []
    session = DiffSearchSession(lambda: cursor, results.append)
    lines, rows = _document("Alpha alpha", "other", "ALPHA")

    assert session.search("  alpha  ", lines, rows) is None
    assert session.query == "alpha"
    assert len(session.matches) == 3
    assert session.active_index == 1
    assert results[-1].reveal == DiffSearchMatch(0, 0, "auto", 6)
    assert results[-1].activation is None
    assert results[-1].dirty_lines == frozenset({0, 2})
    assert cursor == SearchCursor(0, 0, "auto", 0)

    session.search("alpha", lines, rows, submitted=True)
    activation = results[-1].activation
    assert activation is not None
    assert activation.match == session.matches[1]
    assert activation.pane is None
    assert not activation.update_active_pane
    assert results[-1].reveal is None

    cursor = SearchCursor(0, 0, "auto", 6)
    session.jump(1, lines, rows)
    assert session.active_index == 2
    assert results[-1].activation is not None
    assert results[-1].activation.dirty_lines == frozenset({0, 2})
    cursor = SearchCursor(2, 2, "auto", 0)
    session.jump(-1, lines, rows)
    assert session.active_index == 1
    cursor = SearchCursor(1, 1, "auto", 0)
    session.sync_cursor()
    assert session.active_index == -1


def test_search_session_reuses_buckets_and_clears_previously_highlighted_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results: list[SearchResult] = []
    session = DiffSearchSession(lambda: SearchCursor(0, 0, "auto", 0), results.append)
    lines, rows = _document("alpha beta", "beta", "alpha")
    session.search("beta", lines, rows)
    content = Content("alpha beta")
    first = session.highlight(content, 0, "auto")
    assert [(s.start, s.end, str(s.style)) for s in first.spans] == [
        (6, 10, "on $warning 45%")
    ]

    def unexpected_rebuild(*_args):
        raise AssertionError("highlight must reuse the line-side index")

    monkeypatch.setattr(diff_search, "build_match_buckets", unexpected_rebuild)
    assert session.highlight(content, 0, "auto") == first
    assert session.highlight(content, 0, "old") is content
    session.clear()
    assert results[-1].dirty_lines == frozenset({0, 1})
    assert session.query == ""
    assert not session.matches
    assert session.active_index == -1
    assert session.highlight(content, 0, "auto") is content
    session.repaint()
    assert not results[-1].dirty_lines


def test_search_session_projection_rebuild_and_render_clear() -> None:
    results: list[SearchResult] = []
    cursor = SearchCursor(0, 0, "new", 0)
    session = DiffSearchSession(lambda: cursor, results.append)
    lines = [
        DiffLine(
            old_line_no=1, new_line_no=1, old_content="needle", new_content="needle new", is_modified=True
        )
    ]
    split = [
        RenderedRow(
            mode="split",
            row_index=0,
            line_index=0,
            hunk_index=0,
            kind="modified-new",
            side="auto",
            anchor_id="line-0",
            old_line_no=1,
            new_line_no=1,
        )
    ]
    session.search("needle", lines, split)
    assert [match.side for match in session.matches] == ["old", "new"]
    unified = [
        RenderedRow(
            mode="unified",
            row_index=0,
            line_index=0,
            hunk_index=0,
            kind="added",
            side="new",
            anchor_id="line-0",
            old_line_no=None,
            new_line_no=1,
        )
    ]
    session.refresh(lines, unified)
    assert [match.side for match in session.matches] == ["new"]
    assert session.active_index == 0
    count = len(results)
    session.clear(repaint=False)
    assert len(results) == count
    assert session.query == ""
    assert not session.matches
    session.repaint()
    assert results[-1].dirty_lines == frozenset({0})


def test_search_session_blank_missing_and_unmatched_submissions() -> None:
    results: list[SearchResult] = []
    session = DiffSearchSession(lambda: SearchCursor(0, 0, "auto", 0), results.append)
    lines, rows = _document("alpha")
    session.search("alpha", lines, rows)
    count = len(results)
    session.search(None, lines, rows, submitted=True)
    assert len(results) == count
    assert session.query == "alpha"
    session.search("missing", lines, rows, submitted=True)
    assert session.query == "missing"
    assert not session.matches
    assert results[-1].dirty_lines == frozenset({0})
    assert results[-1].flash_message == "No matches: missing"
    assert results[-1].flash_style == "warning"
    session.search("  ", lines, rows, submitted=True)
    assert session.query == ""
    assert results[-1].flash_message == "Search cleared"
    assert results[-1].flash_duration == 1.5
    session.jump(1, lines, rows)
    assert results[-1].flash_message == "No active search"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement", ["new_query", "clear", "projection", "render_install"]
)
async def test_search_session_rejects_delayed_result_after_replacement(
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original_build = diff_search._build_search_index

    def delayed_build(*args):
        entered.set()
        assert release.wait(timeout=5)
        return original_build(*args)

    results: list[SearchResult] = []
    session = DiffSearchSession(lambda: SearchCursor(0, 0, "auto", 0), results.append)
    lines, rows = _document("old", "new")
    session.search("new", lines, rows)
    monkeypatch.setattr(diff_search, "_ASYNC_SEARCH_ROW_THRESHOLD", 1)
    monkeypatch.setattr(diff_search, "_SEARCH_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(diff_search, "_build_search_index", delayed_build)
    work = session.search("old", lines, rows)
    assert work is not None
    task = asyncio.create_task(work)
    try:
        await wait_until(entered.is_set)
        monkeypatch.setattr(diff_search, "_ASYNC_SEARCH_ROW_THRESHOLD", 5_000)
        if replacement == "new_query":
            session.search("new", lines, rows)
        elif replacement == "projection":
            session.refresh(lines, rows[1:])
        else:
            session.clear(repaint=replacement == "clear")
        state = (session.query, tuple(session.matches), session.active_index)
        effects = list(results)
        content = session.highlight(Content("old"), 0, "auto")
    finally:
        release.set()
        await task

    assert (session.query, tuple(session.matches), session.active_index) == state
    assert results == effects
    assert session.highlight(Content("old"), 0, "auto") == content
    assert not content.spans
