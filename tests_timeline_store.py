import ast
import unittest
from pathlib import Path

class TimelineStoreTests(unittest.TestCase):
    def test_timeline_constructor_accepts_store(self):
        module = ast.parse(Path('src/ui/area_status_widget.py').read_text(encoding='utf-8'))
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'AreaStatusTestApp')
        init = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
        self.assertIn('store', [arg.arg for arg in init.args.args])

    def test_main_passes_active_store(self):
        source = Path('src/ui/main_window.py').read_text(encoding='utf-8')
        self.assertIn('AreaStatusTestApp(self.area_timeline_window, store=self.area_status_store)', source)
        self.assertIn('self._close_area_timeline_window()', source)

if __name__ == '__main__':
    unittest.main()