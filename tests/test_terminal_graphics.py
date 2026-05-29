"""Tests for terminal graphics transport setup."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rit.ui import terminal_graphics


@dataclass
class _CompletedProcess:
    stdout: str


@pytest.fixture(autouse=True)
def clear_transport_cache():
    terminal_graphics.detect_terminal_graphics_transport.cache_clear()
    yield
    terminal_graphics.detect_terminal_graphics_transport.cache_clear()
    terminal_graphics._patch_tgp_sender(use_tmux_passthrough=False)


class _FakeStdout:
    def __init__(self) -> None:
        self.value = ""
        self.flushed = False

    def write(self, text: str) -> None:
        self.value += text

    def flush(self) -> None:
        self.flushed = True


def test_wrap_tmux_passthrough_sequence_doubles_escapes() -> None:
    sequence = "\x1b_Ga=p;payload\x1b\\"

    wrapped = terminal_graphics.wrap_tmux_passthrough_sequence(sequence)

    assert wrapped == "\x1bPtmux;\x1b\x1b_Ga=p;payload\x1b\x1b\\\x1b\\"


def test_detect_tmux_passthrough_enabled(monkeypatch) -> None:
    terminal_graphics.detect_terminal_graphics_transport.cache_clear()
    monkeypatch.setenv("TMUX", "/tmp/tmux-socket")
    monkeypatch.setattr(
        terminal_graphics,
        "_tmux_option_value",
        lambda args: "on" if "-gv" in args else "",
    )

    transport = terminal_graphics.detect_terminal_graphics_transport()

    assert transport.name == "kitty-tmux-passthrough"
    assert transport.inside_tmux is True
    assert transport.passthrough_enabled is True
    assert transport.ready is True


def test_tmux_without_passthrough_reports_actionable_status(monkeypatch) -> None:
    terminal_graphics.detect_terminal_graphics_transport.cache_clear()
    monkeypatch.setenv("TMUX", "/tmp/tmux-socket")
    monkeypatch.setattr(terminal_graphics, "_tmux_option_value", lambda args: "off")

    assert terminal_graphics.terminal_graphics_status_message() == (
        "tmux passthrough is disabled. Add `set -g allow-passthrough on`."
    )


def test_tgp_message_sender_uses_tmux_passthrough(monkeypatch) -> None:
    fake_stdout = _FakeStdout()
    monkeypatch.setattr(terminal_graphics.sys, "__stdout__", fake_stdout)

    terminal_graphics._send_tgp_message_via_tmux(payload="abc", a="p", q=2)

    assert fake_stdout.value == "\x1bPtmux;\x1b\x1b_Ga=p,q=2;abc\x1b\x1b\\\x1b\\"
    assert fake_stdout.flushed is True
