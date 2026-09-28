"""Miner work and wallet-reported receipts are separate observations, never fiat profit."""
import math
from decimal import Decimal
from datetime import datetime, timezone
from local_rpc import request_json, rpc

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Expected nonnegative integer")
    return value

def parse_work(data):
    if not str(data.get("version", "")) or not isinstance(data.get("results"), dict):
        raise ValueError("Not an XMRig summary")
    total = data.get("hashrate", {}).get("total", [])
    rate = total[0] if total else None
    if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0):
        raise ValueError("Invalid hashrate")
    return {"hashrate_hs": rate, "accepted_shares": integer(data["results"]["shares_good"]),
            "total_shares": integer(data["results"]["shares_total"]),
            "source": "Local XMRig API; shares are work reports, not payouts", "observed_at": timestamp()}

def parse_receipts(data, address, minimum_confirmations=60, from_height=0):
    records = {}
    for t in data.get("in", []):
        if t.get("type") != "block" or t.get("address") != address:
            continue
        amount, confirmations, height = integer(t["amount"]), integer(t["confirmations"]), integer(t["height"])
        txid = t["txid"]
        if len(txid) != 64 or any(c not in "0123456789abcdefABCDEF" for c in txid):
            raise ValueError("Invalid transaction id")
        if height < from_height or t.get("double_spend_seen") is not False:
            continue
        if confirmations < max(60, minimum_confirmations) or t.get("locked") is not False:
            continue
        record = {"txid": txid.lower(), "atomic_amount": amount, "height": height, "confirmations": confirmations}
        if txid.lower() in records and records[txid.lower()] != record:
            raise ValueError("Conflicting duplicate transaction")
        records[txid.lower()] = record
    atomic = sum(r["atomic_amount"] for r in records.values())
    return {"wallet_reported_mined_xmr": str(Decimal(atomic) / Decimal(10**12)),
            "receipts": sorted(records.values(), key=lambda r: (r["height"], r["txid"])),
            "source": "Local wallet RPC; not independently chain-verified",
            "scope": f"Unlocked mined wallet history from block {from_height}; may include other workers",
            "observed_at": timestamp()}

class MoneroBuyerAdapter:
    def __init__(self, config):
        self.config, self.name = config, "Monero / P2Pool"

    def poll(self):
        c = self.config
        result = {"name": self.name, "category": "Protocol-issued proof-of-work rewards",
                  "status": "NOT_CONFIGURED", "work": None, "payments": None, "errors": [],
                  "estimated_usd": None}
        if c.get("enabled") is not True:
            result["reason"] = "Configure official local clients and a receiving address to observe real work."
            return result
        try:
            result["work"] = parse_work(request_json(c["xmrig_api"]))
        except Exception as exc:
            result["errors"].append("Work observation failed: " + type(exc).__name__)
        try:
            address = c.get("public_address", "")
            if not address:
                raise ValueError("Missing receiving address")
            account = integer(c.get("account_index", 0))
            actual = rpc(c["wallet_rpc"], "get_address", {"account_index": account})
            if actual.get("address") != address:
                raise ValueError("Wallet address mismatch")
            data = rpc(c["wallet_rpc"], "get_transfers",
                       {"in": True, "account_index": account, "filter_by_height": True,
                        "min_height": c.get("history_from_height", 0)})
            result["payments"] = parse_receipts(data, address, c.get("minimum_confirmations", 60),
                                               c.get("history_from_height", 0))
        except Exception as exc:
            result["errors"].append("Payment observation failed: " + type(exc).__name__)
        result["status"] = "OBSERVING" if not result["errors"] else "INCOMPLETE_EVIDENCE"
        result["reason"] = "Work, receipts, wallet balance and fiat profit are different measures."
        return result

