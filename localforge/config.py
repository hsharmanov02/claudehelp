"""Paths and saved settings."""
import json
import os
import sys
import threading
from pathlib import Path

APP_NAME = "LocalForge"


def frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Folder that holds the program (and the bundled llama engines)."""
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Folder that holds bundled read-only files (web UI)."""
    if frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "localforge"
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    override = os.environ.get("LOCALFORGE_HOME")
    if override:
        d = Path(override)
    elif os.name == "nt":
        d = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        d = Path.home() / ".localforge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def home_dir() -> Path:
    return Path.home()


def work_dir() -> Path:
    """Folder where the assistant's code runs and where it saves things by default."""
    docs = Path.home() / "Documents"
    base = docs if docs.is_dir() else Path.home()
    d = base / "LocalForge"
    d.mkdir(parents=True, exist_ok=True)
    return d


DEFAULTS = {
    "nCtx": 16384,         # with a q8 cache this costs about the same memory as 8192 used to
    "threads": 0,          # 0 = automatic
    "gpuLayers": -1,       # -1 = automatic (all layers if the model fits in video memory)
    "backend": "auto",     # auto | vulkan | cuda | cpu
    "temperature": 0.4,
    "maxTokens": 2048,
    "autoApprove": False,
    "browserAutoApprove": False,
    "hfToken": "",
    "lastModel": None,
    "foundModels": [],
    "extraModelDirs": [],
}


class Settings:
    def __init__(self):
        self.path = data_dir() / "settings.json"
        self._lock = threading.Lock()
        self.values = dict(DEFAULTS)
        try:
            self.values.update(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception:
            pass
        if not self.values.get("ctxV2"):
            # Older versions defaulted to 8192 without a compressed cache: move them to 16384.
            if self.values.get("nCtx") == 8192:
                self.values["nCtx"] = 16384
            self.set("ctxV2", True)

    def get(self, key):
        return self.values.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        with self._lock:
            self.values[key] = value
            try:
                self.path.write_text(json.dumps(self.values, indent=2), encoding="utf-8")
            except Exception:
                pass

    def public(self) -> dict:
        v = {k: self.values.get(k, DEFAULTS.get(k)) for k in DEFAULTS if k not in ("foundModels",)}
        v["hfToken"] = bool(self.values.get("hfToken"))
        return v
