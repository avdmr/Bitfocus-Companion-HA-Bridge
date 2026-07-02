"""Compatibility helpers for Home Assistant config subentries."""

from __future__ import annotations

from typing import Any


def iter_config_subentries(entry: Any) -> list[Any]:
    """Return config subentries as a list across Home Assistant versions.

    Some Home Assistant versions expose ``entry.subentries`` as a dict-like
    object, while others expose an iterable collection. The POC should not fail
    setup just because that container shape changes.
    """
    subentries = getattr(entry, "subentries", None)
    if subentries is None:
        return []
    if hasattr(subentries, "values"):
        return list(subentries.values())
    try:
        return list(subentries)
    except TypeError:
        return []
