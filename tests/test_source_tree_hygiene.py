from pathlib import Path


LOCAL_ARTIFACT_PATTERNS = {
    ".DS_Store",
    "*.old",
    "*.bak",
    "*.orig",
    "*.rej",
    "*~",
}


def _project_files(repo_root: Path, *, ignored_dirs: set[str]):
    for parent, dir_names, file_names in repo_root.walk():
        dir_names[:] = [name for name in dir_names if name not in ignored_dirs]
        for file_name in file_names:
            yield parent / file_name


def test_project_tree_does_not_contain_local_artifact_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    backup_suffixes = (".old", ".bak", ".orig", ".rej")
    ignored_dirs = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
    }

    artifacts = sorted(
        path.relative_to(repo_root)
        for path in _project_files(repo_root, ignored_dirs=ignored_dirs)
        if (
            path.name == ".DS_Store"
            or path.name.endswith("~")
            or any(path.name.endswith(suffix) for suffix in backup_suffixes)
        )
    )

    assert artifacts == []


def test_gitignore_blocks_local_artifact_patterns() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    patterns = {
        line.strip()
        for line in (repo_root / ".gitignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert LOCAL_ARTIFACT_PATTERNS <= patterns
