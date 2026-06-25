from __future__ import annotations

import json

__all__ = ("parse_paginated_items",)


def parse_paginated_items(result: str) -> list[object]:
    """Parse concatenated JSON pages emitted by gh api --paginate."""
    index = 0
    result_length = len(result)
    while index < result_length and result[index].isspace():
        index += 1
    if index >= result_length:
        return []
    if result.startswith("[]", index):
        next_index = index + 2
        while next_index < result_length and result[next_index].isspace():
            next_index += 1
        if next_index >= result_length:
            return []

    items: list[object] = []
    decoder = json.JSONDecoder()

    while index < result_length:
        while index < result_length and result[index].isspace():
            index += 1
        if index >= result_length:
            break

        data, index = decoder.raw_decode(result, index)
        if isinstance(data, list):
            next_index = index
            while next_index < result_length and result[next_index].isspace():
                next_index += 1
            if not items and next_index >= result_length:
                return data
            items.extend(data)
        else:
            items.append(data)

    return items
