from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.notifier.email_notifier import diagnose_email_connection, send_test_email  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    diagnostic = diagnose_email_connection()
    print("Stone AI 邮件链路诊断（不显示凭证）")
    print(
        f"配置：{diagnostic.get('smtp_host', '未配置')}:{diagnostic.get('smtp_port', '未配置')} / "
        f"{diagnostic.get('security', '未配置')}"
    )
    for stage, detail in diagnostic.get("stages", {}).items():
        suffix = f" - {detail.get('error_category')}: {detail.get('summary')}" if detail.get("status") == "failed" else ""
        print(f"- {stage}: {detail.get('status')}{suffix}")
    if diagnostic.get("status") != "passed":
        print(
            f"诊断停止于 {diagnostic.get('error_stage', 'configuration')}："
            f"{diagnostic.get('error_category', 'UNKNOWN_ERROR')} - {diagnostic.get('error_summary', '')}"
        )
        return 0

    result = send_test_email()
    print(result["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
