import rit.services.gh_paginated_json as gh_paginated_json
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


def test_parse_paginated_items_whitespace_only_skips_decoder(monkeypatch) -> None:
    monkeypatch.setattr(
        gh_paginated_json.json,
        "JSONDecoder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("whitespace-only paginated JSON should not create a decoder")
        ),
    )

    assert parse_paginated_items("\n  \t") == []


def test_parse_paginated_items_reuses_single_array_page(monkeypatch) -> None:
    decoded_page = [{"login": "alice"}]

    class Decoder:
        def raw_decode(self, _result: str, index: int):
            assert index == 0
            return decoded_page, 20

    monkeypatch.setattr(gh_paginated_json.json, "JSONDecoder", Decoder)

    assert parse_paginated_items('[{"login": "alice"}]') is decoded_page


def test_parse_paginated_items_empty_array_skips_decoder(monkeypatch) -> None:
    monkeypatch.setattr(
        gh_paginated_json.json,
        "JSONDecoder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty paginated JSON array should not create a decoder")
        ),
    )

    assert parse_paginated_items(" []\n") == []
