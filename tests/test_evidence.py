import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from node_engine import AutoProtocolNodeEngine, load_config, ROOT
from protocols import GrassBuyerAdapter, HoneygainBuyerAdapter, NodepayBuyerAdapter, DawnBuyerAdapter, GradientBuyerAdapter, BlessBuyerAdapter
from protocols.monero_buyer import parse_receipts, parse_work, MoneroBuyerAdapter
from local_rpc import local_url, rpc
from telemetry_hub import TelemetryHub
from pilot import Pilot, build_miner_config, checked_binary
from dashboard import create_dashboard_server

ADDRESS = "test-address-never-used-on-network"

def receipt(**changes):
    value = {"type": "block", "address": ADDRESS, "amount": 270_000_000,
             "confirmations": 61, "height": 100, "txid": "a" * 64,
             "double_spend_seen": False, "locked": False}
    value.update(changes)
    return value

class EvidenceTests(unittest.TestCase):
    def test_no_legacy_adapter_can_earn_or_request_network(self):
        async def check():
            session = Mock()
            for cls in (GrassBuyerAdapter, HoneygainBuyerAdapter, NodepayBuyerAdapter,
                        DawnBuyerAdapter, GradientBuyerAdapter, BlessBuyerAdapter):
                adapter = cls()
                await adapter.initialize(session, {})
                for _ in range(4):
                    observation = await adapter.execute_heartbeat()
                    self.assertIsNone(observation["estimated_usd"])
                    self.assertIsNone(observation["points_earned"])
                    self.assertEqual(observation["status"], "RESEARCH_ONLY")
                self.assertIsNone((await adapter.poll_earnings())["amount"])
            self.assertEqual(session.mock_calls, [])
        asyncio.run(check())

    def test_only_unlocked_mined_receipts_for_configured_address(self):
        values = [receipt(), receipt(type="in", txid="b"*64),
                  receipt(address="different", txid="c"*64),
                  receipt(locked=True, txid="d"*64),
                  receipt(confirmations=59, txid="e"*64),
                  receipt(double_spend_seen=True, txid="f"*64)]
        result = parse_receipts({"in": values}, ADDRESS)
        self.assertEqual(result["wallet_reported_mined_xmr"], "0.00027")
        self.assertEqual(len(result["receipts"]), 1)

    def test_duplicates_and_repeated_polls_do_not_compound(self):
        data = {"in": [receipt(), receipt()]}
        first, second = parse_receipts(data, ADDRESS), parse_receipts(data, ADDRESS)
        self.assertEqual(first["receipts"], second["receipts"])
        self.assertEqual(first["wallet_reported_mined_xmr"], second["wallet_reported_mined_xmr"])
        self.assertEqual(first["wallet_reported_mined_xmr"], "0.00027")

    def test_reorg_removal_is_not_retained(self):
        self.assertEqual(len(parse_receipts({"in": [receipt()]}, ADDRESS)["receipts"]), 1)
        self.assertEqual(parse_receipts({}, ADDRESS)["wallet_reported_mined_xmr"], "0")

    def test_pending_unknown_and_old_transfers_do_not_count(self):
        self.assertEqual(parse_receipts({"pending": [receipt()]}, ADDRESS)["receipts"], [])
        self.assertEqual(parse_receipts({"in": [receipt(locked=None)]}, ADDRESS)["receipts"], [])
        self.assertEqual(parse_receipts({"in": [receipt()]}, ADDRESS, from_height=101)["receipts"], [])

    def test_conflicting_duplicate_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_receipts({"in": [receipt(), receipt(amount=400)]}, ADDRESS)

    def test_invalid_amounts_are_rejected(self):
        for value in (-1, float("nan"), True, "200"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_receipts({"in": [receipt(amount=value)]}, ADDRESS)

    def test_worker_shares_never_become_money(self):
        work = parse_work({"version": "6", "hashrate": {"total": [32.0, 30.0, None]},
                           "results": {"shares_good": 123, "shares_total": 124}})
        self.assertEqual(work["accepted_shares"], 123)
        self.assertNotIn("wallet_reported_mined_xmr", work)
        self.assertNotIn("estimated_usd", work)

    def test_unknown_and_nonfinite_hashrates(self):
        data = {"version": "6", "hashrate": {"total": [None]}, "results": {"shares_good": 0, "shares_total": 0}}
        self.assertIsNone(parse_work(data)["hashrate_hs"])
        data["hashrate"]["total"][0] = float("nan")
        with self.assertRaises(ValueError):
            parse_work(data)

    def test_network_failure_clears_receipts_instead_of_inventing_zero(self):
        c = load_config(ROOT / "config.json")["monero"]
        c.update(enabled=True, public_address=ADDRESS)
        adapter = MoneroBuyerAdapter(c)
        with patch("protocols.monero_buyer.request_json", side_effect=OSError), patch("protocols.monero_buyer.rpc", side_effect=OSError):
            result = adapter.poll()
        self.assertIsNone(result["payments"])
        self.assertIsNone(result["work"])
        self.assertEqual(result["status"], "INCOMPLETE_EVIDENCE")

    def test_wallet_mismatch_prevents_receipt_lookup(self):
        c = load_config(ROOT / "config.json")["monero"]
        c.update(enabled=True, public_address=ADDRESS)
        with patch("protocols.monero_buyer.request_json", side_effect=OSError), patch("protocols.monero_buyer.rpc", return_value={"address": "wrong"}) as call:
            result = MoneroBuyerAdapter(c).poll()
        self.assertIsNone(result["payments"])
        self.assertEqual(call.call_count, 1)

    def test_rpc_rejects_remote_urls_and_spending(self):
        for url in ("https://example.com", "http://example.com:80", "http://user:pass@127.0.0.1:18083", "file:///tmp/a"):
            with self.assertRaises(ValueError):
                local_url(url)
        with self.assertRaises(ValueError):
            rpc("http://127.0.0.1:18083/json_rpc", "transfer", {"amount": 1})

    def test_disabled_node_never_calls_network(self):
        with tempfile.TemporaryDirectory() as directory, patch("local_rpc.request_json") as network:
            node = AutoProtocolNodeEngine(state_file=Path(directory) / "state.json")
            state = node.cycle()
            self.assertIsNone(state["totals"]["wallet_reported_mined_xmr"])
            self.assertIsNone(state["totals"]["fiat_profit"])
            self.assertEqual(network.call_count, 0)
            self.assertTrue((Path(directory) / "state.json").exists())

    def test_invalid_config_rejected(self):
        config = load_config(ROOT / "config.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for field, value in (("upfront_spend_limit", 1), ("schema_version", 1), ("heartbeat_interval_seconds", 0)):
                changed = copy.deepcopy(config)
                changed[field] = value
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_hash_pinning_detects_modified_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-an-executable"
            path.write_bytes(b"test fixture")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(checked_binary(str(path), digest), str(path.resolve()))
            path.write_bytes(b"modified")
            with self.assertRaises(ValueError):
                checked_binary(str(path), digest)

    def test_pilot_config_limits_and_gpu_backends(self):
        p = load_config(ROOT / "config.json")["pilot"]
        built = build_miner_config(p)
        self.assertEqual(built["cpu"]["rx"], [-1])
        self.assertFalse(built["opencl"]["enabled"])
        self.assertFalse(built["cuda"]["enabled"])
        self.assertEqual(built["http"]["host"], "127.0.0.1")
        self.assertFalse(built["randomx"]["wrmsr"])
        p["threads"] = 20
        with self.assertRaises(ValueError):
            build_miner_config(p)

    def test_pilot_disabled_fails_before_starting_clients(self):
        with patch("pilot.subprocess.Popen") as launch:
            with self.assertRaises(ValueError):
                Pilot(load_config(ROOT / "config.json"), ROOT).start()
            launch.assert_not_called()

    def test_pilot_deadline_terminates_only_owned_clients(self):
        p = Pilot(load_config(ROOT / "config.json"), ROOT)
        process = Mock()
        process.poll.return_value = None
        p.processes = [process]
        p.status, p.deadline = "RUNNING_BOUNDED_PILOT", 0
        self.assertFalse(p.tick())
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
        self.assertEqual(p.status, "TIME_LIMIT_REACHED")

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.hub = TelemetryHub()
        self.server = create_dashboard_server(self.hub, 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_snapshot_and_static_page(self):
        with urlopen(self.url + "/api/status") as response:
            self.assertIsNone(json.load(response)["totals"]["fiat_profit"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        with urlopen(self.url) as response:
            self.assertIn(b"A payment must leave evidence", response.read())

    def test_no_file_traversal_or_bad_host(self):
        for request in (self.url + "/../config.json", Request(self.url + "/api/status", headers={"Host": "attacker.example"})):
            with self.assertRaises(HTTPError):
                urlopen(request)

if __name__ == "__main__":
    unittest.main()
