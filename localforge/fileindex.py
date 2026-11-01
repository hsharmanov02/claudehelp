"""Index of the files on this PC so the assistant can find things by name or type."""
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .models import drives

MAX_ENTRIES = 600_000
SKIP = {"windows", "$recycle.bin", "system volume information", "programdata", "program files",
        "program files (x86)", "node_modules", ".git", "__pycache__", "appdata", "winsxs", "recovery",
        "msocache", "perflogs", "$windows.~bt", "$winreagent", "config.msi", "site-packages", ".venv", "venv"}

TYPE_GROUPS = {
    "photo": {"jpg", "jpeg", "png", "heic", "webp", "gif", "dng", "raw", "cr2", "nef"},
    "image": {"jpg", "jpeg", "png", "heic", "webp", "gif", "bmp", "svg", "dng", "tif", "tiff", "ico"},
    "picture": {"jpg", "jpeg", "png", "heic", "webp", "gif", "bmp"},
    "screenshot": {"jpg", "jpeg", "png"},
    "video": {"mp4", "mkv", "mov", "webm", "avi", "wmv", "flv", "m4v"},
    "movie": {"mp4", "mkv", "mov", "avi", "wmv", "m4v"},
    "music": {"mp3", "m4a", "flac", "wav", "ogg", "opus", "aac", "wma"},
    "audio": {"mp3", "m4a", "flac", "wav", "ogg", "opus", "aac", "wma"},
    "song": {"mp3", "m4a", "flac", "wav", "ogg", "opus"},
    "pdf": {"pdf"},
    "document": {"pdf", "doc", "docx", "odt", "txt", "rtf", "md", "xls", "xlsx", "ppt", "pptx", "csv"},
    "doc": {"doc", "docx", "pdf", "odt"},
    "spreadsheet": {"xls", "xlsx", "csv", "ods"},
    "excel": {"xls", "xlsx", "xlsm", "csv"},
    "word": {"doc", "docx"},
    "presentation": {"ppt", "pptx", "odp"},
    "powerpoint": {"ppt", "pptx"},
    "text": {"txt", "md", "log", "csv", "json"},
    "note": {"txt", "md"},
    "code": {"py", "js", "ts", "tsx", "jsx", "java", "c", "cpp", "h", "hpp", "cs", "html", "css", "json", "rb",
             "php", "lua", "go", "rs", "sh", "ps1", "bat", "sql", "kt", "swift"},
    "script": {"py", "js", "sh", "ts", "rb", "php", "lua", "ps1", "bat", "cmd"},
    "python": {"py", "ipynb"},
    "javascript": {"js", "mjs"},
    "html": {"html", "htm"},
    "exe": {"exe", "msi"},
    "program": {"exe", "msi"},
    "installer": {"exe", "msi"},
    "zip": {"zip", "rar", "7z", "tar", "gz"},
    "archive": {"zip", "rar", "7z", "tar", "gz"},
    "model": {"gguf", "safetensors"},
    "gguf": {"gguf"},
    "ebook": {"epub", "mobi", "pdf"},
    "book": {"epub", "mobi", "pdf"},
    "iso": {"iso", "img"},
    "font": {"ttf", "otf", "woff", "woff2"},
}

STOP = set("""the a an my me i you your can could please find search look for where is are was show list all any file files
folder folders on in of pc computer drive disk storage device and or to it that this with called named have do does see view
open read what which there some get give locate check latest recent newest biggest largest about from like just want need
into inside whats what's how many much know tell us our at be by so""".split())


class FileIndex:
    def __init__(self):
        self.entries: list[tuple] = []   # (path, name_lower, size, mtime, is_dir)
        self.built_at = 0.0
        self.building = False
        self._lock = threading.Lock()

    @property
    def ready(self):
        return self.built_at > 0

    def roots(self) -> list[Path]:
        home = Path.home()
        roots = [home]
        if os.name == "nt":
            sysdrive = (os.environ.get("SystemDrive", "C:") + "\\").upper()
            for d in drives():
                if str(d).upper() == sysdrive:
                    try:
                        for e in os.scandir(d):
                            n = e.name.lower()
                            if e.is_dir() and n not in SKIP and n != "users" and not n.startswith(("$", ".")):
                                roots.append(Path(e.path))
                    except OSError:
                        pass
                else:
                    roots.append(d)
        return roots

    def build(self):
        with self._lock:
            if self.building:
                return
            self.building = True
        try:
            out = []
            deadline = time.time() + 240

            def walk(d: str, depth: int):
                if len(out) >= MAX_ENTRIES or depth > 16 or time.time() > deadline:
                    return
                try:
                    it = list(os.scandir(d))
                except OSError:
                    return
                for e in it:
                    if len(out) >= MAX_ENTRIES:
                        return
                    name = e.name
                    if name.startswith(".") or name.startswith("$"):
                        continue
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir and name.lower() in SKIP:
                        continue
                    out.append((e.path, name.lower(), 0 if is_dir else st.st_size, st.st_mtime, is_dir))
                    if is_dir:
                        walk(e.path, depth + 1)

            for r in self.roots():
                walk(str(r), 0)
            self.entries = out
            self.built_at = time.time()
        finally:
            self.building = False

    def ensure_fresh(self, max_age=600, wait=True):
        if time.time() - self.built_at > max_age and not self.building:
            if wait and not self.ready:
                self.build()
            else:
                threading.Thread(target=self.build, daemon=True).start()

    def parse_query(self, text: str):
        lower = text.lower()
        tokens = [t.strip(".-") for t in re.findall(r"[a-z0-9_.\-]+", lower)]
        exts, words, hints = set(), [], []
        all_exts = set().union(*TYPE_GROUPS.values())
        for t in tokens:
            if not t:
                continue
            singular = t[:-1] if t.endswith("s") else t
            group = TYPE_GROUPS.get(t) or TYPE_GROUPS.get(singular)
            if group:
                exts |= group
                hints.append(singular)
            elif t in all_exts and f".{t}" in lower:
                exts.add(t)
            elif t not in STOP and len(t) >= 2:
                words.append(t)
        return {
            "words": list(dict.fromkeys(words)), "exts": exts, "hints": list(dict.fromkeys(hints)),
            "recent": bool(re.search(r"\b(latest|recent|newest|last|today|yesterday)\b", lower)),
            "big": bool(re.search(r"\b(biggest|largest|big|huge|space)\b", lower)),
        }

    def search(self, text: str, limit=25):
        q = self.parse_query(text)
        if not q["words"] and not q["exts"] and not q["recent"] and not q["big"]:
            return []
        scored = []
        for e in self.entries:
            path, name, size, mtime, is_dir = e
            ext = name.rsplit(".", 1)[-1] if "." in name else ""
            if q["exts"] and (is_dir or ext not in q["exts"]):
                continue
            score = 0.0
            if q["words"]:
                pl = path.lower()
                stem = name.rsplit(".", 1)[0]
                hits = 0
                for w in q["words"]:
                    if name == w or stem == w:
                        score += 6; hits += 1
                    elif w in name:
                        score += 3; hits += 1
                    elif w in pl:
                        score += 1; hits += 1
                if hits == 0:
                    continue
                if hits == len(q["words"]):
                    score += 4
            else:
                score = 1.0
            if q["hints"] and any(h in path.lower() for h in q["hints"]):
                score += 2
            if is_dir:
                score += 0.5
            scored.append((score, size if q["big"] else mtime, e))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [s[2] for s in scored[:limit]]

    @staticmethod
    def human(b):
        for unit, n in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
            if b >= n:
                return f"{b / n:.1f} {unit}"
        return f"{b} B"

    def format(self, hits) -> str:
        lines = []
        for path, _, size, mtime, is_dir in hits:
            if is_dir:
                lines.append(f"[folder] {path}")
            else:
                lines.append(f"{path}  ({self.human(size)}, {datetime.fromtimestamp(mtime):%d %b %Y %H:%M})")
        return "\n".join(lines)

    def overview(self) -> str:
        home = Path.home()
        names = []
        try:
            for e in sorted(os.scandir(home), key=lambda e: e.name.lower()):
                if e.is_dir() and not e.name.startswith((".", "$")) and e.name.lower() not in SKIP:
                    names.append(e.name)
        except OSError:
            pass
        drv = ", ".join(str(d) for d in drives()) if os.name == "nt" else "/"
        return f"Home folder {home} contains: {', '.join(names[:40])}. Drives: {drv}."
