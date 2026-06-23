from rit.services.gh_paginated_json import parse_paginated_items


def test_parse_paginated_items_flattens_concatenated_json_arrays() -> None:
    items = parse_paginated_items(
        """
        [{"login": "alice"}]
        [{"login": "bob"}, {"login": "carol"}]
        """
    )

    assert items == [
        {"login": "alice"},
        {"login": "bob"},
        {"login": "carol"},
    ]


def test_parse_paginated_items_keeps_non_array_json_objects() -> None:
    items = parse_paginated_items(
        """
        {"login": "alice"}
        [{"login": "bob"}]
        """
    )

    assert items == [{"login": "alice"}, {"login": "bob"}]


def test_parse_paginated_items_ignores_whitespace_only_output() -> None:
    assert parse_paginated_items("\n  \t") == []

