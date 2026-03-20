from typing import Callable

from rit.state.settings_schema import SCHEMA, get_default_settings


class Settings:
    """Type-safe settings with dot-notation access (e.g., "ui.theme")."""

    def __init__(
        self,
        settings: dict | None = None,
        on_change: Callable[[str, object, object], None] | None = None,
    ) -> None:
        # Start with defaults
        self._settings = get_default_settings()

        # Merge provided settings
        if settings:
            self._deep_merge(self._settings, settings)

        self._on_change = on_change
        self._schema = SCHEMA

    def _deep_merge(self, base: dict, override: dict) -> None:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def get[T](self, key: str, expect_type: type[T]) -> T:
        value = self._get_nested(key)
        if not isinstance(value, expect_type):
            raise TypeError(
                f"Setting '{key}' has type {type(value).__name__}, "
                f"expected {expect_type.__name__}"
            )
        return value

    def get_or[T](self, key: str, default: T) -> T:
        try:
            value = self._get_nested(key)
            return value if value is not None else default  # type: ignore
        except KeyError:
            return default

    def set(self, key: str, value: object) -> None:
        old_value = self.get_or(key, None)

        parts = key.split(".")
        current = self._settings

        for part in parts[:-1]:
            if part not in current:
                raise KeyError(f"Invalid setting key: {key}")
            current = current[part]

        current[parts[-1]] = value

        if self._on_change and old_value != value:
            self._on_change(key, value, old_value)

    def _get_nested(self, key: str) -> object:
        parts = key.split(".")
        current: object = self._settings

        for part in parts:
            if not isinstance(current, dict):
                raise KeyError(f"Invalid setting key: {key}")
            if part not in current:
                raise KeyError(f"Setting not found: {key}")
            current = current[part]

        return current

    def to_dict(self) -> dict:
        import copy

        return copy.deepcopy(self._settings)

    @property
    def schema(self) -> list[dict]:
        return self._schema

    @property
    def theme(self) -> str:
        return self.get("ui.theme", str)

    @property
    def diff_mode(self) -> str:
        return self.get("ui.diff_mode", str)

    @property
    def vim_mode(self) -> bool:
        return self.get("keybindings.vim_mode", bool)

    @property
    def show_line_numbers(self) -> bool:
        return self.get("ui.show_line_numbers", bool)

    @property
    def word_diff(self) -> bool:
        return self.get("ui.word_diff", bool)

    @property
    def sidebar_width(self) -> int:
        return self.get("ui.sidebar_width", int)
