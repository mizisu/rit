from dataclasses import dataclass
from typing import Literal

from textual.content import Content
from textual.message import Message


@dataclass
class Flash(Message):
    content: str | Content
    style: Literal["default", "warning", "success", "error"] = "default"
    duration: float | None = None


@dataclass
class SettingChanged(Message):
    key: str
    value: object
    old_value: object | None = None
