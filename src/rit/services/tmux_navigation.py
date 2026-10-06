from __future__ import annotations

import os
import shlex
import subprocess
from types import TracebackType
from typing import Literal, Self


class TmuxNavigation:
    """Advertise this process to the optional tmux navigation plugin."""

    def __init__(self) -> None:
        self._pane = (
            os.environ.get("TMUX_PANE")
            if os.environ.get("TMUX") and os.isatty(0)
            else None
        )
        self._identity: str | None = None
        self._restore: str | None = None

    def __enter__(self) -> Self:
        if self._pane is None:
            return self
        if self._tmux("show-options", "-sqv", "@rit-navigation-enabled") != "1":
            return self
        started = self._run("ps", "-p", str(os.getpid()), "-o", "lstart=")
        if started:
            self._identity = f"{os.getpid()} {' '.join(started.split())}"
            self.resume()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.pause()

    def resume(self) -> None:
        """Claim this pane while the application owns the terminal."""
        if self._pane is None or self._identity is None or self._restore is not None:
            return
        option = self._tmux("show-options", "-pq", "-t", self._pane, "@rit_nav")
        if option is None:
            return
        target = shlex.quote(self._pane)
        # tmux treats a trailing semicolon in a CLI argument as a command separator.
        self._restore = (
            f"set-option -p -t {target} {option}\n"
            if option
            else f"set-option -pu -t {target} @rit_nav"
        )
        self._tmux("set-option", "-p", "-t", self._pane, "@rit_nav", self._identity)

    def pause(self) -> None:
        """Restore the pane state without overwriting a newer owner."""
        if self._pane is None or self._restore is None:
            return
        restored = self._tmux(
            "if-shell",
            "-t",
            self._pane,
            "-F",
            f"#{{==:#{{@rit_nav}},{self._identity}}}",
            self._restore,
        )
        if restored is not None:
            self._restore = None

    def select_pane(self, direction: Literal["left", "right"]) -> None:
        """Move outward while the application owns its tmux pane."""
        if self._pane is not None and self._restore is not None:
            self._tmux(
                "select-pane", "-t", self._pane, "-L" if direction == "left" else "-R"
            )

    def _tmux(self, *args: str) -> str | None:
        return self._run("tmux", *args)

    @staticmethod
    def _run(*args: str) -> str | None:
        try:
            return subprocess.check_output(
                args,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
                env={**os.environ, "LC_ALL": "C"},
            ).removesuffix("\n")
        except OSError, subprocess.SubprocessError:
            return None
