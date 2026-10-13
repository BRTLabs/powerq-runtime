from __future__ import annotations
from dataclasses import asdict, dataclass, field
from uuid import uuid4

def uid(): return str(uuid4())

@dataclass(slots=True)
class Signal:
    name: str = 'State'; db: int = 1; byte: int = 0; type: str = 'BYTE'; bit: int = 0; unit: str = ''; area_state: bool = False; id: str = field(default_factory=uid)

@dataclass(slots=True)
class Plc:
    name: str = 'PLC'; ip: str = ''; rack: int = 0; slot: int = 1; poll: float = 1.0; enabled: bool = True; signals: list[Signal] = field(default_factory=list); id: str = field(default_factory=uid)

@dataclass(slots=True)
class Area:
    name: str = 'Area 1'; plcs: list[Plc] = field(default_factory=list); id: str = field(default_factory=uid)

@dataclass(slots=True)
class Project:
    name: str = 'PLC Control Center'; output_folder: str = ''; dataset: str = 'plc_data'; areas: list[Area] = field(default_factory=lambda:[Area()])
    def dict(self): return asdict(self)