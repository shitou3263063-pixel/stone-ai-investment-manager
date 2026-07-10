from __future__ import annotations

from datetime import date
import unittest

from scripts.build_daily_snapshot import _task_gates
from scripts.preflight_check import (
    check_cash_floor,
    check_duplicate_bond,
    check_unconfirmed_not_booked,
    check_weekly_base_dca,
    evaluate_dqs,
)


def _master(cash: int = 220000) -> dict:
    return {
        "totals": {
            "us_stock": 385000,
            "hk_stock": 272600,
            "cn_stock": 266500,
            "china_bond": 1130000,
            "gold": 547000,
            "cash": cash,
            "total_assets": 2821100,
        },
        "confirmed_quantities": {
            "10年地债": {
                "asset_class": "china_bond",
                "amount_cny_approx": 132200,
                "included_in": "中国债券",
                "duplicate_counting_allowed": False,
            }
        },
    }


class ReliabilityRulesTest(unittest.TestCase):
    def test_duplicate_bond_is_not_counted_when_marked_included(self) -> None:
        result = check_duplicate_bond(_master())
        self.assertEqual(result["status"], "OK")

    def test_duplicate_bond_is_error_when_component_can_be_counted_again(self) -> None:
        master = _master()
        master["confirmed_quantities"]["10年地债"]["duplicate_counting_allowed"] = True
        result = check_duplicate_bond(master)
        self.assertEqual(result["status"], "ERROR")

    def test_cash_floor_blocks_when_cash_below_5_percent(self) -> None:
        result = check_cash_floor(_master(cash=100000))
        self.assertEqual(result["status"], "ERROR")

    def test_duplicate_base_dca_same_week_blocks(self) -> None:
        execution_state = {
            "records": [
                {"natural_week": "2026-W28", "order_type": "base_dca", "status": "suggested"},
                {"natural_week": "2026-W28", "order_type": "base_dca", "status": "pending_confirmation"},
            ]
        }
        result = check_weekly_base_dca(execution_state, date(2026, 7, 8))
        self.assertEqual(result["status"], "ERROR")

    def test_non_wednesday_default_dca_is_not_allowed(self) -> None:
        dqs = evaluate_dqs(90, [])
        gates = _task_gates(date(2026, 7, 9), dqs, {"records": []})
        self.assertFalse(gates["wednesday_dca"]["trade_gate"])
        self.assertFalse(gates["daily_cio"]["trade_gate"])

    def test_low_dqs_cannot_give_precise_amount_or_trade_advice(self) -> None:
        result = evaluate_dqs(69, [])
        self.assertFalse(result["trade_advice_allowed"])
        self.assertFalse(result["precise_amount_allowed"])
        self.assertEqual(result["decision"], "no_trade_advice")

    def test_mid_dqs_only_allows_cap_not_precise_amount(self) -> None:
        result = evaluate_dqs(80, [])
        self.assertTrue(result["trade_advice_allowed"])
        self.assertFalse(result["precise_amount_allowed"])
        self.assertEqual(result["decision"], "direction_or_cap_only")

    def test_ibkr_failure_degrades_to_no_auto_booking(self) -> None:
        dqs = evaluate_dqs(90, [])
        gates = _task_gates(date(2026, 7, 8), dqs, {"records": []})
        self.assertTrue(gates["weekly_review"]["reconcile_gate"])
        self.assertEqual(gates["global_monitor"]["trade_gate"], False)

    def test_unconfirmed_trade_must_not_be_booked(self) -> None:
        execution_state = {
            "records": [
                {
                    "id": "draft-1",
                    "order_type": "opportunity_add",
                    "status": "pending_confirmation",
                    "booked_to_portfolio": True,
                }
            ]
        }
        result = check_unconfirmed_not_booked(execution_state)
        self.assertEqual(result["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
