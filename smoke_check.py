"""Small, display-free release check. Run with: python smoke_check.py"""

import compileall
import tempfile
from pathlib import Path

from src.models.account_manager import load_accounts
from src.models.batch_manager import load_batch_types
from src.models.queue_manager import VALID_STATUSES, load_wait_list
from src.ui.area_status_database import validate_sqlite_database
from src.ui.matrix_widget_content import resolve_matrix_area_id


def main() -> None:
    root = Path(__file__).resolve().parent
    assert compileall.compile_dir(root, quiet=1)
    assert load_accounts()
    assert load_batch_types()
    assert load_wait_list()
    assert VALID_STATUSES
    assert resolve_matrix_area_id(index=0, available_areas=["area_10", "area_2"]) == "area_2"

    with tempfile.TemporaryDirectory() as directory:
        invalid = Path(directory) / "not-a-database.db"
        invalid.write_text("not sqlite", encoding="utf-8")
        try:
            validate_sqlite_database(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid SQLite file was accepted")

    print("Power Queue Manager v2.0 smoke check passed")


if __name__ == "__main__":
    main()