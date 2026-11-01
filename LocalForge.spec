# PyInstaller build spec for LocalForge Desktop (Windows).
# Bundles the whole Python standard library so the AI's Python code can import anything from it.
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_all

SKIP = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv", "pydoc_data",
        "this", "antigravity", "__phello__", "_tkinter", "msilib", "curses", "readline", "tty", "pty", "termios",
        "pwd", "grp", "fcntl", "resource", "posix", "crypt", "nis", "spwd", "syslog", "_posixsubprocess", "_curses",
        "_curses_panel", "ossaudiodev", "_crypt", "_gdbm", "dbm.gnu", "dbm.ndbm", "_dbm", "_posixshmem"}
hidden = []
for name in sorted(sys.stdlib_module_names):
    if name in SKIP or name.startswith("_"):
        continue
    hidden.append(name)
    try:
        hidden += [m for m in collect_submodules(name) if not any(m == s or m.startswith(s + ".") for s in SKIP)
                   and ".test" not in m and "idle_test" not in m]
    except Exception:
        pass

wv_datas, wv_bins, wv_hidden = collect_all("webview")

a = Analysis(
    ["run_localforge.py"],
    pathex=["."],
    binaries=wv_bins,
    datas=[("localforge/web", "localforge/web")] + wv_datas,
    hiddenimports=hidden + wv_hidden + collect_submodules("localforge"),
    excludes=["tkinter", "test", "idlelib", "lib2to3", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LocalForge",
    console=False,
    icon=None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LocalForge", upx=False)
