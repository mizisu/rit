import inspect
from types import ModuleType

import rit.ui.components.file_changes as file_changes_module
import rit.ui.screens.main as main_module
import rit.ui.screens.review_submit as review_submit_module


def test_ui_interaction_modules_do_not_use_runtime_casts() -> None:
    modules: tuple[ModuleType, ...] = (
        file_changes_module,
        main_module,
        review_submit_module,
    )

    for module in modules:
        assert "cast(" not in inspect.getsource(module), module.__name__
