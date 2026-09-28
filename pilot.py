"""Bounded official-client pilot. Requires existing, hash-pinned clients and local nodes.

No download, paid provisioning, wallet creation, transactions, or auto-upgrades.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import threading
from urllib.parse import urlsplit
from local_rpc import local_url, rpc


def checked_binary(path, expected):
    candidate = Path(path)
    if not path or not candidate.is_file():
        raise ValueError("Official client executable is missing")
    if len(expected) != 64:
        raise ValueError("A reviewed executable SHA-256 is required")
    with candidate.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual.lower() != expected.lower():
        raise ValueError("Executable hash does not match the reviewed version")
    return str(candidate.resolve())


def build_miner_config(p):
    if type(p.get("threads")) is not int or p["threads"] != 1:
        raise ValueError("This bootstrap pilot permits one mining thread")
    if not 1 <= p["api_port"] <= 65535 or not 1 <= p["stratum_port"] <= 65535:
        raise ValueError("Invalid port")
    if p.get("pause_on_active_seconds", 0) < 120:
        raise ValueError("Require at least 120 seconds of user inactivity")
    return {
        "autosave": False, "background": False, "colors": False, "title": False,
        "randomx": {"init": 1, "init-avx2": 1, "mode": "light", "rdmsr": False,
                    "wrmsr": False, "numa": False},
        "cpu": {"enabled": True, "rx": [-1], "priority": 0, "yield": True,
                "huge-pages": False, "huge-pages-jit": False},
        "opencl": {"enabled": False}, "cuda": {"enabled": False},
        "donate-level": 1, "donate-over-proxy": 0,
        "pools": [{"url": f"127.0.0.1:{p['stratum_port']}", "user": "x", "pass": "x",
                   "coin": "monero", "algo": "rx/0", "enabled": True}],
        "http": {"enabled": True, "host": "127.0.0.1", "port": p["api_port"],
                 "restricted": True, "access-token": os.environ.get("XMRIG_API_TOKEN")},
        "pause-on-battery": True, "pause-on-active": p["pause_on_active_seconds"],
        "watch": False, "dmi": False, "print-time": 60,
    }


class Pilot:
    def __init__(self, config, root):
        self.config, self.root = config, Path(root)
        self.processes, self.logs = [], []
        self.deadline = None
        self.status = "DISABLED"
        self.lock = threading.RLock()
        self.timer = None

    def prepare(self):
        p, m = self.config["pilot"], self.config["monero"]
        if p.get("allow_cpu_work") is not True or m.get("enabled") is not True:
            raise ValueError("CPU pilot and Monero observation must be explicitly enabled in config.json")
        if type(p.get("duration_seconds")) is not int or not 1 <= p["duration_seconds"] <= 1800:
            raise ValueError("Pilot must be bounded to at most 1800 seconds")
        if self.config.get("upfront_spend_limit") != 0:
            raise ValueError("This bootstrap supports only zero upfront spending")
        xmrig = checked_binary(p["xmrig_binary"], p["xmrig_sha256"])
        p2pool = checked_binary(p["p2pool_binary"], p["p2pool_sha256"])
        miner_config = build_miner_config(p)
        daemon = urlsplit(local_url(m["daemon_rpc"]))
        if daemon.hostname != "127.0.0.1":
            raise ValueError("Pilot requires the local IPv4 Monero daemon")
        if m["xmrig_api"] != f"http://127.0.0.1:{p['api_port']}/2/summary":
            raise ValueError("Observer API must match the pilot API")
        info = rpc(m["daemon_rpc"], "get_info", wallet_auth=False)
        if info.get("mainnet") is not True or info.get("synchronized") is not True:
            raise ValueError("A synced mainnet Monero daemon is required")
        valid = rpc(m["wallet_rpc"], "validate_address",
                    {"address": m["public_address"], "any_net_type": False, "allow_openalias": False})
        if (valid.get("valid") is not True or valid.get("nettype") != "mainnet"
                or valid.get("subaddress") is not False or valid.get("integrated") is not False):
            raise ValueError("P2Pool requires a valid standard mainnet receiving address")
        actual = rpc(m["wallet_rpc"], "get_address", {"account_index": m.get("account_index", 0)})
        if actual.get("address") != m["public_address"]:
            raise ValueError("Receiving wallet and configured address differ")
        return p, m, daemon, xmrig, p2pool, miner_config

    def start(self):
        p, m, daemon, xmrig, p2pool, miner_config = self.prepare()
        runtime = self.root / "runtime"
        runtime.mkdir(exist_ok=True)
        config_path = runtime / "xmrig-pilot.json"
        config_path.write_text(json.dumps(miner_config, indent=2), encoding="utf-8")
        commands = [
            ("p2pool", [p2pool, "--host", "127.0.0.1", "--rpc-port", str(daemon.port),
                        "--zmq-port", "18084", "--wallet", m["public_address"], "--mini",
                        "--stratum", f"127.0.0.1:{p['stratum_port']}",
                        "--p2p", "127.0.0.1:37889", "--no-upnp", "--light-mode"]),
            ("xmrig", [xmrig, "--config", str(config_path)]),
        ]
        self.deadline = time.monotonic() + p["duration_seconds"]
        try:
            for name, command in commands:
                log = (runtime / f"{name}-pilot.log").open("ab")
                self.logs.append(log)
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                self.processes.append(subprocess.Popen(command, cwd=runtime, stdout=log,
                                                       stderr=subprocess.STDOUT, creationflags=flags))
        except Exception:
            self.stop("START_FAILED")
            raise
        self.status = "RUNNING_BOUNDED_PILOT"
        self.timer = threading.Timer(p["duration_seconds"], self.stop, args=("TIME_LIMIT_REACHED",))
        self.timer.daemon = True
        self.timer.start()

    def tick(self):
        if self.status != "RUNNING_BOUNDED_PILOT":
            return False
        if time.monotonic() >= self.deadline:
            self.stop("TIME_LIMIT_REACHED")
            return False
        if any(p.poll() is not None for p in self.processes):
            # Fail closed instead of restarting a misconfigured or rejected client.
            self.stop("CLIENT_EXITED")
            return False
        return True

    def stop(self, status="STOPPED"):
        with self.lock:
            self._stop(status)

    def _stop(self, status):
        if self.timer:
            self.timer.cancel()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        self.processes.clear()
        for log in self.logs:
            log.close()
        self.logs.clear()
        self.status = status

    def telemetry(self):
        return {"status": self.status,
                "remaining_seconds": max(0, int(self.deadline - time.monotonic())) if self.deadline else None,
                "note": "One idle CPU thread; light RandomX; no GPU backend; not a wattage or total CPU cap."}
