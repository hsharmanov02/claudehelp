"""Atomic snapshots. Unknown earnings stay unknown."""
import copy
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

class TelemetryHub:
    def __init__(self, state_file="node_state.json"):
        self.state_file, self.start_time = Path(state_file), time.monotonic()
        self.lock = threading.RLock()
        self.state = {"schema_version": 2, "mode": "observe", "protocols": [], "system_metrics": {},
                      "notice": "No income has been demonstrated by this project. Previous prototype totals were fabricated.",
                      "expansion_ideas": [], "discovered_protocols": [], "live_logs": [],
                      "pilot": {"status": "DISABLED"},
                      "totals": {"fiat_profit": None, "points_value": None, "wallet_reported_mined_xmr": None}}

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def add_log(self, protocol, level, message):
        with self.lock:
            self.state["live_logs"].append({"timestamp": datetime.now(timezone.utc).isoformat(),
                                           "protocol": protocol, "level": level, "message": message})
            self.state["live_logs"] = self.state["live_logs"][-50:]

    def get_full_state(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        result["uptime_seconds"] = int(time.monotonic() - self.start_time)
        result["observed_at"] = datetime.now(timezone.utc).isoformat()
        payments = [p["payments"] for p in result["protocols"] if p.get("payments") is not None]
        result["totals"]["wallet_reported_mined_xmr"] = (
            payments[0]["wallet_reported_mined_xmr"] if len(payments) == 1 else None)
        return result

    def save_snapshot(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.get_full_state(), indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temp, self.state_file)

