"""Load trusted Python area rules and persist interpreted state transitions."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "area_status_config.py"
VALID_STATES = frozenset((0, 1, 2))


@dataclass(frozen=True, slots=True)
class SignalRule:
    alias: str
    source: tuple[str, str]
    value_map: Mapping[Any, int | None]
    unknown: int | None


@dataclass(frozen=True, slots=True)
class AreaConfig:
    path: Path
    signals: dict[str, SignalRule]
    area_rules: dict[str, Any]
    module: ModuleType


_cached_config: AreaConfig | None = None
_cached_mtime_ns: int | None = None


def _load_module(path: Path) -> ModuleType:
    if not path.exists():
        raise FileNotFoundError(f"Area status configuration not found: {path}")
    module_name = "powerq_area_status_config"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load area status configuration: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def validate_config(config: AreaConfig | ModuleType) -> AreaConfig:
    if isinstance(config, AreaConfig):
        return config
    module = config
    raw_signals = getattr(module, "SIGNALS", None)
    raw_areas = getattr(module, "AREA_RULES", None)
    if not isinstance(raw_signals, dict):
        raise ValueError("SIGNALS must be a dictionary")
    if not isinstance(raw_areas, dict) or not raw_areas:
        raise ValueError("AREA_RULES must be a non-empty dictionary")

    signals: dict[str, SignalRule] = {}
    sources: set[tuple[str, str]] = set()
    for alias, declaration in raw_signals.items():
        if not isinstance(alias, str) or not alias.isidentifier():
            raise ValueError(f"Invalid signal alias: {alias!r}")
        if not isinstance(declaration, dict):
            raise ValueError(f"{alias}: declaration must be a dictionary")
        source = declaration.get("source")
        if not isinstance(source, tuple) or len(source) != 2 or not all(isinstance(x, str) and x.strip() for x in source):
            raise ValueError(f"{alias}: source must be ('PLC name', 'signal name')")
        source = (source[0].strip(), source[1].strip())
        if source in sources:
            raise ValueError(f"Source {source!r} is declared more than once")
        sources.add(source)
        value_map = declaration.get("map")
        if not isinstance(value_map, dict) or not value_map:
            raise ValueError(f"{alias}: map must be a non-empty dictionary")
        for raw_value, state in value_map.items():
            if state is not None and state not in VALID_STATES:
                raise ValueError(f"{alias}: map[{raw_value!r}] must be 0, 1, 2, or None")
        unknown = declaration.get("unknown", None)
        if unknown is not None and unknown not in VALID_STATES:
            raise ValueError(f"{alias}: unknown must be 0, 1, 2, or None")
        signals[alias] = SignalRule(alias, source, dict(value_map), unknown)

    area_rules: dict[str, Any] = {}
    probe = {alias: rule.unknown for alias, rule in signals.items()}
    for area_id, rule in raw_areas.items():
        if not isinstance(area_id, str) or not area_id.strip():
            raise ValueError("Every AREA_RULES key must be a non-empty area ID")
        if not callable(rule):
            raise ValueError(f"{area_id}: rule must be callable")
        result = rule(dict(probe))
        if result not in VALID_STATES:
            raise ValueError(f"{area_id}: rule returned {result!r}; expected 0, 1, or 2")
        area_rules[area_id.strip()] = rule

    return AreaConfig(CONFIG_PATH, signals, area_rules, module)


def load_config(path: str | Path = CONFIG_PATH, *, force: bool = False) -> AreaConfig:
    global _cached_config, _cached_mtime_ns
    path = Path(path).resolve()
    mtime_ns = path.stat().st_mtime_ns if path.exists() else None
    if not force and path == CONFIG_PATH.resolve() and _cached_config is not None and _cached_mtime_ns == mtime_ns:
        return _cached_config
    loaded = validate_config(_load_module(path))
    loaded = AreaConfig(path, loaded.signals, loaded.area_rules, loaded.module)
    if path == CONFIG_PATH.resolve():
        _cached_config = loaded
        _cached_mtime_ns = mtime_ns
    return loaded


def save_config(*_args, **_kwargs):
    raise RuntimeError(f"Edit the trusted Python configuration directly in VS Code: {CONFIG_PATH}")


def _typed_lookup(value_map: Mapping[Any, int | None], raw_value: Any, unknown: int | None) -> int | None:
    """Lookup without conflating bool with integer 0/1."""
    for candidate, state in value_map.items():
        if type(candidate) is type(raw_value) and candidate == raw_value:
            return state
    return unknown


def normalize_signals(config: AreaConfig, raw_signal_values: Mapping[tuple[str, str], Any]) -> dict[str, int | None]:
    normalized: dict[str, int | None] = {}
    for alias, rule in config.signals.items():
        raw = raw_signal_values.get(rule.source, object())
        normalized[alias] = rule.unknown if type(raw) is object else _typed_lookup(rule.value_map, raw, rule.unknown)
    return normalized


def evaluate_areas(config: AreaConfig, raw_signal_values: Mapping[tuple[str, str], Any]) -> dict[str, int]:
    signals = normalize_signals(config, raw_signal_values)
    result: dict[str, int] = {}
    for area_id, rule in config.area_rules.items():
        state = rule(dict(signals))
        if state not in VALID_STATES:
            raise ValueError(f"{area_id}: rule returned {state!r}; expected 0, 1, or 2")
        result[area_id] = int(state)
    return result


def persist_area_states(database_path, states, cache):
    now = time.time()
    changes = []
    with sqlite3.connect(database_path, timeout=10) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS area_state_history (id INTEGER PRIMARY KEY, area_id TEXT NOT NULL, state_key INTEGER NOT NULL, changed_at REAL NOT NULL)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_area_state_history_area_time ON area_state_history(area_id, changed_at, id)")
        connection.execute("CREATE TABLE IF NOT EXISTS areas (area_id TEXT PRIMARY KEY, created_at REAL NOT NULL)")
        for area_id, state in states.items():
            connection.execute("INSERT OR IGNORE INTO areas(area_id, created_at) VALUES(?, ?)", (area_id, now))
            if cache.get(area_id) != state:
                connection.execute("INSERT INTO area_state_history(area_id,state_key,changed_at) VALUES(?,?,?)", (area_id, state, now))
                cache[area_id] = state
                changes.append((area_id, state))
    return changes