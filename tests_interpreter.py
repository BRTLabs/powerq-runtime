import sqlite3
import tempfile
import unittest
from pathlib import Path

from plc_control_center.interpreter import evaluate_areas, load_config, normalize_signals, persist_area_states


class InterpreterTests(unittest.TestCase):
    def test_python_config_and_per_signal_map(self):
        config = load_config(force=True)
        normalized = normalize_signals(config, {("PLC_1", "State"): 12})
        self.assertEqual(normalized["conditionare_state"], 2)
        self.assertEqual(evaluate_areas(config, {("PLC_1", "State"): 12})["Area_Conditionare"], 2)
        self.assertEqual(evaluate_areas(config, {("PLC_1", "State"): 3})["Area_Conditionare"], 1)
        self.assertEqual(evaluate_areas(config, {("PLC_1", "State"): 0})["Area_Conditionare"], 0)

    def test_missing_signal_is_handled_by_area_rule(self):
        config = load_config(force=True)
        self.assertEqual(evaluate_areas(config, {})["Area_Conditionare"], 0)

    def test_transition_only_persistence_and_area_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "states.db"
            cache = {}
            self.assertEqual(persist_area_states(db, {"Area_A": 1}, cache), [("Area_A", 1)])
            self.assertEqual(persist_area_states(db, {"Area_A": 1}, cache), [])
            self.assertEqual(persist_area_states(db, {"Area_A": 2}, cache), [("Area_A", 2)])
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM area_state_history").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT area_id FROM areas").fetchone()[0], "Area_A")


if __name__ == "__main__":
    unittest.main()