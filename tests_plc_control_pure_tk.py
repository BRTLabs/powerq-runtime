import ast
import unittest
from pathlib import Path


class PlcControlPureTkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = Path(__file__).resolve().parent / "plc_control_center" / "ui.py"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_plc_control_uses_no_ttk(self):
        imports = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
                imports.extend(alias.name for alias in node.names)
        self.assertFalse(any(name == "ttk" or name.endswith(".ttk") for name in imports))
        self.assertNotIn("ttk.", self.source)

    def test_title_has_no_jti_prefix(self):
        self.assertIn('text="PLC CONTROL CENTER"', self.source)
        self.assertNotIn('text="JTI  |  PLC CONTROL CENTER"', self.source)

    def test_plant_structure_is_a_three_column_table(self):
        self.assertIn('CanvasTable(self.left, ("Name", "Type", "Status")', self.source)

    def test_layout_is_resize_aware(self):
        self.assertIn('self.grid_rowconfigure(1, weight=1)', self.source)
        self.assertIn('self.right.grid_rowconfigure(1, weight=1)', self.source)
        self.assertIn('self.canvas.bind("<Configure>"', self.source)

    def test_main_window_integration_is_preserved(self):
        main = (Path(__file__).resolve().parent / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("from plc_control_center.ui import App as PlcControlApp", main)
        self.assertIn("PlcControlApp(win)", main)


if __name__ == "__main__":
    unittest.main()
