"""Looks at the PC: CPU, memory, graphics card and free disk space."""
import ctypes
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, asdict, field

from . import config

GB = 1024 ** 3
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class Gpu:
    name: str
    vram: int          # bytes, 0 if unknown
    vendor: str        # nvidia | amd | intel | other


@dataclass
class PcInfo:
    os: str
    cpu: str
    cores: int          # physical-ish core count used for threads
    logical: int
    totalRam: int
    availRam: int
    freeDisk: int
    gpus: list = field(default_factory=list)

    @property
    def best_gpu(self):
        real = [g for g in self.gpus if g.vendor in ("nvidia", "amd") or g.vram >= 3 * GB]
        return max(real, key=lambda g: g.vram) if real else None

    @property
    def vram(self) -> int:
        g = self.best_gpu
        return g.vram if g else 0

    @property
    def model_budget(self) -> int:
        """Largest model file that still runs at a usable speed on this PC."""
        vram = self.vram
        ram = self.totalRam
        if vram >= 4 * GB:
            # Whole model on the graphics card, leaving room for the context cache.
            gpu_fit = int(vram * 0.85) - 700 * 1024 ** 2
            # Partly offloaded models get slow fast; allow at most ~25% spill into RAM.
            return max(gpu_fit, min(int(gpu_fit * 1.25), int(ram * 0.45)))
        # CPU only: memory bandwidth limits speed, so keep models modest.
        return min(int(ram * 0.4), 9 * GB)

    @property
    def moe_budget(self) -> int:
        """Mixture-of-experts models (e.g. 30B-A3B) only touch a few GB per token, so their experts can sit in
        system RAM while the rest runs on the graphics card. They can be much bigger than VRAM and stay fast."""
        return int(max(self.vram, 0) * 0.85 + self.totalRam * 0.55)

    def to_dict(self):
        d = asdict(self)
        d["vram"] = self.vram
        d["modelBudget"] = self.model_budget
        g = self.best_gpu
        d["bestGpu"] = asdict(g) if g else None
        return d


def _run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _memory():
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullTotalPhys, st.ullAvailPhys
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        return info["MemTotal"], info.get("MemAvailable", info["MemFree"])
    except Exception:
        return 8 * GB, 4 * GB


def _cpu_name():
    if os.name == "nt":
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            pass
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "Unknown CPU"


def _physical_cores(logical: int) -> int:
    if os.name == "nt":
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfCores -Sum).Sum"])
        try:
            n = int(out.strip())
            if n > 0:
                return n
        except ValueError:
            pass
    return max(1, logical // 2) if logical >= 8 else logical


def _vendor(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "rtx" in n or "quadro" in n:
        return "nvidia"
    if "amd" in n or "radeon" in n:
        return "amd"
    if "intel" in n or "arc" in n:
        return "intel"
    return "other"


def _gpus():
    gpus = []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            try:
                gpus.append(Gpu(parts[0], int(float(parts[1])) * 1024 ** 2, "nvidia"))
            except ValueError:
                pass
    if os.name == "nt":
        # The registry has the real 64-bit memory size (WMI's AdapterRAM stops at 4 GB).
        ps = (
            "$r=@(); Get-ChildItem 'HKLM:\\SYSTEM\\ControlSet001\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}' "
            "-ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue; "
            "if ($p.DriverDesc) { $m = $p.'HardwareInformation.qwMemorySize'; if (-not $m) { $m = $p.'HardwareInformation.MemorySize' }; "
            "if ($m -is [byte[]]) { $m = [BitConverter]::ToUInt32($m,0) }; $r += [pscustomobject]@{name=$p.DriverDesc; vram=[uint64]$m} } }; "
            "$r | ConvertTo-Json -Compress"
        )
        out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=15)
        try:
            data = json.loads(out) if out.strip() else []
            if isinstance(data, dict):
                data = [data]
            for d in data:
                name = str(d.get("name") or "")
                if not name or "basic" in name.lower() or "virtual" in name.lower() or "remote" in name.lower():
                    continue
                if any(g.name == name for g in gpus):
                    continue
                gpus.append(Gpu(name, int(d.get("vram") or 0), _vendor(name)))
        except Exception:
            pass
    return gpus


_cache = None


def scan(refresh=False) -> PcInfo:
    global _cache
    if _cache is not None and not refresh:
        total, avail = _memory()
        _cache.availRam = avail
        _cache.freeDisk = shutil.disk_usage(config.models_dir()).free
        return _cache
    total, avail = _memory()
    logical = os.cpu_count() or 4
    _cache = PcInfo(
        os=f"{platform.system()} {platform.release()}",
        cpu=_cpu_name(),
        cores=_physical_cores(logical),
        logical=logical,
        totalRam=total,
        availRam=avail,
        freeDisk=shutil.disk_usage(config.models_dir()).free,
        gpus=_gpus(),
    )
    return _cache


def fmt_bytes(b: int) -> str:
    if not b or b <= 0:
        return "?"
    if b >= GB:
        return f"{b / GB:.1f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024 ** 2:.0f} MB"
    if b >= 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b} B"
