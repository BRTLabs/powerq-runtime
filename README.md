# PowerQ Edge (`powerq-edge`)

**Deterministic Industrial Telemetry, Queue Orchestration & AI Diagnostics for Manufacturing Cells.**
Engineered by **[BRT Labs](https://brt-labs.tech)** (`brt-labs.tech`).

---

## Overview

**PowerQ** bridges headless Siemens S7 PLC telemetry pipelines with intuitive operator interfaces and LLM-assisted root-cause diagnostics. Built for high-reliability operational cells, PowerQ polls S7 endpoints over ISO-on-TCP (`RFC 1006`), evaluates deterministic state rules (`Fault` / `Idle` / `Running`), and persists transition timelines to a local SQLite WAL historian.

### Core Pillars

1. **Dual-Interface Architecture (`src/ui` & `plc_control_center`):** Run low-overhead background S7 polling threads alongside a responsive pure-Tkinter operator workstation with multi-window area zoom and live signal inspection.
2. **High-Throughput S7 Acquisition (`plc_control_center/engine.py` & `s7.py`):** Contiguous DB block read merging, automatic exponential backoff, and PROFINET/S7 subnet discovery.
3. **Deterministic State Interpreter (`plc_control_center/interpreter.py`):** Maps raw PLC byte/word/real payloads into normalized area states (`0 = Fault`, `1 = Idle`, `2 = Running`) without unsafe `eval` execution.
4. **Claude OT Diagnostic Copilot (`plc_control_center/claude_copilot.py`):** Integrates **Anthropic Claude (`claude-sonnet-4-5`)** to synthesize natural-language shift handovers, root-cause downtime reports, and validated Python state-mapping rules from SQLite telemetry snapshots.

---

## Quick Start

Requires **Python 3.10+**.

```bash
python -m pip install -r requirements.txt
python main.py
```

### Enable Claude OT Diagnostics

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

---

## Verification & Automated Test Suite

```bash
python smoke_check.py
python -m unittest discover -s . -p "tests_*.py" -v
```

---

## License

Copyright (c) 2026 **BRT Labs** (`https://brt-labs.tech`). All rights reserved.
