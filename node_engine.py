"""Evidence-first zero-upfront-spend observer with an optional bounded official-client pilot."""
import argparse
import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from dashboard import create_dashboard_server
from expansion import ProtocolRadar, IdeaExpander
from protocols import (GrassBuyerAdapter, HoneygainBuyerAdapter, NodepayBuyerAdapter,
                       DawnBuyerAdapter, GradientBuyerAdapter, BlessBuyerAdapter)
from protocols.monero_buyer import MoneroBuyerAdapter
from resource_limiter import ResourceLimiter
from telemetry_hub import TelemetryHub
from pilot import Pilot

ROOT = Path(__file__).resolve().parent

def load_config(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 2 or data.get("mode") != "observe":
        raise ValueError("Expected schema 2, observe mode. Pilot activation is a separate command.")
    if data.get("upfront_spend_limit") != 0:
        raise ValueError("Only zero upfront spending is supported")
    if type(data.get("heartbeat_interval_seconds")) is not int or not 5 <= data["heartbeat_interval_seconds"] <= 3600:
        raise ValueError("Observation interval must be between 5 and 3600 seconds")
    if type(data.get("dashboard_port")) is not int or not 1 <= data["dashboard_port"] <= 65535:
        raise ValueError("Invalid dashboard port")
    return data

class AutoProtocolNodeEngine:
    def __init__(self, port=None, config_path=None, state_file=None):
        self.config = load_config(config_path or ROOT / "config.json")
        self.port = port if port is not None else self.config["dashboard_port"]
        self.hub = TelemetryHub(state_file or ROOT / "node_state.json")
        self.limiter, self.radar, self.expander = ResourceLimiter(), ProtocolRadar(), IdeaExpander()
        self.legacy = [GrassBuyerAdapter(), HoneygainBuyerAdapter(), NodepayBuyerAdapter(),
                       DawnBuyerAdapter(), GradientBuyerAdapter(), BlessBuyerAdapter()]
        self.monero = MoneroBuyerAdapter(self.config["monero"])
        self.pilot = Pilot(self.config, ROOT)
        self.stop_event = threading.Event()
        self.hub.add_log("ENGINE", "INFO", "Evidence observer initialized. Website pings earn nothing.")
        self.hub.update(discovered_protocols=self.radar.candidates(),
                        economics=self.config["economics"])

    def cycle(self):
        protocols = [self.monero.poll()] + [p.get_telemetry() for p in self.legacy]
        metrics = self.limiter.get_metrics()
        self.hub.update(protocols=protocols, system_metrics=metrics,
                        expansion_ideas=self.expander.evaluate_and_expand(protocols, metrics),
                        pilot=self.pilot.telemetry())
        self.hub.save_snapshot()
        return self.hub.get_full_state()

    def start(self, run_pilot=False, duration=None):
        server = create_dashboard_server(self.hub, self.port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        timer = None
        pool = None
        if duration is not None:
            timer = threading.Timer(duration, self.stop_event.set)
            timer.daemon = True
            timer.start()
        try:
            if run_pilot:
                self.pilot.start()
                self.hub.update(mode="bounded_pilot")
                self.hub.add_log("PILOT", "INFO", "Official clients started; automatic time limit active.")
            print(f"Local dashboard: http://127.0.0.1:{self.port}", flush=True)
            # Network observation never blocks the pilot's one-second stop/deadline checks.
            pool = ThreadPoolExecutor(max_workers=1)
            if pool is not None:
                pending = pool.submit(self.cycle)
                next_cycle = 0
                import time
                while not self.stop_event.wait(1):
                    if run_pilot and not self.pilot.tick():
                        self.hub.add_log("PILOT", "INFO", self.pilot.status)
                        break
                    if pending.done():
                        pending.result()
                        if not next_cycle:
                            next_cycle = time.monotonic() + self.config["heartbeat_interval_seconds"]
                        if time.monotonic() >= next_cycle:
                            pending = pool.submit(self.cycle)
                            next_cycle = 0
        finally:
            # Stop clients before waiting on dashboard cleanup.
            self.pilot.stop(self.pilot.status if self.pilot.status != "RUNNING_BOUNDED_PILOT" else "STOPPED")
            if pool:
                pool.shutdown(wait=True, cancel_futures=True)
            self.hub.update(pilot=self.pilot.telemetry())
            self.hub.save_snapshot()
            if timer:
                timer.cancel()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="One evidence observation; no mining or server")
    parser.add_argument("--pilot", action="store_true", help="Start the explicitly enabled bounded CPU pilot")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--port", type=int)
    parser.add_argument("--duration", type=int, help="Stop observer after this many seconds")
    args = parser.parse_args()
    if args.once and args.pilot:
        parser.error("--once and --pilot are mutually exclusive")
    if args.duration is not None and args.duration < 1:
        parser.error("--duration must be positive")
    try:
        node = AutoProtocolNodeEngine(port=args.port, config_path=args.config)
        if args.once:
            print(json.dumps(node.cycle(), indent=2))
        else:
            node.start(run_pilot=args.pilot, duration=args.duration)
    except KeyboardInterrupt:
        print("Stopped.")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Node could not start: {exc}\n")

if __name__ == "__main__":
    main()

