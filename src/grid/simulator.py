from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import GridSignal, RiskReview
from .signal_engine import signal_key


SIM_FIELDS = ["trade_id", "date", "symbol", "action", "quantity", "price", "amount_yuan", "status", "note"]


def ensure_simulation_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8", newline="") as file:
            csv.DictWriter(file, fieldnames=SIM_FIELDS).writeheader()


def append_simulated_signal(path: Path, signal: GridSignal, review: RiskReview, last_signal_key: str) -> str:
    ensure_simulation_file(path)
    key = signal_key(signal)
    if signal.action == "NONE" or key == last_signal_key:
        return last_signal_key
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=SIM_FIELDS)
        writer.writerow(
            {
                "trade_id": key,
                "date": datetime.now().isoformat(timespec="seconds"),
                "symbol": signal.symbol,
                "action": signal.action,
                "quantity": signal.quantity,
                "price": signal.price,
                "amount_yuan": signal.amount_yuan,
                "status": "simulated" if review.paper_only else "suggested",
                "note": review.final_advice,
            }
        )
    return key
