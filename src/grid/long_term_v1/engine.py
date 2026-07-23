from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import floor
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import GridDecision, GridStatus, MarketInputs, MODE, STRATEGY_ID
from .state_store import LongTermGridStateStore


class LongTermGridEngine:
    """Purely simulated VOO/QQQ grid evaluation with explicit risk gates."""

    def __init__(self, config: Mapping[str, Any], state_store: LongTermGridStateStore) -> None:
        self.config = dict(config)
        self.state_store = state_store
        self.symbols = self.config.get("symbols") or {}
        self.budget = self.config.get("budget") or {}
        self.risk = self.config.get("risk_gates") or {}
        self.vix_config = self.config.get("vix_adjustment") or {}
        self.costs = self.config.get("transaction_costs") or {}
        self.center_config = self.config.get("reference_center") or {}

    def evaluate(self, inputs: MarketInputs, *, now: datetime | None = None) -> GridDecision:
        current = _utc(now or datetime.now(tz=timezone.utc))
        symbol = inputs.symbol.upper()
        if symbol not in {"VOO", "QQQ"} or symbol not in self.symbols:
            raise ValueError(f"unsupported long-term grid symbol: {symbol}")
        symbol_config = self.symbols[symbol]
        symbol_budget = float(symbol_config["budget_cny"])
        total_budget = float(self.budget["total_cny"])
        used_symbol = self.state_store.used_cash(symbol)
        used_total = self.state_store.used_cash()
        remaining = max(0.0, symbol_budget - used_symbol)
        used_pct = used_symbol / symbol_budget * 100.0 if symbol_budget else 0.0

        computed_center = _computed_center(inputs.previous_close, inputs.ma20)
        active_center = self.state_store.active_center(symbol)
        if not active_center and computed_center is not None:
            active_center = self.state_store.ensure_initial_center(
                symbol, computed_center, now=current
            )
        reference_center = float(active_center["center"]) if active_center else computed_center
        candidate_id = self._maybe_propose_center(
            symbol, computed_center, active_center, inputs, current
        )

        blocked = self._risk_reasons(inputs, current)
        if reference_center is None or reference_center <= 0:
            blocked.append("REFERENCE_CENTER_UNAVAILABLE")
        if inputs.usd_cny is None or inputs.usd_cny <= 0:
            blocked.append("USD_CNY_UNAVAILABLE")

        deviation = (
            (float(inputs.price) / reference_center - 1.0) * 100.0
            if inputs.price is not None and reference_center else None
        )
        base = self._decision_base(
            inputs,
            current,
            reference_center,
            deviation,
            remaining,
            used_pct,
            candidate_id,
        )
        if blocked:
            return self._build_decision(
                {
                    **base,
                    "status": GridStatus.GRID_BLOCKED,
                    "blocked_reasons": blocked,
                }
            )

        profit = self._take_profit_candidate(symbol, float(inputs.price))
        if profit:
            lot = profit["lot"]
            stage = int(profit["stage"])
            return self._build_decision(
                {
                    **base,
                    "status": GridStatus.GRID_TAKE_PROFIT_CANDIDATE,
                    "grid_level": int(lot["grid_level"]),
                    "standard_amount_cny": 0.0,
                    "adjusted_amount_cny": 0.0,
                    "amount_usd": 0.0,
                    "estimated_quantity": int(profit["quantity"]),
                    "take_profit_1": float(lot["take_profit_1"]),
                    "take_profit_2": float(lot["take_profit_2"]),
                    "lot_event_id": str(lot["event_id"]),
                    "metadata": {
                        **base["metadata"],
                        "take_profit_stage": stage,
                        "sell_position_scope": "GRID_POSITION",
                    },
                }
            )

        level = self._hit_buy_level(symbol, float(inputs.price), float(reference_center))
        if level is None:
            return self._build_decision({**base, "status": GridStatus.NO_ACTION})

        level_number = int(level["level"])
        standard = symbol_budget * float(level["budget_pct"]) / 100.0
        multiplier, evaluation_only, vix_reason = self._vix_multiplier(
            float(inputs.vix), level_number
        )
        adjusted = standard * multiplier
        budget_reasons = self._budget_reasons(
            symbol,
            adjusted,
            symbol_budget=symbol_budget,
            total_budget=total_budget,
            used_symbol=used_symbol,
            used_total=used_total,
            now=current,
        )
        if vix_reason:
            budget_reasons.append(vix_reason)
        usd_amount = adjusted / float(inputs.usd_cny)
        commission_rate = float(self.costs.get("commission_bps", 5)) / 10000.0
        slippage_rate = float(self.costs.get("slippage_bps", 5)) / 10000.0
        all_in_price = float(inputs.price) * (1.0 + commission_rate + slippage_rate)
        quantity = floor(usd_amount / all_in_price) if all_in_price > 0 else 0
        if quantity < 1:
            budget_reasons.append("INSUFFICIENT_CASH_FOR_ONE_WHOLE_SHARE")
        actual_notional_cny = quantity * float(inputs.price) * float(inputs.usd_cny)
        fees = actual_notional_cny * commission_rate
        slippage = actual_notional_cny * slippage_rate
        tp1 = float(inputs.price) * (
            1.0 + float(symbol_config["take_profit_1_pct"]) / 100.0
        )
        tp2 = float(inputs.price) * (
            1.0 + float(symbol_config["take_profit_2_pct"]) / 100.0
        )
        status = (
            GridStatus.GRID_BLOCKED
            if budget_reasons
            else GridStatus.ALLOW_EVALUATION_ONLY
            if evaluation_only
            else GridStatus.GRID_BUY_CANDIDATE
        )
        return self._build_decision(
            {
                **base,
                "status": status,
                "grid_level": level_number,
                "standard_amount_cny": standard,
                "adjusted_amount_cny": adjusted,
                "amount_usd": usd_amount,
                "estimated_quantity": quantity,
                "take_profit_1": tp1,
                "take_profit_2": tp2,
                "estimated_fees_cny": fees,
                "estimated_slippage_cny": slippage,
                "blocked_reasons": budget_reasons,
                "metadata": {
                    **base["metadata"],
                    "vix_multiplier": multiplier,
                    "level_drop_pct": float(level["drop_pct"]),
                    "configured_budget_pct": float(level["budget_pct"]),
                },
            }
        )

    def _risk_reasons(self, inputs: MarketInputs, now: datetime) -> list[str]:
        reasons: list[str] = []
        minimum_dqs = float(self.risk.get("minimum_dqs", 85))
        maximum_risk = float(self.risk.get("maximum_risk_score", 50))
        accepted = {
            str(value).upper()
            for value in self.risk.get(
                "accepted_quote_statuses", ["VALID", "REALTIME_VALID"]
            )
        }
        if inputs.dqs is None:
            reasons.append("DQS_MISSING")
        elif float(inputs.dqs) < minimum_dqs:
            reasons.append(f"DQS_BELOW_{minimum_dqs:g}")
        if inputs.risk_score is None:
            reasons.append("RISK_SCORE_MISSING")
        elif float(inputs.risk_score) > maximum_risk:
            reasons.append(f"RISK_SCORE_ABOVE_{maximum_risk:g}")
        if str(inputs.quote_status).upper() not in accepted:
            reasons.append(f"QUOTE_STATUS_{str(inputs.quote_status).upper()}_NOT_ACCEPTED")
        if inputs.quote_delay_seconds is None:
            reasons.append("QUOTE_DELAY_UNKNOWN")
        elif float(inputs.quote_delay_seconds) > float(
            self.risk.get("maximum_quote_delay_seconds", 90)
        ):
            reasons.append("QUOTE_DELAY_EXCEEDS_90_SECONDS")
        if inputs.price is None or inputs.price <= 0:
            reasons.append("QUOTE_PRICE_MISSING")
        if bool(self.risk.get("require_us_regular_session", True)) and str(
            inputs.market_session
        ).upper() != "OPEN":
            reasons.append("US_MARKET_NOT_IN_REGULAR_SESSION")
        if inputs.vix is None:
            reasons.append("VIX_MISSING")
        elif float(inputs.vix) >= float(self.risk.get("maximum_vix", 40)):
            reasons.append("VIX_AT_OR_ABOVE_40")
        if inputs.vix_time is None:
            reasons.append("VIX_TIMESTAMP_MISSING")
        elif (
            now - _utc(inputs.vix_time)
        ).total_seconds() > float(self.risk.get("vix_max_age_seconds", 300)):
            reasons.append("VIX_STALE")
        reasons.extend(str(reason) for reason in inputs.data_anomalies)
        return list(dict.fromkeys(reasons))

    def _budget_reasons(
        self,
        symbol: str,
        adjusted: float,
        *,
        symbol_budget: float,
        total_budget: float,
        used_symbol: float,
        used_total: float,
        now: datetime,
    ) -> list[str]:
        reasons: list[str] = []
        if adjusted <= 0:
            return reasons
        new_york = now.astimezone(ZoneInfo("America/New_York"))
        local_midnight = new_york.replace(hour=0, minute=0, second=0, microsecond=0)
        daily = self.state_store.simulated_buy_amount_since(symbol, local_midnight)
        rolling = self.state_store.simulated_buy_amount_since(
            symbol, _rolling_three_trading_day_start(now)
        )
        if daily + adjusted > symbol_budget * float(
            self.budget.get("daily_symbol_usage_limit_pct", 20)
        ) / 100.0:
            reasons.append("DAILY_SYMBOL_BUDGET_LIMIT_20_PERCENT")
        if rolling + adjusted > symbol_budget * float(
            self.budget.get("rolling_three_day_symbol_usage_limit_pct", 35)
        ) / 100.0:
            reasons.append("ROLLING_THREE_DAY_LIMIT_35_PERCENT")
        maximum_use = total_budget * float(
            self.budget.get("maximum_total_usage_pct", 60)
        ) / 100.0
        reserve = total_budget * float(
            self.budget.get("minimum_cash_reserve_pct", 40)
        ) / 100.0
        if used_total + adjusted > maximum_use:
            reasons.append("TOTAL_GRID_USAGE_LIMIT_60_PERCENT")
        if total_budget - used_total - adjusted < reserve:
            reasons.append("MINIMUM_GRID_CASH_RESERVE_40_PERCENT")
        if used_symbol + adjusted > symbol_budget:
            reasons.append("SYMBOL_GRID_BUDGET_EXHAUSTED")
        return reasons

    def _vix_multiplier(self, vix: float, level: int) -> tuple[float, bool, str]:
        shallow = level <= int(self.vix_config.get("shallow_level_count", 2))
        if vix < 15:
            return float(self.vix_config.get("below_15", 0.7)), False, ""
        if vix < 20:
            return float(self.vix_config.get("from_15_to_20", 1.0)), False, ""
        if vix < 30:
            key = "from_20_to_30_shallow" if shallow else "from_20_to_30_deep"
            return float(self.vix_config.get(key, 0.5 if shallow else 1.0)), False, ""
        if vix < 40:
            if shallow:
                return 0.0, False, "VIX_30_TO_40_SHALLOW_LEVEL_PAUSED"
            return float(self.vix_config.get("from_30_to_40_deep", 1.0)), True, ""
        return 0.0, False, "VIX_AT_OR_ABOVE_40"

    def _hit_buy_level(
        self, symbol: str, price: float, reference_center: float
    ) -> Mapping[str, Any] | None:
        hit = [
            level
            for level in self.symbols[symbol].get("buy_levels", [])
            if price <= reference_center * (1.0 - float(level["drop_pct"]) / 100.0)
        ]
        for level in sorted(hit, key=lambda item: int(item["level"]), reverse=True):
            if not self.state_store.active_lot(symbol, int(level["level"])):
                return level
        return None

    def _take_profit_candidate(self, symbol: str, price: float) -> dict[str, Any] | None:
        for lot in self.state_store.open_lots(symbol):
            if bool(lot["tp1_completed"]) and not bool(lot["tp2_completed"]):
                if price >= float(lot["take_profit_2"]):
                    return {
                        "lot": lot,
                        "stage": 2,
                        "quantity": int(lot["remaining_quantity"]),
                    }
            elif not bool(lot["tp1_completed"]) and price >= float(lot["take_profit_1"]):
                return {
                    "lot": lot,
                    "stage": 1,
                    "quantity": max(1, int(lot["simulated_quantity"]) // 2),
                }
        return None

    def _maybe_propose_center(
        self,
        symbol: str,
        computed_center: float | None,
        active_center: Mapping[str, Any] | None,
        inputs: MarketInputs,
        now: datetime,
    ) -> int | None:
        if computed_center is None or not active_center:
            return None
        if computed_center <= float(active_center["center"]):
            return None
        required_days = int(
            self.center_config.get("consecutive_days_above_ma20", 5)
        )
        reason = (
            "FIVE_TRADING_DAYS_ABOVE_MA20"
            if inputs.consecutive_days_above_ma20 >= required_days
            else "MONTHLY_UPWARD_REVIEW_CANDIDATE"
        )
        return self.state_store.propose_center(
            symbol, computed_center, reason=reason, now=now
        )

    def _decision_base(
        self,
        inputs: MarketInputs,
        now: datetime,
        reference_center: float | None,
        deviation: float | None,
        remaining: float,
        used_pct: float,
        candidate_id: int | None,
    ) -> dict[str, Any]:
        return {
            "event_id": str(uuid4()),
            "strategy_id": STRATEGY_ID,
            "symbol": inputs.symbol.upper(),
            "generated_at": now,
            "current_price": inputs.price,
            "source": inputs.source,
            "quote_time": inputs.quote_time,
            "quote_delay_seconds": inputs.quote_delay_seconds,
            "quote_status": inputs.quote_status,
            "market_session": inputs.market_session,
            "reference_center": reference_center,
            "center_deviation_pct": deviation,
            "grid_level": None,
            "standard_amount_cny": 0.0,
            "adjusted_amount_cny": 0.0,
            "amount_usd": 0.0,
            "estimated_quantity": 0,
            "remaining_grid_budget_cny": remaining,
            "used_budget_pct": used_pct,
            "take_profit_1": None,
            "take_profit_2": None,
            "dqs": inputs.dqs,
            "risk_score": inputs.risk_score,
            "vix": inputs.vix,
            "blocked_reasons": (),
            "estimated_fees_cny": 0.0,
            "estimated_slippage_cny": 0.0,
            "decision_inputs_hash": _inputs_hash(inputs, reference_center),
            "metadata": {
                "reference_center_candidate_id": candidate_id,
                "reference_center_change_requires_manual_confirmation": True,
                "mode": MODE,
            },
        }

    @staticmethod
    def _build_decision(payload: Mapping[str, Any]) -> GridDecision:
        kwargs = dict(payload)
        kwargs["blocked_reasons"] = tuple(
            dict.fromkeys(str(reason) for reason in kwargs.get("blocked_reasons", []))
        )
        return GridDecision(**kwargs)


def _computed_center(previous_close: float | None, ma20: float | None) -> float | None:
    values = [float(value) for value in (previous_close, ma20) if value is not None and value > 0]
    return min(values) if len(values) == 2 else None


def _inputs_hash(inputs: MarketInputs, center: float | None) -> str:
    payload = {
        "inputs": {
            key: (
                value.isoformat()
                if isinstance(value, datetime)
                else list(value)
                if isinstance(value, tuple)
                else value
            )
            for key, value in vars(inputs).items()
        },
        "active_reference_center": center,
        "strategy_id": STRATEGY_ID,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("grid evaluation time must include timezone")
    return value.astimezone(timezone.utc)


def _rolling_three_trading_day_start(value: datetime) -> datetime:
    """Return the local-midnight start of the current/previous two weekdays.

    The budget rule is expressed in trading days, not elapsed 72-hour windows;
    using New York time also keeps a weekend or DST transition from changing the
    amount counted for the rolling limit.  Exchange holidays are intentionally
    left to the caller's configured trading calendar and do not affect the
    conservative weekday fallback used here.
    """
    local = _utc(value).astimezone(ZoneInfo("America/New_York"))
    trading_days_seen = 1 if local.weekday() < 5 else 0
    cursor = local
    while trading_days_seen < 3:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            trading_days_seen += 1
    return cursor.replace(hour=0, minute=0, second=0, microsecond=0)
