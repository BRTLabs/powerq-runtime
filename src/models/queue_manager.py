import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WAIT_LIST_FILE = BASE_DIR / "data" / "wait_list_data.json"

from .status_manager import load_statuses

VALID_STATUSES = set(load_statuses())

DEFAULT_WAIT_LIST = [
    {"id": "queue-1", "batch_name": "batch_1", "status": "Final Processing"},
    {"id": "queue-2", "batch_name": "batch_2", "status": "Done"},
    {"id": "queue-3", "batch_name": "batch_3", "status": "Waiting"},
]


def _normalize_wait_list(raw_wait_list: list) -> list[dict[str, str]]:
    normalized = []
    seen_ids = set()
    for item in raw_wait_list:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        batch_name = item.get("batch_name")
        status = item.get("status")
        if not isinstance(item_id, str) or not item_id.strip():
            continue
        if not isinstance(batch_name, str) or not batch_name.strip():
            continue
        if status not in VALID_STATUSES:
            continue
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        normalized.append(
            {
                "id": item_id.strip(),
                "batch_name": batch_name.strip(),
                "status": status,
            }
        )
    return normalized


def load_wait_list() -> list[dict[str, str]]:
    if WAIT_LIST_FILE.exists():
        try:
            loaded = json.loads(WAIT_LIST_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                wait_list = _normalize_wait_list(loaded)
                if wait_list:
                    return wait_list
        except (OSError, json.JSONDecodeError):
            pass
    return [item.copy() for item in DEFAULT_WAIT_LIST]


def save_wait_list(wait_list: list[dict[str, str]]) -> None:
    WAIT_LIST_FILE.write_text(json.dumps(wait_list, indent=2), encoding="utf-8")