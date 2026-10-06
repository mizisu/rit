from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tty
from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest

from rit.services.tmux_navigation import TmuxNavigation


class TmuxServer:
    """Own a disposable server, never the developer's tmux socket."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.socket = directory / "socket"
        self.command("-f", "/dev/null", "new-session", "-d", "/bin/sh")

    def command(self, *args: str) -> str:
        result = subprocess.run(
            ["tmux", "-u", "-S", str(self.socket), *args],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return result.stdout.removesuffix("\n")

    def press(self, pane: str, key: str, ready: str) -> None:
        self.command("send-keys", "-t", pane, key)
        self.command("wait-for", ready)

    def start_capture(self) -> str:
        command = shlex.join(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                str(Path(__file__).resolve()),
                "capture",
                str(self.directory),
            ]
        )
        command += "; tmux wait-for -S capture-exited; exec /bin/sh"
        pane = self.command("new-window", "-P", "-F", "#{pane_id}", command)
        self.command("wait-for", "capture-ready")
        return pane

    def close(self) -> None:
        subprocess.run(
            ["tmux", "-S", str(self.socket), "kill-server"],
            check=False,
            capture_output=True,
            timeout=5,
        )


class Capture:
    """Run the application lifecycle on a real pane's terminal."""

    @staticmethod
    def run(directory: Path) -> None:
        previous = directory / "previous"
        if previous.exists():
            subprocess.run(
                ["tmux", "source-file", "-"],
                input=(
                    f"set-option -p -t {os.environ['TMUX_PANE']} "
                    f"@rit_nav {shlex.quote(previous.read_text())}\n"
                ),
                text=True,
                check=True,
                timeout=5,
            )
        with TmuxNavigation() as navigation:
            tty.setraw(0)
            Capture.signal("capture-ready")
            while (data := os.read(0, 1)) != b"!":
                if not data:
                    break
                if data == b"P":
                    navigation.pause()
                    Capture.signal("paused")
                elif data == b"R":
                    navigation.resume()
                    Capture.signal("resumed")
                elif data == b"E":
                    raise RuntimeError("application error")

    @staticmethod
    def signal(name: str) -> None:
        subprocess.run(["tmux", "wait-for", "-S", name], check=True, timeout=5)


@pytest.fixture
def server() -> Iterator[TmuxServer]:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    version = subprocess.run(
        ["tmux", "-V"], capture_output=True, text=True, check=True
    ).stdout
    number = version.split()[1]
    major, _, minor = number.partition(".")
    minor = "".join(character for character in minor if character.isdigit())
    if (int(major), int(minor)) < (3, 7):
        pytest.skip("tmux navigation requires tmux 3.7+")
    with tempfile.TemporaryDirectory(prefix="rit-tmux-", dir="/tmp") as directory:
        instance = TmuxServer(Path(directory))
        try:
            yield instance
        finally:
            instance.close()


def test_navigation_restores_real_pane_state(server: TmuxServer) -> None:
    server.command("set-option", "-s", "@rit-navigation-enabled", "1")
    for previous in (
        None,
        "",
        " previous marker ",
        ";",
        '"quoted" \\;\n$HOME #{pane_id}\n',
    ):
        if previous is not None:
            (server.directory / "previous").write_text(previous)
        pane = server.start_capture()
        identity = server.command("show-options", "-pqv", "-t", pane, "@rit_nav")
        assert identity
        server.press(pane, "P", "paused")
        snapshot = server.command("show-options", "-pq", "-t", pane, "@rit_nav")
        assert bool(snapshot) == (previous is not None)
        assert server.command("show-options", "-pqv", "-t", pane, "@rit_nav") == (
            previous or ""
        )
        for _ in range(2):
            server.press(pane, "R", "resumed")
        assert (
            server.command("show-options", "-pqv", "-t", pane, "@rit_nav") == identity
        )
        server.press(pane, "!", "capture-exited")
        assert server.command("show-options", "-pq", "-t", pane, "@rit_nav") == snapshot

    pane = server.start_capture()
    server.press(pane, "E", "capture-exited")
    assert server.command("show-options", "-pq", "-t", pane, "@rit_nav") == snapshot

    pane = server.start_capture()
    server.command("set-option", "-p", "-t", pane, "@rit_nav", "another owner")
    server.press(pane, "!", "capture-exited")
    assert (
        server.command("show-options", "-pqv", "-t", pane, "@rit_nav")
        == "another owner"
    )


def test_navigation_selects_neighbors_of_its_own_pane(
    server: TmuxServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = server.command("display-message", "-p", "#{pane_id}")
    window = server.command("display-message", "-p", "#{window_id}")
    right = server.command(
        "split-window", "-h", "-t", pane, "-P", "-F", "#{pane_id}", "/bin/sh"
    )
    left = server.command(
        "split-window", "-hb", "-t", pane, "-P", "-F", "#{pane_id}", "/bin/sh"
    )
    server.command("select-pane", "-t", right)
    pid = server.command("display-message", "-p", "#{pid}")
    monkeypatch.setenv("TMUX", f"{server.socket},{pid},0")
    monkeypatch.setenv("TMUX_PANE", pane)
    monkeypatch.setattr(os, "isatty", lambda _: True)
    server.command("set-option", "-s", "@rit-navigation-enabled", "1")
    with TmuxNavigation() as navigation:
        navigation.select_pane("left")
        assert server.command("display-message", "-p", "-t", window, "#{pane_id}") == left
        navigation.select_pane("right")
        assert server.command("display-message", "-p", "-t", window, "#{pane_id}") == right
        navigation.pause()
        navigation.select_pane("left")
        assert server.command("display-message", "-p", "-t", window, "#{pane_id}") == right


@pytest.mark.parametrize(("direction", "flag"), [("left", "-L"), ("right", "-R")])
def test_navigation_selects_panes_only_while_active(
    monkeypatch: pytest.MonkeyPatch,
    direction: Literal["left", "right"],
    flag: str,
) -> None:
    monkeypatch.setenv("TMUX", "test socket")
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setattr(os, "isatty", lambda _: True)
    monkeypatch.setattr(TmuxNavigation, "_run", staticmethod(lambda *_: "start time"))
    navigation = TmuxNavigation()
    command = Mock(side_effect=["1", "", "", "", ""])
    monkeypatch.setattr(navigation, "_tmux", command)

    navigation.select_pane(direction)
    command.assert_not_called()
    with navigation:
        command.reset_mock()
        navigation.select_pane(direction)
        command.assert_called_once_with("select-pane", "-t", "%7", flag)
    command.reset_mock()
    navigation.select_pane(direction)
    command.assert_not_called()


@pytest.mark.parametrize("phase", ["snapshot", "claim", "restore"])
def test_navigation_handles_uncertain_commands(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    monkeypatch.setenv("TMUX", "test socket")
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setattr(os, "isatty", lambda _: True)
    monkeypatch.setattr(TmuxNavigation, "_run", staticmethod(lambda *_: "start time"))
    responses = {
        "snapshot": ["1", None],
        "claim": ["1", "@rit_nav previous", None, ""],
        "restore": ["1", "@rit_nav previous", "", None, ""],
    }[phase]
    command = Mock(side_effect=responses)
    navigation = TmuxNavigation()
    monkeypatch.setattr(navigation, "_tmux", command)
    with navigation:
        navigation.pause()
    assert command.call_count == len(responses)
    if phase != "snapshot":
        assert command.call_args.args[-2] == (
            f"#{{==:#{{@rit_nav}},{os.getpid()} start time}}"
        )
        assert command.call_args.args[-1] == "set-option -p -t %1 @rit_nav previous\n"


@pytest.mark.parametrize("reason", ["no-tmux", "no-tty", "no-plugin"])
def test_navigation_is_opt_in(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    monkeypatch.setenv("TMUX", "test socket")
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setattr(os, "isatty", lambda _: reason != "no-tty")
    if reason == "no-tmux":
        monkeypatch.delenv("TMUX")
    command = Mock(return_value="")
    navigation = TmuxNavigation()
    monkeypatch.setattr(navigation, "_tmux", command)
    with navigation:
        navigation.select_pane("left")
        navigation.select_pane("right")
    if reason == "no-plugin":
        command.assert_called_once_with(
            "show-options", "-sqv", "@rit-navigation-enabled"
        )
    else:
        command.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(),
        subprocess.TimeoutExpired("tmux", 1),
        subprocess.CalledProcessError(1, "tmux"),
    ],
)
def test_navigation_command_failures_are_nonfatal(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    assert TmuxNavigation._run("tmux", "show-options") is None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "capture":
        Capture.run(Path(sys.argv[2]))
