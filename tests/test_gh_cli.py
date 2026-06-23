import pytest

from rit.services import gh_cli
from rit.services.gh_cli import (
    GhCliError,
    gh_command,
    gh_failure_message,
    gh_missing_cli_message,
    run_gh,
)


def test_gh_command_prefixes_args_with_gh_binary() -> None:
    assert gh_command(["api", "graphql"]) == ("gh", "api", "graphql")


def test_gh_failure_message_prefers_stderr_text() -> None:
    assert gh_failure_message(["api"], "  denied\n") == "denied"


def test_gh_failure_message_falls_back_to_joined_args() -> None:
    assert (
        gh_failure_message(["api", "graphql"], "")
        == "gh command failed: api graphql"
    )


def test_gh_missing_cli_message_names_install_url() -> None:
    assert gh_missing_cli_message() == (
        "gh CLI not found. Please install GitHub CLI: https://cli.github.com/"
    )


@pytest.mark.asyncio
async def test_run_gh_wraps_missing_cli_as_gh_error(monkeypatch) -> None:
    async def missing_gh(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(gh_cli.asyncio, "create_subprocess_exec", missing_gh)

    with pytest.raises(GhCliError) as exc_info:
        await run_gh(["api"])

    assert str(exc_info.value) == gh_missing_cli_message()
