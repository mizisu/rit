from __future__ import annotations

import tomllib
from pathlib import Path


def _pytest_options() -> dict[str, object]:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    return pyproject["tool"]["pytest"]["ini_options"]


def test_pytest_runs_the_full_suite_in_parallel_by_default() -> None:
    addopts = _pytest_options()["addopts"]
    assert "-n" in addopts
    assert "auto" in addopts


def test_pytest_parallelism_uses_workstealing_distribution() -> None:
    addopts = _pytest_options()["addopts"]
    assert "--dist" in addopts
    assert "worksteal" in addopts


def test_wait_until_helper_exists() -> None:
    conftest = Path("tests/conftest.py").read_text()
    assert "async def wait_until(" in conftest
