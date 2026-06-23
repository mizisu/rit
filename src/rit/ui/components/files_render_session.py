from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rit.core.types import FileDiff
from rit.state.models import PRFile
from rit.ui.components.combined_diff import (
    COMBINED_DIFF_FILENAME,
    CombinedDiffDocument,
)

__all__ = (
    "CombinedFileJump",
    "CombinedRenderRequest",
    "FilesRenderSession",
    "FullFilePreviewRestoreTarget",
    "PendingLocationJump",
)


@dataclass(frozen=True)
class CombinedRenderRequest:
    signature: tuple[str, ...]
    focus_diff: bool


@dataclass(frozen=True)
class CombinedFileJump:
    filename: str
    focus_diff: bool


@dataclass(frozen=True)
class PendingLocationJump:
    filename: str
    line: int
    side: Literal["LEFT", "RIGHT"]
    focus_diff: bool


@dataclass(frozen=True)
class FullFilePreviewRestoreTarget:
    filename: str
    diff: FileDiff


class FilesRenderSession:
    """Tracks render-session state for the Files tab."""

    def __init__(self, *, combined_threshold: int = 2) -> None:
        self._combined_threshold = combined_threshold
        self._queued_combined_render: CombinedRenderRequest | None = None
        self._combined_files_signature: tuple[str, ...] | None = None
        self._combined_document: CombinedDiffDocument | None = None
        self._showing_combined_files = False
        self._pending_combined_file_jump: CombinedFileJump | None = None
        self._pending_location_jump: PendingLocationJump | None = None

    @property
    def combined_document(self) -> CombinedDiffDocument | None:
        return self._combined_document

    @property
    def showing_combined_files(self) -> bool:
        return self._showing_combined_files

    def files_signature(self, files: Sequence[PRFile]) -> tuple[str, ...]:
        return tuple(file.filename for file in files)

    def uses_combined_files(self, files: Sequence[PRFile]) -> bool:
        return len(files) >= self._combined_threshold

    def set_showing_combined_files(self, showing: bool) -> None:
        self._showing_combined_files = showing

    def record_combined_document(
        self,
        signature: tuple[str, ...],
        document: CombinedDiffDocument,
    ) -> None:
        self._combined_files_signature = signature
        self._combined_document = document
        self._showing_combined_files = True

    def queue_combined_render(
        self,
        files: Sequence[PRFile],
        *,
        current_file: str | None,
        focus_diff: bool = False,
        force: bool = False,
    ) -> bool:
        signature = self.files_signature(files)
        if len(signature) < self._combined_threshold:
            return False

        if (
            not force
            and self._showing_combined_files
            and current_file == COMBINED_DIFF_FILENAME
            and self._combined_files_signature == signature
        ):
            return True

        self._queued_combined_render = CombinedRenderRequest(signature, focus_diff)
        return True

    def take_queued_combined_render(self) -> CombinedRenderRequest | None:
        request = self._queued_combined_render
        self._queued_combined_render = None
        return request

    def has_queued_combined_render(self) -> bool:
        return self._queued_combined_render is not None

    def queue_combined_file_jump(
        self,
        files: Sequence[PRFile],
        filename: str,
        *,
        focus_diff: bool,
    ) -> bool:
        if not self.uses_combined_files(files):
            return False

        self._pending_combined_file_jump = CombinedFileJump(
            filename=filename,
            focus_diff=focus_diff,
        )
        return True

    def take_pending_combined_file_jump(self) -> CombinedFileJump | None:
        pending = self._pending_combined_file_jump
        self._pending_combined_file_jump = None
        return pending

    def queue_location_jump(
        self,
        filename: str,
        line: int,
        side: Literal["LEFT", "RIGHT"],
        *,
        focus_diff: bool,
    ) -> None:
        self._pending_location_jump = PendingLocationJump(
            filename=filename,
            line=line,
            side=side,
            focus_diff=focus_diff,
        )

    def take_pending_location_jump(
        self,
        rendered_filename: str,
    ) -> PendingLocationJump | None:
        pending = self._pending_location_jump
        if pending is None:
            return None
        if (
            rendered_filename != COMBINED_DIFF_FILENAME
            and pending.filename != rendered_filename
        ):
            return None

        self._pending_location_jump = None
        return pending

    def full_file_preview_restore_target(
        self,
        *,
        filename: str,
        file_diff: FileDiff,
        current_file: str | None,
        current_diff: FileDiff | None,
    ) -> FullFilePreviewRestoreTarget:
        if (
            self._showing_combined_files
            and current_file == COMBINED_DIFF_FILENAME
            and current_diff is not None
        ):
            return FullFilePreviewRestoreTarget(
                filename=COMBINED_DIFF_FILENAME,
                diff=current_diff,
            )

        return FullFilePreviewRestoreTarget(filename=filename, diff=file_diff)

    def combined_file_for_line(self, line_index: int) -> str | None:
        if not self._showing_combined_files or self._combined_document is None:
            return None
        return self._combined_document.file_for_line(line_index)
