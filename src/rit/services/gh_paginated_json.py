from __future__ import annotations

import json

__all__ = ("parse_paginated_items",)


def parse_paginated_items(result: str) -> list[object]:
    """Parse concatenated JSON pages emitted by gh api --paginate."""
    items: list[object] = []
    decoder = json.JSONDecoder()
    index = 0

    while index < len(result):
        while index < len(result) and result[index].isspace():
            index += 1
        if index >= len(result):
            break

        data, index = decoder.raw_decode(result, index)
        if isinstance(data, list):
            items.extend(data)
        else:
            items.append(data)

    return items
