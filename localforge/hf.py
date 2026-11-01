"""Hugging Face Hub: uncensored GGUF search, file lists, PC-aware picks and resumable downloads."""
import json
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

UNCENSORED_MARKERS = ("uncensored", "abliterat", "ablit", "dolphin", "lexi", "heretic")


def is_uncensored(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in UNCENSORED_MARKERS)


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
    """Searches only uncensored / abliterated GGUF repos."""
    base = query.strip()
    if is_uncensored(base):
        queries = [base]
    elif not base:
        queries = ["uncensored", "abliterated"]
    else:
        queries = [f"{base} uncensored", f"{base} abliterated"]
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
            if not path.lower().endswith(".gguf") or "mmproj" in path.lower():
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
            "image", "diffusion", "flux", "-wan", "video", "speech", "whisper", "ocr")


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
    "abliterated", "uncensored",
    "3B abliterated", "4B abliterated", "7B abliterated", "8B abliterated", "9B abliterated", "12B abliterated",
    "14B abliterated", "24B abliterated", "27B abliterated", "32B abliterated", "70B abliterated",
    "8B uncensored", "12B uncensored", "24B uncensored",
    "qwen3 abliterated", "qwen3 30b a3b abliterated", "gemma 3 abliterated", "llama 3.1 8b abliterated",
    "mistral small abliterated", "mistral nemo abliterated", "qwen2.5 coder abliterated", "dolphin", "deepseek r1 distill abliterated",
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


def download_part(repo: str, path: str, target: Path, token: str | None, cancel: threading.Event, on_bytes):
    """Downloads one file to target (resuming target.part). on_bytes(n) is called as data arrives."""
    part = target.with_name(target.name + ".part")
    start = part.stat().st_size if part.exists() else 0
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
                part.replace(target)
                return
            if e.code in (401, 403):
                raise RuntimeError("This model is gated. Add a Hugging Face token in Settings.") from None
            raise RuntimeError(f"Server returned HTTP {e.code}") from None
    if resp is None:
        raise RuntimeError("Too many redirects")
    resumed = resp.status == 206
    if not resumed:
        start = 0
    on_bytes(start, reset=True)
    expected = start + int(resp.headers.get("Content-Length") or 0)
    done = start
    with resp, open(part, "ab" if resumed else "wb") as out:
        while True:
            if cancel.is_set():
                raise InterruptedError("Cancelled")
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            on_bytes(len(chunk))
    if expected > start and done < expected:
        raise RuntimeError("Download was cut off. Click download again to resume.")
    if target.exists():
        target.unlink()
    part.replace(target)
