"""Area selection policy for status-graph matrix cells."""
from __future__ import annotations

import re
from collections.abc import Sequence


def _natural_key(value: str) -> tuple[object, ...]:
    """Return a key that sorts area_2 before area_10."""
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
        if part
    )


def existing_matrix_areas(available_areas: Sequence[str]) -> list[str]:
    """Return unique, non-empty persisted area IDs in natural order."""
    unique: dict[str, None] = {}
    for value in available_areas:
        area_id = str(value).strip()
        if area_id:
            unique.setdefault(area_id, None)
    return sorted(unique, key=_natural_key)


def resolve_matrix_area_id(
    *, index: int, available_areas: Sequence[str]
) -> str | None:
    """Return an existing area ID, never a generated fallback ID."""
    if index < 0:
        raise ValueError("index must be zero or greater")
    areas = existing_matrix_areas(available_areas)
    return areas[index] if index < len(areas) else None