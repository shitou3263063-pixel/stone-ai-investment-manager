from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EntrypointTest(unittest.TestCase):
    def test_root_main_is_official_entrypoint(self) -> None:
        content = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("from src.app import main", content)

    def test_legacy_entrypoints_are_archived(self) -> None:
        self.assertFalse((PROJECT_ROOT / "src" / "main.py").exists())
        self.assertFalse((PROJECT_ROOT / "run.py").exists())
        self.assertTrue((PROJECT_ROOT / "archive" / "src_main_legacy.py").exists())
        self.assertTrue((PROJECT_ROOT / "archive" / "run_legacy.py").exists())

    def test_workflow_uses_root_main_and_pytest(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        self.assertIn("python main.py", workflow)
        self.assertIn("pytest", workflow)
        self.assertNotIn("python src/main.py", workflow)


if __name__ == "__main__":
    unittest.main()
