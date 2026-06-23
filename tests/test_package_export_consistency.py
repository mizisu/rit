import importlib
import pkgutil
from collections.abc import Iterator

import pytest

import rit


def iter_rit_module_names() -> Iterator[str]:
    yield "rit"
    for module_info in sorted(
        pkgutil.walk_packages(rit.__path__, prefix=f"{rit.__name__}."),
        key=lambda info: info.name,
    ):
        yield module_info.name


@pytest.mark.parametrize("module_name", iter_rit_module_names())
def test_rit_module_exports_are_explicit_and_consistent(module_name: str) -> None:
    module = importlib.import_module(module_name)

    assert hasattr(module, "__all__")
    exports = tuple(module.__all__)

    assert len(exports) == len(set(exports))
    assert exports == tuple(sorted(exports))
    for name in exports:
        assert hasattr(module, name)
