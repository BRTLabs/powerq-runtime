import unittest
from src.ui.signal_status import canonical_area_id, raw_value_to_state

class SignalStatusTests(unittest.TestCase):
    def test_area_id(self):
        self.assertEqual(canonical_area_id("Cutting and Dryer"), "Area_Cutting_and_Dryer")
        self.assertEqual(canonical_area_id("Area_MF1"), "Area_MF1")

    def test_raw_state_mapping(self):
        self.assertEqual(raw_value_to_state(0), 0)
        self.assertEqual(raw_value_to_state(9), 1)
        self.assertEqual(raw_value_to_state("10"), 2)
        self.assertIsNone(raw_value_to_state(True))
        self.assertIsNone(raw_value_to_state(21))

if __name__ == "__main__":
    unittest.main()