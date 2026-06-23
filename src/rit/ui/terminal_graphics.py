"""Terminal graphics transport setup."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

from textual_image._terminal import TerminalError
from textual_image.renderable import tgp

__all__ = (
    "TerminalGraphicsTransport",
    "configure_terminal_graphics",
    "detect_terminal_graphics_transport",
    "terminal_graphics_status_message",
    "wrap_tmux_passthrough_sequence",
)


_TMUX_PASSTHROUGH_PREFIX = "\x1bPtmux;"
_TMUX_PASSTHROUGH_SUFFIX = "\x1b\\"
_ORIGINAL_SEND_TGP_MESSAGE = tgp._send_tgp_message
_PATCHED = False


@dataclass(frozen=True)
class TerminalGraphicsTransport:
    """Selected terminal graphics transport."""

    name: str
    inside_tmux: bool
    passthrough_enabled: bool

    @property
    def ready(self) -> bool:
        return not self.inside_tmux or self.passthrough_enabled


def configure_terminal_graphics() -> TerminalGraphicsTransport:
    """Configure Kitty TGP output for the current terminal environment."""
    transport = detect_terminal_graphics_transport()
    _patch_tgp_sender(use_tmux_passthrough=transport.passthrough_enabled)
    return transport


@lru_cache(maxsize=1)
def detect_terminal_graphics_transport() -> TerminalGraphicsTransport:
    """Detect whether Kitty TGP needs tmux passthrough wrapping."""
    inside_tmux = bool(os.environ.get("TMUX"))
    passthrough_enabled = inside_tmux and _tmux_allow_passthrough_enabled()
    name = "kitty-tmux-passthrough" if passthrough_enabled else "kitty-direct"
    return TerminalGraphicsTransport(
        name=name,
        inside_tmux=inside_tmux,
        passthrough_enabled=passthrough_enabled,
    )


def terminal_graphics_status_message() -> str | None:
    """Return an actionable status message when inline graphics cannot work."""
    transport = detect_terminal_graphics_transport()
    if transport.ready:
        return None
    return "tmux passthrough is disabled. Add `set -g allow-passthrough on`."


def wrap_tmux_passthrough_sequence(sequence: str) -> str:
    """Wrap an escape sequence in tmux DCS passthrough syntax."""
    return (
        _TMUX_PASSTHROUGH_PREFIX
        + sequence.replace("\x1b", "\x1b\x1b")
        + _TMUX_PASSTHROUGH_SUFFIX
    )


def _patch_tgp_sender(*, use_tmux_passthrough: bool) -> None:
    global _PATCHED
    if use_tmux_passthrough:
        if not _PATCHED:
            setattr(tgp, "_send_tgp_message", _send_tgp_message_via_tmux)
            _PATCHED = True
        return

    if _PATCHED:
        setattr(tgp, "_send_tgp_message", _ORIGINAL_SEND_TGP_MESSAGE)
        _PATCHED = False


def _send_tgp_message_via_tmux(
    *,
    payload: str | None = None,
    **kwargs: int | str | None,
) -> None:
    if not sys.__stdout__:
        raise TerminalError("sys.__stdout__ is None")

    sequence = "".join(
        [
            "\x1b_G",
            ",".join(
                f"{key}={value}" for key, value in kwargs.items() if value is not None
            ),
            f";{payload}" if payload else "",
            "\x1b\\",
        ]
    )
    sys.__stdout__.write(wrap_tmux_passthrough_sequence(sequence))
    sys.__stdout__.flush()


def _tmux_allow_passthrough_enabled() -> bool:
    values = {
        _tmux_option_value(["show-options", "-pv", "allow-passthrough"]),
        _tmux_option_value(["show-options", "-gv", "allow-passthrough"]),
    }
    return any(value in {"on", "all"} for value in values)


def _tmux_option_value(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            timeout=1,
            check=True,
        )
    except FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired:
        return ""
    return result.stdout.strip()
