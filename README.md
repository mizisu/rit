# rit

A terminal UI for reviewing GitHub pull requests.

| Demo 1 | Demo 2 |
| --- | --- |
| ![rit demo 1](docs/assets/demo_1.gif) | ![rit demo 2](docs/assets/demo_2.gif) |

## Requirements

- Python 3.14+
- uv
- GitHub CLI (`gh`)

## Installation

```bash
uv tool install --python 3.14 git+https://github.com/mizisu/rit.git
gh auth login
```

## Usage

```bash
rit 123
rit owner/repo#123
rit https://github.com/owner/repo/pull/123
```


