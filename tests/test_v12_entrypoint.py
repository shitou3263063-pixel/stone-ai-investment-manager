from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EntrypointTest(unittest.TestCase):
    def test_root_main_is_official_entrypoint(self) -> None:
        content = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("from src.pipeline.unified_pipeline import main", content)

    def test_legacy_entrypoints_are_archived(self) -> None:
        self.assertFalse((PROJECT_ROOT / "src" / "main.py").exists())
        self.assertFalse((PROJECT_ROOT / "run.py").exists())
        legacy = PROJECT_ROOT / "archive" / "legacy_entrypoints"
        self.assertTrue((legacy / "src_main_deprecated.py").exists())
        self.assertTrue((legacy / "run_deprecated.py").exists())
        self.assertIn("禁止生产运行", (legacy / "src_main_deprecated.py").read_text(encoding="utf-8"))
        self.assertIn("禁止生产运行", (legacy / "run_deprecated.py").read_text(encoding="utf-8"))

    def test_workflow_uses_root_main_and_pytest(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
        self.assertIn("python main.py", workflow)
        self.assertIn("pytest", workflow)
        self.assertNotIn("python src/main.py", workflow)


if __name__ == "__main__":
    unittest.main()
