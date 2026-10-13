import json
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATUS_FILE = BASE_DIR / "data" / "batch_statuses.json"
DEFAULT_STATUSES = ["Waiting", "Conditioning", "Final Conditioning", "Processing", "Final Processing", "Done"]
def load_statuses() -> list[str]:
    try:
        raw = json.loads(STATUS_FILE.read_text(encoding="utf-8")); clean = []
        for value in raw:
            if isinstance(value, str) and value.strip() and value.strip() not in clean: clean.append(value.strip())
        return clean or DEFAULT_STATUSES.copy()
    except (OSError, json.JSONDecodeError, TypeError): return DEFAULT_STATUSES.copy()
def save_statuses(statuses: list[str]) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True); temporary = STATUS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(statuses, indent=2), encoding="utf-8"); temporary.replace(STATUS_FILE)