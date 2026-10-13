import ast
import unittest
from pathlib import Path

class AreaZoomWindowTests(unittest.TestCase):
    def test_zoom_module_uses_no_ttk(self):
        source = Path('src/ui/area_zoom_window.py').read_text(encoding='utf-8')
        self.assertNotIn('ttk', source)
        ast.parse(source)

    def test_old_embedded_zoom_logic_removed(self):
        source = Path('src/ui/main_window.py').read_text(encoding='utf-8')
        self.assertNotIn('_render_area_signal_view', source)
        self.assertNotIn('_back_to_area_matrix', source)
        self.assertIn('AreaZoomWindow(', source)

if __name__ == '__main__':
    unittest.main()