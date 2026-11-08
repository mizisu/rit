import os
import re
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from rit.app import RitApp

__all__ = (
    "main",
    "parse_pr_reference",
)


def parse_pr_reference(pr_ref: str) -> tuple[str | None, str | None, int]:
    """Parse PR reference in various formats.

    Supported formats:
    - 123 (PR number only, uses current repo)
    - owner/repo#123
    - https://github.com/owner/repo/pull/123
    - github.com/owner/repo/pull/123

    Returns:
        Tuple of (owner, repo, pr_number)
        owner and repo are None if only PR number is provided
    """
    # Pattern: GitHub URL - https://github.com/owner/repo/pull/123
    url_pattern = r"^(?:https?://)?github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:/.*)?$"
    match = re.match(url_pattern, pr_ref)
    if match:
        return match.group(1), match.group(2), int(match.group(3))

    # Pattern: owner/repo#123
    full_pattern = r"^([^/]+)/([^#]+)#(\d+)$"
    match = re.match(full_pattern, pr_ref)
    if match:
        return match.group(1), match.group(2), int(match.group(3))

    # Pattern: just a number
    if pr_ref.isdigit():
        return None, None, int(pr_ref)

    raise click.BadParameter(
        f"Invalid PR reference: {pr_ref}. "
        + "Use '123', 'owner/repo#123', or 'https://github.com/owner/repo/pull/123'"
    )


def create_app(*, owner: str | None, repo: str | None, pr_number: int) -> RitApp:
    """Configure terminal input before Textual reads its environment."""
    if os.environ.get("TERM_PROGRAM") == "rex":
        # Rex's extended key reports interrupt Hangul IME composition.
        os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")

        from typing import cast

        from textual._ansi_sequences import ANSI_SEQUENCES_KEYS
        from textual.keys import Keys

        cast(dict[str, object], ANSI_SEQUENCES_KEYS)["\x1b[104;5u"] = (Keys.ControlH,)

    from rit.app import RitApp

    return RitApp(owner=owner, repo=repo, pr_number=pr_number)


@click.command()
@click.argument("pr_ref")
@click.version_option()
def main(pr_ref: str) -> None:
    """Review GitHub Pull Requests in your terminal.

    PR_REF can be:
    - A PR number (e.g., 123) - uses current repo
    - Full reference (e.g., owner/repo#123)
    - GitHub URL (e.g., https://github.com/owner/repo/pull/123)
    """
    owner, repo, pr_number = parse_pr_reference(pr_ref)

    app = create_app(owner=owner, repo=repo, pr_number=pr_number)
    with app.tmux_navigation:
        app.run()


if __name__ == "__main__":
    main()
