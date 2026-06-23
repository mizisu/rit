from rit.core.types import FileDiff
from rit.state.models import PRFile
from rit.ui.components.combined_diff import (
    COMBINED_DIFF_FILENAME,
    CombinedDiffDocument,
)
from rit.ui.components.files_render_session import (
    CombinedFileJump,
    FilesRenderSession,
    FullFilePreviewRestoreTarget,
    PendingLocationJump,
)


def _files(*filenames: str) -> list[PRFile]:
    return [PRFile(filename=filename) for filename in filenames]


def _document() -> CombinedDiffDocument:
    return CombinedDiffDocument(
        diff=FileDiff(filename=COMBINED_DIFF_FILENAME),
        file_line_starts={"one.py": 0, "two.py": 4},
        file_start_lines=(0, 4),
        file_start_names=("one.py", "two.py"),
        line_lookup={("two.py", 12, "RIGHT"): 5},
    )


def test_queue_combined_render_skips_current_signature() -> None:
    session = FilesRenderSession()
    files = _files("one.py", "two.py")
    signature = session.files_signature(files)
    session.record_combined_document(signature, _document())

    assert session.queue_combined_render(
        files,
        current_file=COMBINED_DIFF_FILENAME,
    )
    assert session.take_queued_combined_render() is None


def test_queue_combined_render_records_latest_request() -> None:
    session = FilesRenderSession()
    files = _files("one.py", "two.py")

    assert session.queue_combined_render(files, current_file=None, focus_diff=True)

    request = session.take_queued_combined_render()
    assert request is not None
    assert request.signature == ("one.py", "two.py")
    assert request.focus_diff is True
    assert session.take_queued_combined_render() is None


def test_pending_combined_file_jump_is_single_use() -> None:
    session = FilesRenderSession()

    assert session.queue_combined_file_jump(
        _files("one.py", "two.py"),
        "two.py",
        focus_diff=True,
    )

    assert session.take_pending_combined_file_jump() == CombinedFileJump(
        filename="two.py",
        focus_diff=True,
    )
    assert session.take_pending_combined_file_jump() is None


def test_pending_location_jump_waits_for_matching_render_target() -> None:
    session = FilesRenderSession()
    session.queue_location_jump(
        "two.py",
        12,
        "RIGHT",
        focus_diff=True,
    )

    assert session.take_pending_location_jump("one.py") is None
    assert session.take_pending_location_jump(
        COMBINED_DIFF_FILENAME
    ) == PendingLocationJump(
        filename="two.py",
        line=12,
        side="RIGHT",
        focus_diff=True,
    )
    assert session.take_pending_location_jump(COMBINED_DIFF_FILENAME) is None


def test_full_file_preview_restore_target_defaults_to_selected_file() -> None:
    session = FilesRenderSession()
    file_diff = FileDiff(filename="one.py")

    assert session.full_file_preview_restore_target(
        filename="one.py",
        file_diff=file_diff,
        current_file="one.py",
        current_diff=None,
    ) == FullFilePreviewRestoreTarget(filename="one.py", diff=file_diff)


def test_full_file_preview_restore_target_preserves_combined_diff() -> None:
    session = FilesRenderSession()
    session.record_combined_document(("one.py", "two.py"), _document())
    file_diff = FileDiff(filename="one.py")
    combined_diff = FileDiff(filename=COMBINED_DIFF_FILENAME)

    assert session.full_file_preview_restore_target(
        filename="one.py",
        file_diff=file_diff,
        current_file=COMBINED_DIFF_FILENAME,
        current_diff=combined_diff,
    ) == FullFilePreviewRestoreTarget(
        filename=COMBINED_DIFF_FILENAME,
        diff=combined_diff,
    )
