"""Supervisor that keeps the node running unattended and fails closed.

- Restarts the node after a crash, with exponential backoff and a restart-storm cooldown.
- Treats a node whose state snapshot stops updating as hung, and replaces it.
- On Windows, runs the node inside a kill-on-close Job Object, so any mining clients the
  node started are terminated with it, including if this watchdog itself dies.
- Allows one watchdog per project folder.

Stop it with `python watchdog.py --stop`, Ctrl+C or the stop file. Installing it to start at
logon is a separate, explicit step (install_watchdog.ps1).
"""
import argparse
from collections import deque
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
NODE_LOG_LIMIT = 5 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temp, path)


class InstanceLock:
    """An OS-level lock; it is released automatically if the process dies."""

    def __init__(self, path):
        self.path, self.handle = Path(path), None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self):
        if self.handle:
            self.handle.close()
            self.handle = None


class ProcessTree:
    """A child process plus everything it spawns, terminated together."""

    def __init__(self, command, log_path, cwd):
        self.job = None
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size > NODE_LOG_LIMIT:
            os.replace(log_path, log_path.with_suffix(".log.1"))
        self.log = open(log_path, "ab")
        kwargs = {"cwd": cwd, "stdout": self.log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(command, **kwargs)
        if os.name == "nt":
            try:
                self.job = _WindowsJob()
                self.job.assign(self.process)
            except OSError:
                self.kill()
                raise

    @property
    def pid(self):
        return self.process.pid

    def poll(self):
        return self.process.poll()

    def wait(self, timeout):
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self):
        """Hard stop of the whole tree. Safe to call repeatedly."""
        if self.job:
            self.job.terminate()
        elif self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                self.process.kill()
        self.wait(10)
        self.close()

    def close(self):
        if self.job:
            self.job.close()
            self.job = None
        if not self.log.closed:
            self.log.close()


class _WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k32 = k32

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def assign(self, process):
        if not self.k32.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def terminate(self):
        if self.handle:
            self.k32.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


class Watchdog:
    def __init__(self, command, state_file, runtime_dir=RUNTIME, *, stale_after=120, startup_grace=180,
                 backoff_min=5, backoff_max=300, healthy_after=600, storm_max=5, storm_window=600,
                 storm_cooldown=1800, graceful_timeout=20, poll_interval=1.0, suspend_gap=30, logger=None):
        self.command = list(command)
        self.state_file = Path(state_file)
        self.runtime = Path(runtime_dir)
        self.stop_file = self.runtime / "watchdog.stop"
        self.node_stop_file = self.runtime / "node.stop"
        self.status_file = self.runtime / "watchdog_status.json"
        self.lock = InstanceLock(self.runtime / "watchdog.lock")
        self.stale_after, self.startup_grace = stale_after, startup_grace
        self.backoff_min, self.backoff_max, self.healthy_after = backoff_min, backoff_max, healthy_after
        self.storm_max, self.storm_window, self.storm_cooldown = storm_max, storm_window, storm_cooldown
        self.graceful_timeout, self.poll_interval, self.suspend_gap = graceful_timeout, poll_interval, suspend_gap
        self.log = logger or logging.getLogger("watchdog")
        self.stop_event = threading.Event()
        self.failures, self.crash_times = 0, deque()
        self.status = {"state": "starting", "watchdog_pid": os.getpid(), "started_at": utc_now(),
                       "launches": 0, "restarts": 0, "child_pid": None, "last_exit_code": None,
                       "last_reason": None, "next_restart_at": None}

    def request_stop(self, *_):
        self.stop_event.set()

    def _write_status(self, **changes):
        self.status.update(changes, updated_at=utc_now())
        atomic_write_json(self.status_file, self.status)

    def _stop_requested(self):
        if self.stop_file.exists():
            try:
                self.stop_file.unlink()
            except OSError:
                pass
            self.stop_event.set()
        return self.stop_event.is_set()

    def _wait(self, seconds):
        deadline = time.monotonic() + seconds
        while not self._stop_requested():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            self.stop_event.wait(min(self.poll_interval, remaining))
        return False

    def run(self):
        if not self.lock.acquire():
            self.log.error("Another watchdog is already supervising this folder; exiting.")
            return 3
        try:
            try:
                self.stop_file.unlink()
            except FileNotFoundError:
                pass
            self.log.info("Watchdog %s supervising: %s", os.getpid(), " ".join(self.command))
            while not self._stop_requested():
                reason, code, ran_for = self._run_once()
                if reason == "stopped":
                    break
                delay = self._next_delay(ran_for)
                self.log.warning("Node %s (exit code %s) after %.0fs; restarting in %.0fs.",
                                 reason, code, ran_for, delay)
                restart_at = datetime.fromtimestamp(time.time() + delay, timezone.utc).isoformat()
                self._write_status(state="waiting_to_restart", child_pid=None, last_exit_code=code,
                                   last_reason=reason, next_restart_at=restart_at)
                if not self._wait(delay):
                    break
                self.status["restarts"] += 1
            self._write_status(state="stopped", child_pid=None, next_restart_at=None)
            self.log.info("Watchdog stopped.")
            return 0
        finally:
            self.lock.release()

    def _next_delay(self, ran_for):
        now = time.monotonic()
        self.failures = 1 if ran_for >= self.healthy_after else self.failures + 1
        self.crash_times.append(now)
        while self.crash_times and now - self.crash_times[0] > self.storm_window:
            self.crash_times.popleft()
        if len(self.crash_times) >= self.storm_max:
            self.log.error("Restart storm: %d exits within %ds; cooling down for %ds.",
                           len(self.crash_times), self.storm_window, self.storm_cooldown)
            self.crash_times.clear()
            return self.storm_cooldown
        return min(self.backoff_max, self.backoff_min * 2 ** (self.failures - 1))

    def _heartbeat_age(self, since_wall):
        try:
            beat = max(self.state_file.stat().st_mtime, since_wall)
        except FileNotFoundError:
            beat = since_wall
        return time.time() - beat

    def _run_once(self):
        try:
            self.node_stop_file.unlink()
        except FileNotFoundError:
            pass
        started = time.monotonic()
        try:
            tree = ProcessTree(self.command + ["--stop-file", str(self.node_stop_file)],
                               self.runtime / "node.log", ROOT)
        except OSError as exc:
            self.log.error("Could not launch node: %s", exc)
            return "failed_to_launch", None, 0.0
        self.status["launches"] += 1
        self._write_status(state="running", child_pid=tree.pid, next_restart_at=None)
        self.log.info("Node started (pid %s).", tree.pid)
        reference_wall, last_tick = time.time(), time.monotonic()
        try:
            while True:
                if self._stop_requested():
                    self._graceful_stop(tree)
                    return "stopped", tree.poll(), time.monotonic() - started
                code = tree.poll()
                if code is not None:
                    return "exited", code, time.monotonic() - started
                now = time.monotonic()
                if now - last_tick > self.suspend_gap:
                    # The PC slept or the process was frozen; give the node a fresh grace period.
                    self.log.info("Resumed after a %.0fs pause; resetting heartbeat grace.", now - last_tick)
                    reference_wall, started = time.time(), now
                last_tick = now
                if now - started > self.startup_grace and self._heartbeat_age(reference_wall) > self.stale_after:
                    self.log.error("No state snapshot for over %ds; treating node as hung.", self.stale_after)
                    tree.kill()
                    return "hung", tree.poll(), time.monotonic() - started
                self.stop_event.wait(self.poll_interval)
        finally:
            if tree.poll() is None:
                tree.kill()
            tree.close()

    def _graceful_stop(self, tree):
        self.log.info("Stopping node (pid %s).", tree.pid)
        self.node_stop_file.parent.mkdir(parents=True, exist_ok=True)
        self.node_stop_file.touch()
        if tree.wait(self.graceful_timeout) is None:
            self.log.warning("Node did not stop within %ds; terminating its process tree.", self.graceful_timeout)
        tree.kill()


def setup_logging(runtime):
    runtime.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(runtime / "watchdog.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("watchdog")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    if sys.stderr is not None:
        logger.addHandler(logging.StreamHandler())
    return logger


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stop", action="store_true", help="Ask a running watchdog to stop the node and exit")
    parser.add_argument("--status", action="store_true", help="Print the last watchdog status")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    if args.stop:
        RUNTIME.mkdir(exist_ok=True)
        (RUNTIME / "watchdog.stop").touch()
        print("Stop requested; the watchdog stops the node within about 20 seconds.")
        return 0
    if args.status:
        path = RUNTIME / "watchdog_status.json"
        print(path.read_text(encoding="utf-8") if path.exists() else '{"state": "never_run"}')
        return 0
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    interval = config.get("heartbeat_interval_seconds", 20)
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    command = [str(python), "-u", str(ROOT / "node_engine.py"), "--config", str(args.config)]
    watchdog = Watchdog(command, ROOT / "node_state.json", RUNTIME,
                        stale_after=max(120, 4 * interval), logger=setup_logging(RUNTIME))
    signal.signal(signal.SIGINT, watchdog.request_stop)
    signal.signal(signal.SIGTERM, watchdog.request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, watchdog.request_stop)
    return watchdog.run()


if __name__ == "__main__":
    sys.exit(main())
