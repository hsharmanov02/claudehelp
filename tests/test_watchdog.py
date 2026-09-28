import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

from node_engine import AutoProtocolNodeEngine
from watchdog import Watchdog, InstanceLock

# The fake node receives "--stop-file <path>" from the watchdog, like node_engine.py does.
FAKE_NODE = textwrap.dedent("""
    import os, subprocess, sys, time
    from pathlib import Path
    mode, state = sys.argv[1], Path(sys.argv[2])
    stop = Path(sys.argv[sys.argv.index("--stop-file") + 1])
    if mode == "crash":
        sys.exit(7)
    if mode == "spawn":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        state.with_name("grandchild.pid").write_text(str(child.pid))
    while True:
        if mode != "hang":
            state.write_text("{}")
        if stop.exists():
            state.with_name("clean_exit").write_text("yes")
            sys.exit(0)
        time.sleep(0.1)
""")


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "fake_node.py"
        self.script.write_text(FAKE_NODE, encoding="utf-8")
        self.state = self.dir / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, mode, **options):
        settings = dict(stale_after=60, startup_grace=60, backoff_min=0.1, backoff_max=0.4,
                        healthy_after=600, storm_max=100, graceful_timeout=5, poll_interval=0.05)
        settings.update(options)
        return Watchdog([sys.executable, str(self.script), mode, str(self.state)], self.state,
                        self.dir / "runtime", **settings)

    def run_until(self, watchdog, condition, timeout=20):
        result = {}
        thread = threading.Thread(target=lambda: result.setdefault("code", watchdog.run()))
        thread.start()
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.05)
        met = condition()
        watchdog.request_stop()
        thread.join(15)
        self.assertFalse(thread.is_alive(), "watchdog did not stop")
        self.assertTrue(met, "condition never met")
        return result.get("code")

    def status(self, watchdog):
        return json.loads(watchdog.status_file.read_text(encoding="utf-8"))

    def test_crashed_node_is_restarted_with_backoff(self):
        watchdog = self.make("crash")
        self.assertEqual(self.run_until(watchdog, lambda: watchdog.status["launches"] >= 3), 0)
        status = self.status(watchdog)
        self.assertEqual(status["state"], "stopped")
        self.assertGreaterEqual(status["restarts"], 2)
        self.assertEqual(status["last_exit_code"], 7)
        self.assertEqual(status["last_reason"], "exited")

    def test_hung_node_without_heartbeat_is_replaced(self):
        watchdog = self.make("hang", stale_after=0.5, startup_grace=0.5)
        self.run_until(watchdog, lambda: watchdog.status.get("last_reason") == "hung")
        self.assertGreaterEqual(watchdog.status["launches"], 1)

    def test_healthy_node_is_left_alone_and_stopped_cleanly(self):
        watchdog = self.make("healthy", stale_after=0.5, startup_grace=0.5)
        started = time.monotonic()
        self.run_until(watchdog, lambda: time.monotonic() - started > 2)
        self.assertEqual(watchdog.status["launches"], 1)
        self.assertEqual((self.dir / "clean_exit").read_text(), "yes")
        self.assertEqual(self.status(watchdog)["state"], "stopped")

    def test_stop_file_stops_the_watchdog(self):
        watchdog = self.make("healthy")
        watchdog.stop_file.parent.mkdir(parents=True, exist_ok=True)
        thread = threading.Thread(target=watchdog.run)
        thread.start()
        while watchdog.status["launches"] < 1:
            time.sleep(0.05)
        watchdog.stop_file.touch()
        thread.join(15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.status(watchdog)["state"], "stopped")

    def test_node_children_die_with_the_node(self):
        watchdog = self.make("spawn", stale_after=0.5, startup_grace=0.5)
        pid_file = self.dir / "grandchild.pid"
        self.run_until(watchdog, pid_file.exists)
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 10
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(pid), "a process started by the node outlived it")

    def test_restart_storm_triggers_cooldown(self):
        watchdog = self.make("crash", storm_max=3, storm_window=60, storm_cooldown=999)
        delays = [watchdog._next_delay(1) for _ in range(3)]
        self.assertEqual(delays[:2], [0.1, 0.2])
        self.assertEqual(delays[2], 999)

    def test_backoff_resets_after_a_healthy_run(self):
        watchdog = self.make("crash")
        for _ in range(4):
            watchdog._next_delay(1)
        self.assertEqual(watchdog._next_delay(601), 0.1)

    def test_only_one_watchdog_per_folder(self):
        lock = InstanceLock(self.dir / "runtime" / "watchdog.lock")
        self.assertTrue(lock.acquire())
        try:
            self.assertEqual(self.make("healthy").run(), 3)
        finally:
            lock.release()


class NodeStopFileTests(unittest.TestCase):
    def test_node_stops_cleanly_when_stop_file_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop = Path(tmp) / "node.stop"
            node = AutoProtocolNodeEngine(port=0, state_file=Path(tmp) / "state.json")
            thread = threading.Thread(target=node.start, kwargs={"stop_file": stop})
            thread.start()
            time.sleep(1.5)
            stop.touch()
            thread.join(10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(node.pilot.status, "DISABLED")

    def test_second_dashboard_cannot_share_the_port(self):
        from dashboard import create_dashboard_server
        from telemetry_hub import TelemetryHub
        first = create_dashboard_server(TelemetryHub(), 0)
        try:
            with self.assertRaises(OSError):
                create_dashboard_server(TelemetryHub(), first.server_port)
        finally:
            first.server_close()


if __name__ == "__main__":
    unittest.main()
