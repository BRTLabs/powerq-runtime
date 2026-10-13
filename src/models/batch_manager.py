import json
from pathlib import Path


DEFAULT_BATCH_TYPES = [f"batch_{index}" for index in range(1, 10)]

BASE_DIR = Path(__file__).resolve().parent.parent.parent
BATCH_TYPES_FILE = BASE_DIR / "data" / "batch_types_data.json"


def _normalize_batch_types(raw_batch_types: list) -> list[str]:
    normalized = []
    seen = set()
    for batch_type in raw_batch_types:
        if not isinstance(batch_type, str):
            continue
        cleaned = batch_type.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized


def load_batch_types() -> list[str]:
    if BATCH_TYPES_FILE.exists():
        try:
            loaded = json.loads(BATCH_TYPES_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                batch_types = _normalize_batch_types(loaded)
                if batch_types:
                    return sorted(batch_types, key=str.lower)
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_BATCH_TYPES.copy()


def save_batch_types(batch_types: list[str]) -> None:
    BATCH_TYPES_FILE.write_text(
        json.dumps(sorted(batch_types, key=str.lower), indent=2),
        encoding="utf-8",
    )


BATCH_TYPES = load_batch_types()