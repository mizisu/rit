"""Datetime helpers for GitHub timestamps and local placeholders."""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = (
    "DATETIME_MIN_UTC",
    "datetime_min_utc",
    "datetime_sort_key",
    "is_min_datetime",
)


DATETIME_MIN_UTC = datetime.min.replace(tzinfo=timezone.utc)


def datetime_min_utc() -> datetime:
    return DATETIME_MIN_UTC


def datetime_sort_key(dt: datetime | None) -> datetime:
    if dt is None or is_min_datetime(dt):
        return DATETIME_MIN_UTC
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_min_datetime(dt: datetime | None) -> bool:
    return dt is not None and dt.replace(tzinfo=None) == datetime.min
