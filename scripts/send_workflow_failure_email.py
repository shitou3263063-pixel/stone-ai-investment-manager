from __future__ import annotations

import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.notifier.email_notifier import send_workflow_failure_notification  # noqa: E402


def _github_run_url() -> str:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "").strip("/")
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    if not repository or not run_id:
        return ""
    return f"{server}/{repository}/actions/runs/{run_id}"


def main() -> int:
    result = send_workflow_failure_notification(
        failed_stage="python main.py",
        run_url=_github_run_url(),
    )
    print(result["message"])
    return 0 if result.get("sent") else 1


if __name__ == "__main__":
    raise SystemExit(main())
