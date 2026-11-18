"""Shared ordering rules for persisted scan history."""
from datetime import datetime


def parse_history_timestamp(value):
    """Parse the legacy Moscow-local display timestamp; invalid values return None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None


def history_sort_key(value, order=0):
    """Sort valid timestamps newest-first; keep invalid records behind valid ones.

    ``order`` is a source-order tie breaker. Earlier sources win equal timestamps.
    """
    parsed = parse_history_timestamp(value)
    return (parsed is not None, parsed or datetime.min, -order)
