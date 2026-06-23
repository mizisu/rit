from rit.state.settings import Settings


def test_get_or_return_type_does_not_overpromise_default_type() -> None:
    assert Settings.get_or.__annotations__["return"] is object


def test_get_or_returns_stored_value_or_default() -> None:
    settings = Settings({"ui": {"theme": "dracula"}})

    assert settings.get_or("ui.theme", "default") == "dracula"
    assert settings.get_or("ui.missing", "default") == "default"
