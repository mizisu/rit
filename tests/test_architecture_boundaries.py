from __future__ import annotations

import ast
from pathlib import Path

APP_DIRS = (Path("src/rit/state"), Path("src/rit/ui"))
ALLOWED_SERVICE_IMPORTS = {("src/rit/state/store.py", "rit.services")}


def _service_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    return [
        name
        for name in imports
        if name == "rit.services" or name.startswith("rit.services.")
    ]


def test_state_and_ui_use_github_service_as_the_only_github_boundary() -> None:
    leaks = []
    for directory in APP_DIRS:
        for path in directory.rglob("*.py"):
            for module in _service_imports(path):
                if (path.as_posix(), module) not in ALLOWED_SERVICE_IMPORTS:
                    leaks.append(f"{path}:{module}")

    assert leaks == []
