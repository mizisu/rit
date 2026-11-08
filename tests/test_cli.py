import json
import os
import subprocess
import sys
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from rit import cli


@pytest.mark.parametrize(
    ("terminal", "configured", "expected_setting", "disabled"),
    [
        ("rex", None, "1", True),
        ("rex", "0", "0", False),
        ("rex", "1", "1", True),
        ("ghostty", None, None, False),
        ("ghostty", "1", "1", True),
        ("tmux", None, None, False),
        (None, None, None, False),
    ],
)
def test_terminal_keyboard_policy_precedes_textual_import(
    terminal: str | None,
    configured: str | None,
    expected_setting: str | None,
    disabled: bool,
) -> None:
    env = os.environ.copy()
    env.pop("TEXTUAL_DRIVER", None)
    for name, value in (
        ("TERM_PROGRAM", terminal),
        ("TEXTUAL_DISABLE_KITTY_KEY", configured),
    ):
        env.pop(name, None)
        if value is not None:
            env[name] = value

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            r"""
import json
import os
import sys

from rit.cli import create_app

assert "textual.constants" not in sys.modules
app = create_app(owner="owner", repo="repo", pr_number=123)
from textual import constants, events
from textual._xterm_parser import XTermParser

parsed_keys = [
    [
        [event.key, event.character]
        for event in XTermParser().feed(sequence)
        if isinstance(event, events.Key)
    ]
    for sequence in ("\x1b[104;5u", "\x0c", "\x08", "\x7f", "한")
]

print(json.dumps([
    os.environ.get("TEXTUAL_DISABLE_KITTY_KEY"),
    constants.DISABLE_KITTY_KEY,
    app.owner,
    app.repo,
    app.pr_number,
    parsed_keys,
]))
""",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    assert json.loads(result.stdout) == [
        expected_setting,
        disabled,
        "owner",
        "repo",
        123,
        [
            [["ctrl+h", None]] if terminal == "rex" or not disabled else [],
            [["ctrl+l", "\x0c"]],
            [["backspace", "\x08"]],
            [["backspace", "\x7f"]],
            [["한", "한"]],
        ],
    ]


def test_cli_launches_app_through_terminal_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = Mock()
    navigation = Mock()
    app.tmux_navigation.__enter__ = navigation
    app.tmux_navigation.__exit__ = Mock(return_value=False)
    create_app = Mock(return_value=app)
    monkeypatch.setattr(cli, "create_app", create_app)

    result = CliRunner().invoke(cli.main, ["owner/repo#123"])

    assert result.exit_code == 0, result.output
    create_app.assert_called_once_with(owner="owner", repo="repo", pr_number=123)
    navigation.assert_called_once()
    app.run.assert_called_once_with()
    app.tmux_navigation.__exit__.assert_called_once_with(None, None, None)
