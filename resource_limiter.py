"""Measure observer CPU, without invented metrics or promises of hard resource caps."""
import os
import sys
import time
import ctypes

class ResourceLimiter:
    def __init__(self, target_cpu_max_pct=2.5):
        self.previous = (time.monotonic(), time.process_time())
        self.priority = "unchanged"
        if sys.platform == "win32":
            try:
                k = ctypes.WinDLL("kernel32", use_last_error=True)
                k.GetCurrentProcess.restype = ctypes.c_void_p
                k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                if k.SetPriorityClass(k.GetCurrentProcess(), 0x00004000):
                    self.priority = "below normal"
            except OSError:
                pass

    def get_metrics(self):
        wall, cpu = time.monotonic(), time.process_time()
        elapsed = wall - self.previous[0]
        value = (cpu - self.previous[1]) / elapsed * 100 if elapsed > 0 else None
        self.previous = (wall, cpu)
        return {"process_cpu_pct": round(value, 2) if value is not None else None,
                "system_cpu_pct": None, "gpu_pct": None, "ram_mb": None, "logical_cpus": os.cpu_count(),
                "priority": self.priority, "scope": "Observer only, one-core CPU percentage; worker usage excluded"}

