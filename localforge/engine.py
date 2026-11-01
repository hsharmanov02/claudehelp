"""Runs GGUF models with llama.cpp's llama-server (bundled), and streams chat replies from it."""
import atexit
import collections
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config
from .procs import tie_to_app
from .hardware import PcInfo

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
EXE = "llama-server.exe" if os.name == "nt" else "llama-server"


def engines_dir() -> Path:
    return config.app_dir() / "engines"


def available_backends() -> dict[str, str]:
    """backend name -> path of its llama-server."""
    out = {}
    override = os.environ.get("LOCALFORGE_LLAMA_SERVER")
    if override and Path(override).is_file():
        out["custom"] = override
    for name in ("cuda", "vulkan", "cpu"):
        p = engines_dir() / name / EXE
        if p.is_file():
            out[name] = str(p)
    return out


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Engine:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.model_path: str | None = None
        self.backend: str | None = None
        self.log = collections.deque(maxlen=400)
        self.last_tps = 0.0
        self.last_prompt_tps = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._resp = None
        atexit.register(self.unload)

    @property
    def loaded(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self.model_path is not None

    def pick_backends(self, pc: PcInfo, wanted: str) -> list[str]:
        have = available_backends()
        if "custom" in have:
            return ["custom"]
        if wanted in have:
            order = [wanted]
        else:
            g = pc.best_gpu
            order = []
            if g and g.vendor == "nvidia" and "cuda" in have:
                order.append("cuda")
            if g and "vulkan" in have:
                order.append("vulkan")
        if "cpu" in have and "cpu" not in order:
            order.append("cpu")
        if not order:
            order = list(have)
        return order

    def load(self, model_path: str, pc: PcInfo, n_ctx: int, threads: int, gpu_layers: int, backend: str,
             on_status=None) -> str:
        """Starts llama-server with the model. Returns a short status message; raises on failure."""
        with self._lock:
            self._kill()
            order = self.pick_backends(pc, backend)
            if not order:
                raise RuntimeError("The llama.cpp engine is missing. Re-download LocalForge (the engines folder must sit next to LocalForge.exe).")
            errors = []
            for b in order:
                ngl = gpu_layers
                if b == "cpu":
                    ngl = 0
                if on_status:
                    on_status(f"Starting the {b.upper()} engine…")
                try:
                    self._start(available_backends()[b], model_path, n_ctx, threads, ngl)
                    self.backend = b
                    self.model_path = model_path
                    return f"{Path(model_path).name} is ready ({b.upper()})"
                except Exception as e:
                    errors.append(f"{b}: {e}")
                    self._kill()
            raise RuntimeError("Couldn't load the model. " + " | ".join(errors))

    def _start(self, exe: str, model: str, n_ctx: int, threads: int, ngl: int):
        self.port = _free_port()
        args = [exe, "-m", model, "--host", "127.0.0.1", "--port", str(self.port),
                "-c", str(n_ctx), "-np", "1", "--jinja", "--no-webui"]
        if threads and threads > 0:
            args += ["-t", str(threads)]
        if ngl is not None and ngl >= 0:
            args += ["-ngl", str(ngl)]
        self.log.clear()
        self.log.append("$ " + " ".join(f'"{a}"' if " " in a else a for a in args))
        self.proc = subprocess.Popen(
            args, cwd=str(Path(exe).parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )
        tie_to_app(self.proc)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        deadline = time.time() + 600
        while time.time() < deadline:
            if self.proc.poll() is not None:
                tail = [l for l in self.log if "error" in l.lower() or "failed" in l.lower()][-3:]
                raise RuntimeError("engine stopped while loading" + (": " + " / ".join(tail) if tail else ""))
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if r.status == 200 and json.loads(r.read() or b"{}").get("status", "ok") == "ok":
                        return
            except urllib.error.HTTPError:
                pass   # 503 while loading
            except Exception:
                pass
            time.sleep(0.4)
        raise RuntimeError("timed out loading the model")

    def _pump(self, proc):
        try:
            for raw in proc.stdout:
                self.log.append(raw.decode("utf-8", "replace").rstrip())
        except Exception:
            pass

    def _kill(self):
        p = self.proc
        self.proc = None
        self.model_path = None
        self.backend = None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def unload(self):
        with self._lock:
            self._kill()

    def stop(self):
        self._stop.set()
        r = self._resp
        if r is not None:
            try:
                r.close()
            except Exception:
                pass

    def gpu_summary(self) -> str:
        """What llama.cpp reported about offloading, e.g. 'offloaded 33/33 layers to GPU'."""
        for line in reversed(self.log):
            if "offloaded" in line and "layers" in line:
                return line.split("load_tensors:")[-1].strip()
        return ""

    def chat(self, messages: list[dict], max_tokens: int, temperature: float, on_piece) -> str:
        """Streams a reply. on_piece(text) gets each new chunk. Returns the whole reply."""
        if not self.loaded:
            raise RuntimeError("No model is loaded")
        self._stop.clear()
        self.last_tps = 0.0
        body = json.dumps({
            "messages": messages, "stream": True, "max_tokens": max_tokens, "temperature": temperature,
            "cache_prompt": True, "timings_per_token": False,
        }).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        out = []
        thinking = False
        t0 = time.time()
        n = 0
        try:
            self._resp = urllib.request.urlopen(req, timeout=600)
            for raw in self._resp:
                if self._stop.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if "timings" in obj:
                    t = obj["timings"]
                    self.last_tps = float(t.get("predicted_per_second") or 0)
                    self.last_prompt_tps = float(t.get("prompt_per_second") or 0)
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    if delta.get("reasoning_content"):
                        if not thinking:
                            thinking = True
                            on_piece("⁣")  # invisible marker: UI shows "thinking…"
                        n += 1
                    piece = delta.get("content")
                    if piece:
                        n += 1
                        out.append(piece)
                        on_piece(piece)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"Engine error {e.code}: {msg}") from None
        except Exception:
            if not self._stop.is_set():
                if not self.loaded:
                    raise RuntimeError("The engine stopped (it may have run out of memory). Try a smaller model or context size.") from None
                raise
        finally:
            r, self._resp = self._resp, None
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
        if not self.last_tps and n:
            self.last_tps = n / max(0.01, time.time() - t0)
        return "".join(out)
