import inspect

import rit.ui.screens.branch_picker as branch_picker_module


def test_branch_picker_does_not_use_runtime_casts() -> None:
    source = inspect.getsource(branch_picker_module)

    assert "cast(" not in source
