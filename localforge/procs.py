"""Makes child processes (llama-server, the browser) close when LocalForge closes, even if it crashes."""
import ctypes
import os
import subprocess

_job = None


def _windows_job():
    global _job
    if _job is not None:
        return _job
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    _job = job
    return job


def tie_to_app(proc: subprocess.Popen):
    """Windows: put the child in a job that is killed when this process exits."""
    if os.name != "nt":
        return
    try:
        job = _windows_job()
        PROCESS_ALL_ACCESS = 0x1F0FFF
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, proc.pid)
        ctypes.windll.kernel32.AssignProcessToJobObject(job, h)
        ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass


def kill_matching(fragment: str):
    """Kills leftover processes whose command line contains fragment (e.g. our browser profile folder)."""
    try:
        if os.name == "nt":
            frag = fragment.replace("'", "''")
            ps = f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{frag}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20, creationflags=0x08000000)
        else:
            subprocess.run(["pkill", "-f", fragment], capture_output=True, timeout=10)
    except Exception:
        pass
