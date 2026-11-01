"""Hugging Face Hub: uncensored GGUF search, file lists, PC-aware picks and resumable downloads."""
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import __version__
from .hardware import GB, PcInfo

BASE = "https://huggingface.co"
UA = f"LocalForge-Desktop/{__version__}"

# Only true uncensored fine-tunes. Abliterated models (refusal direction removed from the weights) are left out.
UNCENSORED_MARKERS = ("uncensored",)
ABLITERATED_MARKERS = ("abliterat", "ablit", "heretic")


def is_uncensored(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in UNCENSORED_MARKERS) and not any(m in n for m in ABLITERATED_MARKERS)


QUANT_RE = re.compile(r"(IQ\d_\w+?|Q\d_K_[SML]|Q\d_K|Q\d_\d|F16|BF16|F32)(?=[.-])", re.I)
SPLIT_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)


@dataclass
class HfFile:
    repo: str
    path: str               # first (or only) file
    size: int               # total size of all parts
    parts: list = field(default_factory=list)   # [(path, size)] for split models

    @property
    def file_name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def quant(self) -> str:
        m = QUANT_RE.search(self.file_name)
        return m.group(1).upper() if m else "GGUF"

    def to_dict(self):
        d = asdict(self)
        d["fileName"] = self.file_name
        d["quant"] = self.quant
        return d


def _request(url: str, token: str | None, extra=None) -> urllib.request.Request:
    headers = {"User-Agent": UA}
    if token and urllib.parse.urlparse(url).hostname.endswith("huggingface.co"):
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return urllib.request.Request(url, headers=headers)


def _get_json(url: str, token: str | None):
    try:
        with urllib.request.urlopen(_request(url, token), timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Hugging Face returned HTTP {e.code}") from None


def raw_search(query: str, token: str | None, limit: int = 40) -> list[dict]:
    q = urllib.parse.quote(query.strip())
    arr = _get_json(f"{BASE}/api/models?search={q}&filter=gguf&sort=downloads&direction=-1&limit={limit}", token)
    return [{"id": o["id"], "downloads": o.get("downloads", 0), "likes": o.get("likes", 0)} for o in arr]


def search(query: str, token: str | None) -> list[dict]:
    """Searches only uncensored GGUF repos (not abliterated ones)."""
    base = query.strip()
    if is_uncensored(base):
        queries = [base]
    elif not base:
        queries = ["uncensored", "uncensored gguf"]
    else:
        queries = [f"{base} uncensored", f"{base} uncensored gguf"]
    seen, out = set(), []
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda q: _safe(lambda: raw_search(q, token), []), queries))
    for r in (x for lst in results for x in lst):
        if is_uncensored(r["id"]) and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    out.sort(key=lambda r: -r["downloads"])
    for r in out:
        r["params"] = params_of(r["id"])
    return out


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _enc(p: str) -> str:
    return "/".join(urllib.parse.quote(s) for s in p.split("/"))


def gguf_files(repo: str, token: str | None) -> list[HfFile]:
    """GGUF files of a repo (one level of subfolders), with split models grouped into one entry."""
    singles: list[HfFile] = []
    splits: dict[str, list] = {}

    def walk(d: str, depth: int):
        suffix = "" if not d else "/" + _enc(d)
        for o in _get_json(f"{BASE}/api/models/{repo}/tree/main{suffix}", token):
            path = o.get("path", "")
            if o.get("type") == "directory":
                if depth < 1:
                    walk(path, depth + 1)
                continue
            if not path.lower().endswith(".gguf") or "mmproj" in path.lower() or path.rsplit("/", 1)[-1].lower().startswith("mtp-"):
                continue
            size = (o.get("lfs") or {}).get("size") or o.get("size") or 0
            m = SPLIT_RE.search(path)
            if m:
                splits.setdefault(path[: m.start()], []).append((path, size))
            else:
                singles.append(HfFile(repo, path, size, [(path, size)]))

    walk("", 0)
    for _, parts in splits.items():
        parts.sort()
        singles.append(HfFile(repo, parts[0][0], sum(s for _, s in parts), parts))
    singles.sort(key=lambda f: f.size)
    return singles


def params_of(name: str):
    """Billions of parameters from a repo name (MoE names like 30B-A3B count the full size)."""
    vals = [float(m) for m in re.findall(r"(?<![\d.A-Za-z])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])", name)]
    vals = [v for v in vals if 0.1 <= v <= 1000]
    return max(vals) if vals else None


def active_params_of(name: str):
    m = re.search(r"A(\d+(?:\.\d+)?)B", name, re.I)
    return float(m.group(1)) if m else None


QUANT_RANK = {
    "Q8_0": 1.06, "Q6_K": 1.05, "Q5_K_M": 1.03, "Q5_K_S": 1.02, "Q4_K_M": 1.0,
    "Q4_K_S": 0.97, "IQ4_XS": 0.96, "Q4_0": 0.95, "IQ4_NL": 0.95,
}
TRUSTED = ("bartowski", "mradermacher", "unsloth", "lmstudio-community", "quantfactory", "huihui-ai", "mlabonne")
EXCLUDED = ("vision", "-vl", "mmproj", "embed", "reward", "draft", "audio", "tts", "rerank", "-it-qat-q4",
            "image", "diffusion", "flux", "-wan", "video", "speech", "whisper", "ocr", "mtp-", "-mtp.")


def est_speed(file_size: int, pc: PcInfo, active_ratio: float = 1.0) -> float:
    """Rough tokens/sec: generation speed is limited by memory bandwidth."""
    if file_size <= 0:
        return 0.0
    moved = file_size * active_ratio
    vram = pc.vram
    gpu_bw = 300e9 if pc.best_gpu and pc.best_gpu.vendor in ("nvidia", "amd") else 80e9
    cpu_bw = 45e9
    usable = int(vram * 0.85) - 700 * 1024 ** 2
    if vram >= 4 * GB and file_size <= usable:
        return gpu_bw / moved
    if vram >= 4 * GB and usable > 0:
        on_gpu = usable / file_size
        t = (moved * on_gpu) / gpu_bw + (moved * (1 - on_gpu)) / cpu_bw
        return 1 / t
    return cpu_bw / moved


QUERIES = [
    "uncensored", "uncensored gguf",
    "1B uncensored", "3B uncensored", "4B uncensored", "7B uncensored", "8B uncensored", "9B uncensored",
    "12B uncensored", "14B uncensored", "20B uncensored", "24B uncensored", "27B uncensored", "32B uncensored", "70B uncensored",
    "qwen uncensored", "qwen3 uncensored", "qwen3.5 uncensored", "gemma uncensored", "llama uncensored",
    "mistral uncensored", "nemo uncensored", "dolphin uncensored", "gpt-oss uncensored", "coder uncensored",
]


def recommend(pc: PcInfo, token: str | None, limit: int = 8) -> list[dict]:
    """Best uncensored models for this PC: biggest model that fits and still runs at a comfortable speed."""
    budget = min(pc.model_budget, pc.freeDisk - 2 * GB)
    with ThreadPoolExecutor(8) as ex:
        found = list(ex.map(lambda q: _safe(lambda: raw_search(q, token, 30), []), QUERIES))
    repos, seen = [], set()
    for r in sorted((x for lst in found for x in lst), key=lambda r: -r["downloads"]):
        rid = r["id"]
        p = params_of(rid)
        if rid in seen or not is_uncensored(rid) or p is None or any(e in rid.lower() for e in EXCLUDED):
            continue
        seen.add(rid)
        repos.append(r)
    repos = repos[:40]

    def evaluate(repo):
        files = _safe(lambda: gguf_files(repo["id"], token), [])
        params = params_of(repo["id"])
        active = active_params_of(repo["id"])
        ratio = (active / params) if active and params else 1.0
        best, best_rank = None, 0
        for f in files:
            if any(e in f.file_name.lower() for e in EXCLUDED):
                continue
            rank = QUANT_RANK.get(f.quant)
            if rank and 0 < f.size <= budget and rank > best_rank:
                best, best_rank = f, rank
        if not best:
            return None
        speed = est_speed(best.size, pc, ratio)
        if speed < 4:
            return None
        speed_factor = 1.0 if speed >= 15 else speed / 15
        trust = 1.1 if repo["id"].lower().startswith(TRUSTED) else 1.0
        effective = params if not active else (params * active) ** 0.5 * 1.6   # MoE quality is between active and total
        score = effective * best_rank * speed_factor * trust
        coder = "coder" in repo["id"].lower()
        tier = "Fast" if speed >= 30 else "Balanced" if speed >= 12 else "Smart but slower"
        pstr = f"{params:.0f}" if params >= 10 else f"{params:.1f}"
        reason = f"{tier} · {pstr}B{' MoE' if active else ''} · {best.quant} · ~{int(speed)} tokens/sec on your PC"
        if coder:
            reason += " · coding model"
        return {"score": score, "downloads": repo["downloads"], "params": params,
                "file": best.to_dict(), "reason": reason, "speed": speed}

    with ThreadPoolExecutor(8) as ex:
        picks = [p for p in ex.map(evaluate, repos) if p]
    picks.sort(key=lambda p: (-p["score"], -p["downloads"]))
    out, used = [], set()
    for p in picks:
        if p["file"]["repo"] in used:
            continue
        used.add(p["file"]["repo"])
        out.append(p)
        if len(out) >= limit:
            break
    return out


def download_url(repo: str, path: str) -> str:
    return f"{BASE}/{repo}/resolve/main/{_enc(path)}?download=true"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


_part_locks: dict[str, threading.Lock] = {}
_part_guard = threading.Lock()


def _replace_with_retry(src: Path, dst: Path, tries=20):
    """Windows antivirus or indexing can hold a just-finished file for a moment; wait and retry the rename."""
    import time
    for i in range(tries):
        try:
            if dst.exists():
                dst.unlink()
            src.replace(dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.5)


def download_part(repo: str, path: str, target: Path, token: str | None, cancel: threading.Event, on_bytes,
                  expected_size: int = 0):
    """Downloads one file to target (resuming target.part). on_bytes(n) is called as data arrives.

    Only one writer per file: a lock inside this process plus a lock file that stops a second LocalForge
    (or a second self-test) from writing the same .part at the same time.
    """
    import os
    import time
    key = str(target.resolve()).lower()
    with _part_guard:
        lock = _part_locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise RuntimeError("This file is already being downloaded.")
    lock_file = target.with_name(target.name + ".lock")
    fd = None
    try:
        for _ in range(2):
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                break
            except FileExistsError:
                # Stale lock from a crash? Locks older than 2 minutes with no growing .part are stale.
                part = target.with_name(target.name + ".part")
                newest = max(lock_file.stat().st_mtime, part.stat().st_mtime if part.exists() else 0)
                if time.time() - newest > 120:
                    lock_file.unlink(missing_ok=True)
                    continue
                raise RuntimeError("Another LocalForge window is already downloading this file.") from None
        if fd is None:
            raise RuntimeError("Couldn't lock the download file.")
        _download_locked(repo, path, target, token, cancel, on_bytes, expected_size)
    finally:
        if fd is not None:
            os.close(fd)
            lock_file.unlink(missing_ok=True)
        lock.release()


def _download_locked(repo, path, target: Path, token, cancel, on_bytes, expected_size):
    part = target.with_name(target.name + ".part")
    start = part.stat().st_size if part.exists() else 0
    if expected_size and start > expected_size:
        part.unlink()          # corrupt leftover: start over
        start = 0
    if expected_size and start == expected_size:
        _replace_with_retry(part, target)
        return
    url = download_url(repo, path)
    resp = None
    for _ in range(10):
        extra = {"Range": f"bytes={start}-"} if start > 0 else None
        try:
            resp = _opener.open(_request(url, token, extra), timeout=60)
            break
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                url = urllib.parse.urljoin(url, e.headers.get("Location"))
                continue
            if e.code == 416:   # already complete
                _replace_with_retry(part, target)
                return
            if e.code in (401, 403):
                raise RuntimeError("This model is gated. Add a Hugging Face token in Settings.") from None
            raise RuntimeError(f"Server returned HTTP {e.code}") from None
    if resp is None:
        raise RuntimeError("Too many redirects")
    resumed = resp.status == 206
    if resumed:
        # Make sure the server resumed exactly where our file ends.
        cr = resp.headers.get("Content-Range", "")
        m = re.match(r"bytes (\d+)-", cr)
        if m and int(m.group(1)) != start:
            resp.close()
            part.unlink(missing_ok=True)
            raise RuntimeError("The server resumed at the wrong place. Click download again to restart cleanly.")
    else:
        start = 0
    on_bytes(start, reset=True)
    expected = start + int(resp.headers.get("Content-Length") or 0)
    if expected_size and expected <= start:
        expected = expected_size
    done = start
    with resp:
        with open(part, "r+b" if resumed else "wb") as out:
            out.seek(start)
            out.truncate()
            while True:
                if cancel.is_set():
                    raise InterruptedError("Cancelled")
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                on_bytes(len(chunk))
            out.flush()
            os.fsync(out.fileno())
    actual = part.stat().st_size
    if expected > start and actual < expected:
        raise RuntimeError("Download was cut off. Click download again to resume.")
    want = expected_size or expected
    if want and actual != want:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file has the wrong size ({actual} bytes, expected {want}). Click download again.")
    _replace_with_retry(part, target)
