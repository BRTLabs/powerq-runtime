# BRT Labs - Claude OT Diagnostic Copilot
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # type: ignore[assignment]

DEFAULT_MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = (
    "You are the BRT Labs PowerQ Industrial OT Copilot. "
    "You analyze deterministic Siemens S7 PLC telemetry (ISO-on-TCP DB reads) and "
    "area state transitions (0=Fault, 1=Idle, 2=Running) from SQLite historians. "
    "Provide concise, zero-hallucination root-cause diagnostics, shift handover summaries, "
    "and deterministic Python signal-mapping rules compatible with area_status_config.py."
)


def fetch_recent_transitions(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    with sqlite3.connect(path, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT area_id, state_key, changed_at FROM area_state_history "
            "ORDER BY changed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def generate_shift_diagnostic(db_path: str | Path, operator_note: str = "") -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or Anthropic is None:
        raise RuntimeError(
            "Set ANTHROPIC_API_KEY and install anthropic to enable live Claude OT diagnostics."
        )
    transitions = fetch_recent_transitions(db_path)
    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Analyze recent area state transitions (0=Fault, 1=Idle, 2=Running): {transitions}\n"
                    f"Operator context: {operator_note or 'Standard shift handover.'}\n"
                    "Produce: (1) Bottleneck/Fault Summary, (2) Upstream/Downstream Correlation, "
                    "and (3) Recommended One-Point Lesson (OPL) action items."
                ),
            }
        ],
    )
    return response.content[0].text
