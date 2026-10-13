"""PLC signal-to-area status rules edited by an administrator in VS Code.

Rules receive normalized signal values. State keys are always:
    0 = Fault, 1 = Idle, 2 = Running

Only signals declared in SIGNALS participate in area interpretation. A source is
``(PLC name, signal name)`` and must match ``data/plc_project.json`` exactly.
"""

FAULT = 0
IDLE = 1
RUNNING = 2

RAW_0_TO_20 = {
    **{value: FAULT for value in (0,)},
    **{value: IDLE for value in range(1, 10)},
    **{value: RUNNING for value in range(10, 21)},
}

SIGNALS = {
    "conditionare_state": {
        "source": ("PLC_1", "State"),
        "map": RAW_0_TO_20,
        "unknown": None,
    },
}


def _single_signal_area(signals, signal_name):
    value = signals.get(signal_name)
    if value == RUNNING:
        return RUNNING
    if value == IDLE:
        return IDLE
    return FAULT


def area_conditionare(signals):
    return _single_signal_area(signals, "conditionare_state")


def area_cutting_and_dryer(signals):
    return FAULT


def area_bin_fill(signals):
    return FAULT


def area_mf1(signals):
    return FAULT


def area_mf2(signals):
    return FAULT


def area_dryer_station(signals):
    return FAULT


AREA_RULES = {
    "Area_Conditionare": area_conditionare,
    "Area_Cutting_and_Dryer": area_cutting_and_dryer,
    "Area_Bin_Fill": area_bin_fill,
    "Area_MF1": area_mf1,
    "Area_MF2": area_mf2,
    "Area_Dryer_Station": area_dryer_station,
}