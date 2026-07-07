from __future__ import annotations

from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path
import os
import smtplib
import ssl
from typing import Any

from utils.logger import write_log


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = "465"
DEFAULT_EMAIL_TO = "shitou3263063@gmail.com"
REQUIRED_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"]


def _load_env_file(env_path: Path) -> None:
    """读取 .env；不覆盖系统环境变量，方便 GitHub Secrets 接管。"""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _get_email_config(env_path: Path | None = None) -> dict[str, str] | None:
    _load_env_file(env_path or PROJECT_ROOT / ".env")

    config = {key: os.getenv(key, "").strip() for key in REQUIRED_KEYS}
    config["SMTP_HOST"] = config["SMTP_HOST"] or DEFAULT_SMTP_HOST
    config["SMTP_PORT"] = config["SMTP_PORT"] or DEFAULT_SMTP_PORT
    config["EMAIL_TO"] = config["EMAIL_TO"] or DEFAULT_EMAIL_TO

    missing = [key for key, value in config.items() if not value]
    if missing:
        return None
    return config


def _read_report(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _send_email(
    config: dict[str, str],
    subject: str,
    body: str,
    attachments: list[Path],
    html_body: str | None = None,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config["SMTP_USER"]
    msg["To"] = config["EMAIL_TO"]
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    for path in attachments:
        msg.add_attachment(
            path.read_bytes(),
            maintype="text",
            subtype="markdown",
            filename=path.name,
        )

    port = int(config["SMTP_PORT"] or DEFAULT_SMTP_PORT)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config["SMTP_HOST"], port, context=context, timeout=30) as smtp:
        smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
        smtp.send_message(msg)


def _parse_colon_lines(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "：" not in line:
            continue
        key, value = line.split("：", 1)
        result[key.strip()] = value.strip()
    return result


def _final_decision_lines(daily_content: str) -> dict[str, str]:
    marker = "【今日最终结论】"
    _, _, final_text = daily_content.partition(marker)
    return _parse_colon_lines(final_text)


def _daily_header_lines(daily_content: str) -> dict[str, str]:
    before_market, _, _ = daily_content.partition("①")
    return _parse_colon_lines(before_market)


def _stone_cio_block(daily_content: str) -> str:
    marker = "【Stone CIO 今日决策】"
    if marker not in daily_content:
        return ""
    _, _, after_marker = daily_content.partition(marker)
    block, _, _ = after_marker.partition("\n日期：")
    return (marker + "\n" + block.strip()).strip()


def _clip(value: str, limit: int = 240) -> str:
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _plain_email_body(
    send_date: date,
    today_action_content: str,
    daily_content: str,
    weekly_attached: bool,
) -> str:
    header = _daily_header_lines(daily_content)
    final = _final_decision_lines(daily_content)
    action = _parse_colon_lines(today_action_content)
    cio_block = _stone_cio_block(daily_content)

    lines = [
        "Stone CIO 今日决策",
        "目标：长期年化 10%–15%",
        "策略：进取型长期增长",
        "提示：不保证收益，仅供投资辅助。",
        "声明：仅供投资辅助，不构成投资建议。",
        "",
        _clip(cio_block, 1400) if cio_block else "今日CIO决策区块未解析，请查看附件 daily_report.md。",
        "",
        f"总资产：{header.get('总资产', '未获取')}",
        f"市场风险评分：{header.get('市场风险评分', '未获取')}",
        f"操作等级：{final.get('操作等级', '未获取')}",
        f"今日是否调仓：{final.get('今日是否调仓', action.get('今日是否再平衡', '未获取'))}",
        "",
        "今日动作",
        f"买：{_clip(final.get('建议买入', '无'))}",
        f"卖：{_clip(final.get('建议卖出', '无'))}",
        f"持有：{_clip(final.get('建议继续持有', '无'), 320)}",
        f"等待：{_clip(final.get('建议等待', '无'), 320)}",
        "",
        f"最大风险：{_clip(final.get('最大风险', action.get('今日最大风险', '未获取')), 360)}",
        f"一句话结论：{_clip(final.get('一句话结论', action.get('一句话结论', '未获取')), 360)}",
        "",
        "完整日报已作为附件发送。",
    ]
    if weekly_attached:
        lines.append("周报也已作为附件发送。")
    lines.append("声明：仅供投资辅助，不构成投资建议；系统不自动交易，不承诺收益。")
    return "\n".join(lines)


def _html_row(label: str, value: str, *, strong: bool = False) -> str:
    safe_label = escape(label)
    safe_value = escape(value or "未获取")
    font_weight = "700" if strong else "500"
    return (
        "<tr>"
        f"<td style='padding:9px 0;color:#667085;width:118px;vertical-align:top'>{safe_label}</td>"
        f"<td style='padding:9px 0;color:#101828;font-weight:{font_weight};line-height:1.55'>{safe_value}</td>"
        "</tr>"
    )


def _action_block(title: str, value: str, color: str) -> str:
    safe_title = escape(title)
    safe_value = escape(_clip(value or "无", 520))
    return (
        "<div style='border:1px solid #eaecf0;border-radius:10px;padding:12px 14px;margin:10px 0;background:#fff'>"
        f"<div style='font-size:13px;color:{color};font-weight:700;margin-bottom:6px'>{safe_title}</div>"
        f"<div style='font-size:15px;color:#101828;line-height:1.6'>{safe_value}</div>"
        "</div>"
    )


def _html_email_body(
    send_date: date,
    today_action_content: str,
    daily_content: str,
    weekly_attached: bool,
) -> str:
    header = _daily_header_lines(daily_content)
    final = _final_decision_lines(daily_content)
    action = _parse_colon_lines(today_action_content)
    cio_block = _stone_cio_block(daily_content)

    total_assets = header.get("总资产", "未获取")
    risk_score = header.get("市场风险评分", "未获取")
    operation_level = final.get("操作等级", "未获取")
    rebalance = final.get("今日是否调仓", action.get("今日是否再平衡", "未获取"))
    dca = action.get("今日是否定投", "未获取")
    conclusion = final.get("一句话结论", action.get("一句话结论", "未获取"))
    max_risk = final.get("最大风险", action.get("今日最大风险", "未获取"))

    weekly_note = "完整日报和周报已放在附件里。" if weekly_attached else "完整日报已放在附件里。"
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,'Microsoft YaHei',sans-serif;color:#101828">
    <div style="max-width:720px;margin:0 auto;padding:24px 14px">
      <div style="background:#ffffff;border:1px solid #eaecf0;border-radius:14px;overflow:hidden">
        <div style="padding:22px 22px 18px;border-bottom:1px solid #eaecf0;background:#0f172a;color:#ffffff">
          <div style="font-size:13px;opacity:.78;margin-bottom:6px">目标：长期年化 10%–15%</div>
          <div style="font-size:24px;font-weight:800;line-height:1.25">Stone CIO 今日决策</div>
          <div style="font-size:14px;opacity:.82;margin-top:8px">策略：进取型长期增长 · 提示：不保证收益，仅供投资辅助。</div>
        </div>

        <div style="padding:18px 22px">
          <div style="background:#f9fafb;border:1px solid #eaecf0;border-radius:10px;padding:13px 14px;margin-bottom:14px;color:#101828;line-height:1.65;white-space:pre-wrap">{escape(_clip(cio_block, 1400) if cio_block else "今日CIO决策区块未解析，请查看附件 daily_report.md。")}</div>
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse">
            {_html_row("总资产", total_assets, strong=True)}
            {_html_row("风险评分", risk_score, strong=True)}
            {_html_row("操作等级", operation_level, strong=True)}
            {_html_row("今日调仓", rebalance)}
            {_html_row("今日定投", dca)}
          </table>
        </div>

        <div style="padding:0 22px 8px">
          <div style="font-size:18px;font-weight:800;margin:8px 0 12px">今日操作建议</div>
          {_action_block("买", final.get("建议买入", "无"), "#067647")}
          {_action_block("卖", final.get("建议卖出", "无"), "#b42318")}
          {_action_block("继续持有", final.get("建议继续持有", "无"), "#344054")}
          {_action_block("等待", final.get("建议等待", "无"), "#b54708")}
        </div>

        <div style="padding:8px 22px 22px">
          <div style="background:#fffbeb;border:1px solid #fedf89;border-radius:10px;padding:13px 14px;margin-bottom:12px">
            <div style="font-size:13px;color:#b54708;font-weight:800;margin-bottom:5px">最大风险</div>
            <div style="font-size:15px;line-height:1.65;color:#101828">{escape(_clip(max_risk, 520))}</div>
          </div>
          <div style="background:#eff8ff;border:1px solid #b2ddff;border-radius:10px;padding:13px 14px">
            <div style="font-size:13px;color:#175cd3;font-weight:800;margin-bottom:5px">一句话结论</div>
            <div style="font-size:16px;line-height:1.65;color:#101828;font-weight:700">{escape(_clip(conclusion, 520))}</div>
          </div>
        </div>

        <div style="padding:16px 22px;border-top:1px solid #eaecf0;background:#f9fafb;color:#475467;font-size:13px;line-height:1.65">
          {escape(weekly_note)}<br>
          仅供投资辅助，不构成投资建议；系统不自动交易，不承诺收益。
        </div>
      </div>
    </div>
  </body>
</html>"""


def send_test_email(env_path: Path | None = None) -> dict[str, Any]:
    """发送一封 SMTP 测试邮件；失败只返回结果，不中断。"""
    config = _get_email_config(env_path)
    if not config:
        message = "邮件未配置，跳过发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    try:
        _send_email(
            config,
            "Stone AI 邮件测试",
            "如果你收到这封邮件，说明 Stone AI Investment Manager 邮件配置成功。",
            [],
        )
        message = f"邮件测试已发送到 {config['EMAIL_TO']}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不影响系统主流程
        message = f"邮件测试发送失败，已跳过：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": False, "message": message}


def send_daily_reports(
    reports_dir: Path | None = None,
    subject_date: date | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    """发送日报、今日行动摘要和可选周报；失败只记录日志，不中断主程序。"""
    config = _get_email_config(env_path)
    if not config:
        message = "邮件未配置，跳过发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    reports_path = reports_dir or PROJECT_ROOT / "reports"
    today_action_path = reports_path / "today_action.md"
    daily_path = reports_path / "daily_report.md"
    weekly_path = reports_path / "weekly_report.md"

    if not daily_path.exists():
        message = "daily_report.md 不存在，跳过邮件发送"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": True, "message": message}

    try:
        send_date = subject_date or date.today()
        subject = "Stone AI CIO Daily - 10%-15% Target"
        today_action_content = _read_report(today_action_path) if today_action_path.exists() else ""
        daily_content = _read_report(daily_path)

        attachments = [daily_path]
        if today_action_path.exists():
            attachments.insert(0, today_action_path)
        weekly_attached = weekly_path.exists()
        if weekly_path.exists():
            attachments.append(weekly_path)

        plain_body = _plain_email_body(send_date, today_action_content, daily_content, weekly_attached)
        html_body = _html_email_body(send_date, today_action_content, daily_content, weekly_attached)
        _send_email(config, subject, plain_body, attachments, html_body)

        message = f"邮件已发送到 {config['EMAIL_TO']}"
        write_log(message, filename="email_notifier.log")
        return {"sent": True, "skipped": False, "message": message}
    except Exception as exc:  # noqa: BLE001 - 邮件失败不能影响报告生成
        message = f"邮件发送失败，已记录错误：{exc}"
        write_log(message, filename="email_notifier.log")
        return {"sent": False, "skipped": False, "message": message}
