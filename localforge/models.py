"""Models on this PC: downloaded ones, ones found by a scan, and downloads in progress."""
import os
import re
import string
import threading
import time
from pathlib import Path

from . import config, hf
from .hardware import GB

SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)
SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "programdata", "node_modules", ".git",
             "appdata", "winsxs", "recovery", "msocache", "perflogs"}


def drives() -> list[Path]:
    if os.name != "nt":
        return [Path.home()]
    out = []
    for letter in string.ascii_uppercase:
        p = Path(f"{letter}:\\")
        try:
            if p.exists():
                out.append(p)
        except OSError:
            pass
    return out


class ModelStore:
    def __init__(self, settings):
        self.settings = settings
        self.dir = config.models_dir()
        self.downloads: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---- local models ----
    def _model_entry(self, p: Path, source: str):
        m = SPLIT_RE.search(p.name)
        if m and m.group(1) != "00001":
            return None
        size = p.stat().st_size
        if m:
            total = int(m.group(2))
            base = p.name[: m.start()]
            size = sum((p.parent / f"{base}-{i:05d}-of-{total:05d}.gguf").stat().st_size
                       for i in range(1, total + 1) if (p.parent / f"{base}-{i:05d}-of-{total:05d}.gguf").exists())
        return {"name": p.name, "path": str(p), "size": size, "source": source}

    def local_models(self) -> list[dict]:
        out, seen = [], set()
        for p in sorted(self.dir.glob("*.gguf")):
            if "mmproj" in p.name.lower():
                continue
            e = self._model_entry(p, "downloaded")
            if e:
                out.append(e)
                seen.add(str(p).lower())
        for s in self.settings.get("foundModels") or []:
            p = Path(s)
            if str(p).lower() in seen or not p.is_file():
                continue
            e = self._model_entry(p, "found")
            if e:
                out.append(e)
                seen.add(str(p).lower())
        out.sort(key=lambda m: m["name"].lower())
        return out

    def find(self, name_or_path: str):
        for m in self.local_models():
            if m["name"] == name_or_path or m["path"] == name_or_path:
                return m
        return None

    def scan_pc(self, only_uncensored=True) -> int:
        """Looks across all drives for GGUF model files you already have (LM Studio, Ollama-style folders, Downloads…)."""
        hits = []
        deadline = time.time() + 90

        def walk(d: Path, depth: int):
            if depth > 8 or time.time() > deadline:
                return
            try:
                it = list(os.scandir(d))
            except OSError:
                return
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        n = e.name.lower()
                        if n.startswith(".") and n not in (".lmstudio", ".cache"):
                            continue
                        if n in SKIP_DIRS and not (n == "appdata" and depth <= 3):
                            continue
                        walk(Path(e.path), depth + 1)
                    elif e.name.lower().endswith(".gguf") and "mmproj" not in e.name.lower():
                        if e.stat().st_size > 50 * 1024 ** 2 and (not only_uncensored or hf.is_uncensored(e.path)):
                            hits.append(e.path)
                except OSError:
                    continue

        roots = drives() if os.name == "nt" else [Path.home()]
        for r in roots:
            walk(r, 0)
        own = str(self.dir).lower()
        hits = [h for h in hits if not h.lower().startswith(own)]
        self.settings.set("foundModels", sorted(set(hits)))
        return len(hits)

    def add_file(self, path: str) -> dict:
        p = Path(path)
        if not p.is_file() or not p.name.lower().endswith(".gguf"):
            raise ValueError("Pick a .gguf model file")
        found = list(self.settings.get("foundModels") or [])
        if str(p) not in found:
            found.append(str(p))
            self.settings.set("foundModels", found)
        return self._model_entry(p, "found")

    def delete(self, path: str) -> str:
        p = Path(path)
        if p.parent.resolve() == self.dir.resolve():
            m = SPLIT_RE.search(p.name)
            if m:
                base = p.name[: m.start()]
                for f in self.dir.glob(f"{base}-*-of-{m.group(2)}.gguf"):
                    f.unlink(missing_ok=True)
            else:
                p.unlink(missing_ok=True)
            return f"Deleted {p.name}"
        # Found by a scan: forget it, never delete the user's own copy.
        found = [f for f in (self.settings.get("foundModels") or []) if f != str(p)]
        self.settings.set("foundModels", found)
        return f"Removed {p.name} from the list (the file itself was left alone)"

    # ---- downloads ----
    def download(self, file: dict, token: str | None, free_disk: int, on_done=None) -> str:
        name = file["fileName"]
        with self._lock:
            d = self.downloads.get(name)
            if d and d["state"] == "running":
                return f"Already downloading {name}"
            if file.get("size", 0) > free_disk - 1 * GB:
                return f"Not enough free disk space for {name}"
            d = {"name": name, "repo": file["repo"], "done": 0, "total": file.get("size", 0), "state": "running",
                 "error": None, "speed": 0.0, "cancel": threading.Event(), "file": file}
            self.downloads[name] = d
        threading.Thread(target=self._run_download, args=(d, token, on_done), daemon=True).start()
        return f"Downloading {name}"

    def _run_download(self, d, token, on_done):
        file = d["file"]
        parts = file.get("parts") or [(file["path"], file.get("size", 0))]
        finished = 0
        window = [time.time(), 0]
        current = [0]

        def on_bytes(n, reset=False):
            if reset:
                current[0] = n
            else:
                current[0] += n
                window[1] += n
            d["done"] = finished + current[0]
            now = time.time()
            if now - window[0] >= 1.0:
                d["speed"] = window[1] / (now - window[0])
                window[0], window[1] = now, 0

        try:
            for path, size in parts:
                target = self.dir / path.rsplit("/", 1)[-1]
                if target.exists() and size and target.stat().st_size == size:
                    finished += size
                    continue
                current[0] = 0
                hf.download_part(d["repo"], path, target, token, d["cancel"], on_bytes, size)
                finished += target.stat().st_size
            d["done"] = d["total"] = finished
            d["state"] = "done"
            with self._lock:
                self.downloads.pop(d["name"], None)
            if on_done:
                on_done(d["name"])
        except InterruptedError:
            d["state"] = "cancelled"
        except Exception as e:
            d["state"] = "error"
            d["error"] = str(e)

    def cancel(self, name: str, delete_partial=True):
        d = self.downloads.pop(name, None)
        if d:
            d["cancel"].set()
            if delete_partial:
                time.sleep(0.3)
                for path, _ in d["file"].get("parts") or [(d["file"]["path"], 0)]:
                    (self.dir / (path.rsplit("/", 1)[-1] + ".part")).unlink(missing_ok=True)

    def download_list(self):
        return [{k: v for k, v in d.items() if k not in ("cancel", "file")} for d in self.downloads.values()]
