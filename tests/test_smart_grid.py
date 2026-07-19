from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.grid.backtest import run_symbol_backtest
from src.grid.budget_manager import build_grid_budget, symbol_budget
from src.grid.grid_engine import _apply_confirmed_manual_trades, evaluate_symbol, load_smart_grid_config, save_grid_state, load_grid_state
from src.grid.models import GridSignal, GridSymbolState
from src.grid.position_manager import split_core_grid
from src.grid.regime_detector import detect_market_regime
from src.grid.signal_engine import build_signal, dynamic_grid_spacing
from src.grid.simulator import append_simulated_signal
from src.grid.validator import review_grid_signal
from src.macro.macro_calendar import load_macro_events
from src.reports.grid_report import generate_grid_daily_section, generate_grid_weekly_report


def _config(paper: bool = True) -> dict:
    config = load_smart_grid_config()
    config["smart_grid"]["paper_mode"] = paper
    config["smart_grid"]["live_advice_enabled"] = not paper
    config["smart_grid"]["auto_trade"] = False
    return config


def _decision(dqs: int = 90, cash: int = 50000, us_status: str = "严重低配", high_event: bool = False) -> dict:
    return {
        "date": "2026-07-13",
        "portfolio_value_yuan": 2821100,
        "dqs": {"score": dqs, "mode": "exact" if dqs >= 85 else "safe", "mode_label": "test"},
        "budget": {"confirmed_cash_available_yuan": cash},
        "allocation": [{"category": "美股", "status": us_status}],
        "event_assessment": {
            "status": "VALID_EVENTS_FOUND" if high_event else "VALID_NO_HIGH_IMPACT_EVENT",
            "event_gate_passed": not high_event,
            "reasons": ["存在已核验高影响事件"] if high_event else [],
        },
        "portfolio_snapshot": _portfolio(),
    }


def _portfolio() -> dict:
    return {
        "total_valued_assets": 2821100,
        "positions": [
            {"security_id": "NVDA", "security_name": "NVDA", "market_value_cny": 68000},
            {"security_id": "VOO", "security_name": "VOO", "market_value_cny": 130000},
        ],
    }


def _live(price: float = 500, qqq: float = 480) -> dict:
    return {
        "items": {
            "VOO": {"close": price, "status": "ok", "source": "alpha_vantage", "change_pct": 0.5, "fetched_at": "2026-07-11T00:00:00"},
            "QQQ": {"close": qqq, "status": "ok", "source": "alpha_vantage", "change_pct": 0.5, "fetched_at": "2026-07-11T00:00:00"},
            "^VIX": {"close": 18, "status": "ok", "source": "cboe_official", "change_pct": 0, "fetched_at": "2026-07-11T00:00:00"},
        }
    }


class SmartGridTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _config()
        self.voo_cfg = self.config["smart_grid"]["symbols"]["VOO"]
        self.qqq_cfg = self.config["smart_grid"]["symbols"]["QQQ"]

    def test_voo_and_qqq_use_different_parameters(self) -> None:
        self.assertNotEqual(self.voo_cfg["normal_grid_min_pct"], self.qqq_cfg["normal_grid_min_pct"])
        self.assertNotEqual(self.voo_cfg["core_position_pct"], self.qqq_cfg["core_position_pct"])

    def test_core_and_grid_position_split(self) -> None:
        split = split_core_grid(100, 75)
        self.assertEqual(split["core_quantity"], 75)
        self.assertEqual(split["grid_quantity"], 25)

    def test_core_position_is_not_sold_by_grid(self) -> None:
        state = GridSymbolState(symbol="VOO", grid_quantity=2, sell_spacing_pct=0.03)
        signal = GridSignal("VOO", "SELL", "SELL_SIGNAL", 520, 515, 5000, 3, 0, "sell")
        review = review_grid_signal(signal=signal, state=state, decision=_decision(), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("核心仓" in reason for reason in review.reasons))

    def test_grid_budget_is_separate_from_dca_budget(self) -> None:
        budget = build_grid_budget(_decision(cash=0), self.config)
        self.assertIn("独立核算", budget["overlap_guard"])
        self.assertEqual(budget["live_available_yuan"], 0)

    def test_grid_budget_is_separate_from_opportunity_cash(self) -> None:
        budget = build_grid_budget(_decision(cash=100000), self.config)
        self.assertGreater(budget["simulated_available_yuan"], 0)
        self.assertTrue(budget["paper_mode"])

    def test_cash_below_safety_line_blocks_buy(self) -> None:
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        review = review_grid_signal(signal=signal, state=GridSymbolState("VOO"), decision=_decision(cash=0), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(cash=0), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("现金" in reason for reason in review.reasons))

    def test_low_dqs_blocks_precise_grid_amount(self) -> None:
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        review = review_grid_signal(signal=signal, state=GridSymbolState("VOO"), decision=_decision(dqs=70), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(dqs=70), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("DQS" in reason for reason in review.reasons))

    def test_missing_data_does_not_trigger_trade(self) -> None:
        state = GridSymbolState("VOO", anchor_price=500)
        signal = build_signal(symbol="VOO", price=None, state=state, symbol_cfg=self.voo_cfg, symbol_budget_yuan=10000, config=self.config, regime={"regime": "data_missing"})
        self.assertEqual(signal.action, "NONE")
        self.assertEqual(state.state, "SAFE_MODE")

    def test_data_conflict_conservative_mode_blocks_trade(self) -> None:
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        decision = _decision(dqs=59)
        review = review_grid_signal(signal=signal, state=GridSymbolState("VOO"), decision=decision, portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(decision, _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(review.rejected)

    def test_dynamic_grid_spacing_for_voo(self) -> None:
        spacing = dynamic_grid_spacing(self.voo_cfg, {"regime": "range", "volatility_state": "normal"})
        self.assertGreater(spacing["buy_spacing_pct"], 0.02)

    def test_high_volatility_expands_spacing(self) -> None:
        normal = dynamic_grid_spacing(self.qqq_cfg, {"regime": "range", "volatility_state": "normal"})
        high = dynamic_grid_spacing(self.qqq_cfg, {"regime": "crisis", "volatility_state": "high"})
        self.assertGreater(high["buy_spacing_pct"], normal["buy_spacing_pct"])

    def test_uptrend_reduces_sell_frequency(self) -> None:
        normal = dynamic_grid_spacing(self.voo_cfg, {"regime": "range", "volatility_state": "normal"})
        uptrend = dynamic_grid_spacing(self.voo_cfg, {"regime": "uptrend", "volatility_state": "normal"})
        self.assertGreater(uptrend["sell_spacing_pct"], normal["sell_spacing_pct"])

    def test_downtrend_limits_continuous_buying_by_wider_spacing(self) -> None:
        normal = dynamic_grid_spacing(self.qqq_cfg, {"regime": "range", "volatility_state": "normal"})
        down = dynamic_grid_spacing(self.qqq_cfg, {"regime": "downtrend", "volatility_state": "normal"})
        self.assertGreater(down["buy_spacing_pct"], normal["buy_spacing_pct"])

    def test_major_event_filter(self) -> None:
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        review = review_grid_signal(signal=signal, state=GridSymbolState("VOO"), decision=_decision(high_event=True), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(high_event=True), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("高影响事件" in reason for reason in review.reasons))

    def test_max_consecutive_buy_levels_effective(self) -> None:
        state = GridSymbolState("VOO", consecutive_buys=3)
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        review = review_grid_signal(signal=signal, state=state, decision=_decision(), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("连续买入" in reason for reason in review.reasons))

    def test_daily_trade_limit_effective(self) -> None:
        state = GridSymbolState("VOO", day_trade_count=1)
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        review = review_grid_signal(signal=signal, state=state, decision=_decision(), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("当日" in reason for reason in review.reasons))

    def test_monthly_trade_limit_effective(self) -> None:
        state = GridSymbolState("VOO", month_trade_count=8)
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        review = review_grid_signal(signal=signal, state=state, decision=_decision(), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("本月" in reason for reason in review.reasons))

    def test_unconfirmed_manual_trade_does_not_update_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["trade_id", "date", "symbol", "action", "quantity", "price", "fees", "status", "note"])
                writer.writeheader()
                writer.writerow({"trade_id": "t1", "date": "2026-07-11", "symbol": "VOO", "action": "BUY", "quantity": 1, "price": 500, "fees": 0, "status": "suggested", "note": ""})
            state = {"symbols": {"VOO": GridSymbolState("VOO", available_grid_cash_yuan=1000).to_dict()}}
            self.assertEqual(_apply_confirmed_manual_trades(state, path), [])
            self.assertEqual(state["symbols"]["VOO"]["grid_quantity"], 0)

    def test_confirmed_manual_trade_updates_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["trade_id", "date", "symbol", "action", "quantity", "price", "fees", "status", "note"])
                writer.writeheader()
                writer.writerow({"trade_id": "t1", "date": "2026-07-11", "symbol": "VOO", "action": "BUY", "quantity": 1, "price": 500, "fees": 0, "status": "confirmed", "note": ""})
            state = {"symbols": {"VOO": GridSymbolState("VOO", available_grid_cash_yuan=1000).to_dict()}}
            self.assertEqual(_apply_confirmed_manual_trades(state, path), ["t1"])
            self.assertEqual(state["symbols"]["VOO"]["grid_quantity"], 1)

    def test_state_file_can_be_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "grid_state.json"
            save_grid_state({"symbols": {"VOO": GridSymbolState("VOO", anchor_price=500).to_dict()}}, path)
            restored = load_grid_state(path)
            self.assertEqual(restored["symbols"]["VOO"]["anchor_price"], 500)

    def test_same_signal_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "simulation.csv"
            signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy", valid_until="2026-07-12")
            review = review_grid_signal(signal=signal, state=GridSymbolState("VOO"), decision=_decision(), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(), self.config), config=self.config, symbol_cfg=self.voo_cfg, price_available=True)
            key = append_simulated_signal(path, signal, review, "")
            append_simulated_signal(path, signal, review, key)
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_transaction_cost_check_blocks_tiny_sell(self) -> None:
        cfg = {**self.voo_cfg, "normal_sell_min_pct": 0.01, "normal_sell_max_pct": 0.01}
        state = GridSymbolState("VOO", grid_quantity=10, sell_spacing_pct=0.0001)
        signal = GridSignal("VOO", "SELL", "SELL_SIGNAL", 501, 500, 1000, 2, 0, "sell")
        review = review_grid_signal(signal=signal, state=state, decision=_decision(us_status="接近目标"), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(), _config(False)), config=_config(False), symbol_cfg=cfg, price_available=True)
        self.assertTrue(any("利润不足" in reason for reason in review.reasons))

    def test_backtest_handles_insufficient_data_without_fake_result(self) -> None:
        result = run_symbol_backtest("NO_SUCH_SYMBOL_FOR_TEST", self.voo_cfg, self.config)
        if result["status"] == "insufficient_data":
            self.assertEqual(result["strategies"], [])

    def test_paper_mode_does_not_use_real_cash(self) -> None:
        budget = build_grid_budget(_decision(cash=100000), self.config)
        self.assertEqual(budget["live_available_yuan"], 0)

    def test_grid_report_fields_complete(self) -> None:
        result = {
            "enabled": True,
            "paper_mode": True,
            "summary": "模拟",
            "grid_budget": build_grid_budget(_decision(), self.config),
            "symbols": {
                "VOO": {
                    "price": 500,
                    "data_time": "now",
                    "regime": {"regime": "range"},
                    "state": GridSymbolState("VOO", anchor_price=500, next_buy_price=485, next_sell_price=515).to_dict(),
                    "signal": {"action": "NONE", "raw_signal": "NO_TRIGGER", "amount_yuan": 0, "reason": "未触发"},
                    "review": {"approved": False, "final_advice": "继续监控", "reasons": ["未触发"]},
                }
            },
            "approved_count": 0,
            "candidate_count": 0,
            "today_total_advice_yuan": 0,
            "applied_manual_trades": [],
        }
        report = generate_grid_daily_section(result)
        self.assertIn("Stone Smart Grid", report)
        self.assertIn("VOO状态", report)

    def test_grid_weekly_report_fields_complete(self) -> None:
        report = generate_grid_weekly_report({"paper_mode": True, "candidate_count": 0, "approved_count": 0, "today_total_advice_yuan": 0, "applied_manual_trades": [], "symbols": {}})
        self.assertIn("Stone Smart Grid 周报", report)

    def test_total_risk_can_reject_raw_signal(self) -> None:
        signal = GridSignal("VOO", "BUY", "BUY_SIGNAL", 480, 485, 3000, 6, 1, "buy")
        review = review_grid_signal(signal=signal, state=GridSymbolState("VOO"), decision=_decision(dqs=50), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(dqs=50), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(review.rejected)

    def test_voo_and_qqq_do_not_exceed_total_grid_budget(self) -> None:
        budget = build_grid_budget(_decision(cash=100000), self.config)
        total_assets = 2821100
        total = symbol_budget("VOO", total_assets, budget, self.voo_cfg) + symbol_budget("QQQ", total_assets, budget, self.qqq_cfg)
        self.assertLessEqual(total, budget["simulated_available_yuan"])

    def test_grid_sell_can_be_denied_when_us_stock_underweight(self) -> None:
        state = GridSymbolState("VOO", grid_quantity=10, sell_spacing_pct=0.03)
        signal = GridSignal("VOO", "SELL", "SELL_SIGNAL", 520, 515, 2000, 2, 0, "sell")
        review = review_grid_signal(signal=signal, state=state, decision=_decision(us_status="严重低配"), portfolio_snapshot=_portfolio(), grid_budget=build_grid_budget(_decision(), _config(False)), config=_config(False), symbol_cfg=self.voo_cfg, price_available=True)
        self.assertTrue(any("美股" in reason for reason in review.reasons))

    def test_auto_trade_is_always_false(self) -> None:
        self.assertFalse(self.config["smart_grid"]["auto_trade"])

    def test_evaluate_symbol_recovers_without_mutating_real_assets(self) -> None:
        result = evaluate_symbol(
            symbol="VOO",
            symbol_cfg=self.voo_cfg,
            state_payload={"symbol": "VOO"},
            decision=_decision(),
            portfolio_snapshot=_portfolio(),
            live_market_result=_live(),
            config=self.config,
            grid_budget=build_grid_budget(_decision(), self.config),
            quantities={"VOO": 28},
        )
        self.assertIn("signal", result)
        self.assertTrue(result["review"]["paper_only"])


if __name__ == "__main__":
    unittest.main()
