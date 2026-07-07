from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.services.gold_price_service import estimate_gold_bar_value


class PortfolioFormatError(ValueError):
    """持仓文件格式错误，带友好提示。"""


DEFAULT_TEMPLATE = """category,name,amount_wan,currency,quantity,unit,note
美股,VOO,0,CNY,,,标普500ETF
美股,英伟达,0,CNY,,,NVIDIA
港股,恒生科技ETF,0,CNY,,,港股科技ETF
A股,沪深300ETF,0,CNY,,,A股宽基ETF
债券,中国债券,0,CNY,,,中国债券资产
黄金,实物金条,,CNY,565,克,每日按黄金价格自动估值
黄金,黄金ETF,0,CNY,,,黄金ETF
现金,现金,0,CNY,,,现金与货币资金
"""

COLUMN_ALIASES = {
    "asset": ["Asset", "asset", "标的", "名称", "Name", "name"],
    "symbol": ["Symbol", "symbol", "代码", "Code", "code", "ticker", "Ticker"],
    "category": ["Category", "category", "类型", "资产类别"],
    "amount": [
        "Amount",
        "amount",
        "市值",
        "金额",
        "amount_cny",
        "Amount_cny",
        "amount_wan",
        "Amount_wan",
        "金额(万元)",
    ],
    "currency": ["Currency", "currency", "币种"],
    "quantity": ["Quantity", "quantity", "数量", "持仓", "克数", "grams", "weight_g", "weight_gram"],
    "unit": ["Unit", "unit", "单位"],
    "note": ["Note", "note", "notes", "Notes", "备注"],
}

REQUIRED_COLUMNS = {
    "asset": "持仓名称列，支持 Asset / asset / 标的 / 名称 / name",
    "category": "资产类别列，支持 Category / category / 类型 / 资产类别",
    "amount": "金额列，支持 Amount / amount / 市值 / 金额 / amount_cny / amount_wan",
}


def ensure_portfolio_template(path: str | Path, *, overwrite: bool = False) -> Path:
    """生成 portfolio.csv 模板；默认不覆盖用户已有数据。"""
    target = Path(path)
    if target.exists() and not overwrite:
        target = target.with_name("portfolio_template.csv")

    target.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not target.exists():
        target.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
    return target


def _normalize_header(name: str) -> str:
    return name.strip().lstrip("\ufeff")


def _resolve_columns(fieldnames: list[str] | None, path: Path) -> dict[str, str]:
    if not fieldnames:
        template_path = ensure_portfolio_template(path)
        raise PortfolioFormatError(
            "portfolio.csv 表头为空或格式错误。"
            f"已生成模板：{template_path}。请按模板填写后重新运行。"
        )

    normalized = {_normalize_header(name): name for name in fieldnames if name}
    resolved: dict[str, str] = {}
    for internal_name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                resolved[internal_name] = normalized[alias]
                break

    missing = [name for name in REQUIRED_COLUMNS if name not in resolved]
    if missing:
        template_path = ensure_portfolio_template(path)
        readable_headers = "、".join(_normalize_header(name) for name in fieldnames if name)
        missing_text = "；".join(REQUIRED_COLUMNS[name] for name in missing)
        raise PortfolioFormatError(
            "portfolio.csv 缺少必要列。"
            f"当前表头：{readable_headers or '空'}。缺少：{missing_text}。"
            f"已生成模板：{template_path}。"
        )

    return resolved


def _parse_amount(value: Any, row_number: int, source_column: str = "") -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0

    amount_is_cny = _normalize_header(source_column).lower() == "amount_cny"
    cleaned = (
        raw.replace(",", "")
        .replace("，", "")
        .replace("万元", "")
        .replace("万", "")
        .replace("元", "")
        .strip()
    )
    try:
        amount = float(cleaned)
        return amount / 10000 if amount_is_cny else amount
    except ValueError as exc:
        raise PortfolioFormatError(
            f"portfolio.csv 第 {row_number} 行金额无法识别：{raw}。请填写数字，单位默认按万元处理。"
        ) from exc


def _parse_optional_number(value: Any, row_number: int, column_name: str) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace("，", "").strip()
    try:
        return float(cleaned)
    except ValueError as exc:
        raise PortfolioFormatError(
            f"portfolio.csv 第 {row_number} 行{column_name}无法识别：{raw}。请填写数字。"
        ) from exc


def _format_grams(quantity_grams: float) -> str:
    return f"{int(quantity_grams)}克" if quantity_grams.is_integer() else f"{quantity_grams:.2f}克"


def _is_physical_gold_bar(category: str, name: str, note: str, unit: str, quantity: float | None) -> bool:
    if category != "黄金" or not quantity:
        return False
    text = f"{name} {note}".upper()
    if "ETF" in text:
        return False
    return unit in {"克", "g", "G", "gram", "GRAM", "grams", "GRAMS"} or "金条" in text


def _apply_gold_bar_valuation(item: dict[str, Any]) -> dict[str, Any]:
    if not _is_physical_gold_bar(
        item["category"],
        item["name"],
        item.get("note", ""),
        item.get("unit", ""),
        item.get("quantity"),
    ):
        return item

    quantity_grams = float(item["quantity"])
    result = estimate_gold_bar_value(quantity_grams)
    grams_text = _format_grams(quantity_grams)
    if result["ok"]:
        item["amount_wan"] = float(result["amount_wan"])
        item["valuation_status"] = "estimated"
        item["price_cny_per_gram"] = result["price_cny_per_gram"]
        item["valuation_note"] = (
            f"{grams_text}金条，按每日黄金价格自动估值为{item['amount_wan']:.2f}万元，"
            f"参考价{result['price_cny_per_gram']:.2f}元/克，来源{result['source']}。"
        )
        return item

    if float(item.get("amount_wan", 0.0) or 0.0) > 0:
        item["valuation_status"] = "manual"
        item["price_cny_per_gram"] = None
        item["valuation_note"] = (
            f"{grams_text}金条自动估值失败，保留持仓文件中的手动估值"
            f"{item['amount_wan']:.2f}万元。"
        )
        return item

    item["amount_wan"] = 0.0
    item["valuation_status"] = "unvalued"
    item["price_cny_per_gram"] = None
    item["valuation_note"] = f"{grams_text}金条，暂未估值"
    return item


def load_portfolio(path: str | Path) -> list[dict[str, Any]]:
    """读取持仓 CSV，并兼容多种表头命名。"""
    portfolio_path = Path(path)
    if not portfolio_path.exists():
        ensure_portfolio_template(portfolio_path, overwrite=True)
        raise PortfolioFormatError(
            f"portfolio.csv 不存在，已自动生成模板：{portfolio_path}。"
            "请填写真实持仓后重新运行。"
        )

    try:
        with portfolio_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            columns = _resolve_columns(reader.fieldnames, portfolio_path)
            rows: list[dict[str, Any]] = []
            for row_number, row in enumerate(reader, start=2):
                asset_column = columns.get("asset", "")
                symbol_column = columns.get("symbol", "")
                category_column = columns.get("category", "")
                amount_column = columns.get("amount", "")
                currency_column = columns.get("currency", "")
                quantity_column = columns.get("quantity", "")
                unit_column = columns.get("unit", "")
                note_column = columns.get("note", "")

                asset_name = str(row.get(asset_column, "") or "").strip()
                category = str(row.get(category_column, "") or "").strip()
                if not asset_name and not category:
                    continue
                if not asset_name:
                    raise PortfolioFormatError(f"portfolio.csv 第 {row_number} 行缺少持仓名称。")
                if not category:
                    raise PortfolioFormatError(f"portfolio.csv 第 {row_number} 行缺少资产类别。")

                item = {
                    "category": category,
                    "name": asset_name,
                    "symbol": str(row.get(symbol_column, "") or "").strip(),
                    "amount_wan": _parse_amount(row.get(amount_column, ""), row_number, amount_column),
                    "currency": str(row.get(currency_column, "CNY") or "CNY").strip(),
                    "quantity": _parse_optional_number(row.get(quantity_column, ""), row_number, "数量"),
                    "unit": str(row.get(unit_column, "") or "").strip(),
                    "note": str(row.get(note_column, "") or "").strip(),
                    "valuation_status": "manual",
                    "valuation_note": "",
                    "price_cny_per_gram": None,
                }
                rows.append(_apply_gold_bar_valuation(item))

        if not rows:
            template_path = ensure_portfolio_template(portfolio_path)
            raise PortfolioFormatError(
                f"portfolio.csv 没有有效持仓数据。已生成模板：{template_path}。"
            )
        return rows
    except csv.Error as exc:
        template_path = ensure_portfolio_template(portfolio_path)
        raise PortfolioFormatError(
            f"portfolio.csv CSV 格式错误：{exc}。已生成模板：{template_path}。"
        ) from exc
