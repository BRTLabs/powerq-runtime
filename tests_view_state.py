import ast
import unittest
from pathlib import Path

class ViewStateTests(unittest.TestCase):
    def test_timeline_accepts_shared_store(self):
        source = Path('src/ui/area_status_widget.py').read_text(encoding='utf-8')
        module = ast.parse(source)
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'AreaStatusTestApp')
        init = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
        self.assertIn('store', [argument.arg for argument in init.args.args])

if __name__ == '__main__':
    unittest.main()