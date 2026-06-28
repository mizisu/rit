# rit

A terminal UI for reviewing GitHub pull requests.

<!--
![rit demo](docs/assets/rit-demo.gif)
-->

## Usage

```bash
# authenticate with GitHub CLI
gh auth login

# open a pull request
uv run rit 123
uv run rit owner/repo#123
uv run rit https://github.com/owner/repo/pull/123
```

Or run it as a Python module:

```bash
uv run python -m rit 123
```

## Git

TODO: Add Git workflow notes.
