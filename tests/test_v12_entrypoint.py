from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EntrypointTest(unittest.TestCase):
    def test_root_main_is_not_business_entrypoint(self) -> None:
        spec = importlib.util.spec_from_file_location("root_main_for_test", PROJECT_ROOT / "main.py")
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        self.assertEqual(module.main(), 2)

    def test_workflow_uses_src_main(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        self.assertIn("python src/main.py", workflow)
        self.assertIn("python scripts/check_all_services.py", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)


if __name__ == "__main__":
    unittest.main()
