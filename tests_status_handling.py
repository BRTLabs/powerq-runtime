import unittest

from src.ui.status_handling import normalize_area_transitions


class StatusHandlingTests(unittest.TestCase):
    def test_normalizes_and_coalesces_last_state(self):
        self.assertEqual(
            normalize_area_transitions(
                [(" Area_1 ", 0), ("Area_2", "1"), ("Area_1", 2)]
            ),
            [("Area_1", 2), ("Area_2", 1)],
        )

    def test_rejects_invalid_state(self):
        for value in (True, -1, 3, None, "running"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_area_transitions([("Area_1", value)])

    def test_rejects_malformed_transition(self):
        payloads = (["Area_1"], [("", 1)], [("Area_1",)], [("Area_1", 1, 2)])
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_area_transitions(payload)


if __name__ == "__main__":
    unittest.main()