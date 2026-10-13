"""Safe database selection helpers for the area-status UI."""
from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def validate_sqlite_database(path: str | Path) -> Path:
    """Validate that *path* is a readable SQLite database with user tables."""
    database_path = Path(path).expanduser().resolve()
    if not database_path.is_file():
        raise ValueError(f"Database file does not exist: {database_path}")
    try:
        with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            tables = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not tables:
                raise ValueError("The selected database contains no application tables")
    except sqlite3.DatabaseError as exc:
        raise ValueError("The selected file is not a valid SQLite database") from exc
    return database_path


def build_store_for_database(store_class: type[T], path: str | Path) -> T:
    """Construct an AreaStatusStore for a selected database path.

    Supports the common ``database_path`` keyword, a positional path, and
    legacy stores whose path is writable after construction.
    """
    database_path = validate_sqlite_database(path)
    signature = inspect.signature(store_class)

    if "database_path" in signature.parameters:
        return store_class(database_path=database_path)

    try:
        return store_class(database_path)
    except TypeError:
        store: Any = store_class()
        if not hasattr(store, "database_path"):
            raise TypeError(
                "AreaStatusStore must accept a database path or expose database_path"
            )
        close = getattr(store, "close", None)
        if callable(close):
            close()
        store.database_path = database_path
        return store