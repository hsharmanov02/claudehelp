"""Runs code the assistant writes, using the bundled Python or the tools installed on this PC."""
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from pathlib import Path

from . import config

TIMEOUT = 60
COMPILE_TIMEOUT = 180
MAX_OUTPUT = 8000
WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if WIN else 0


@dataclass
class RunResult:
    lang: str
    ok: bool
    timed_out: bool
    output: str
    preview: str | None = None   # path of an HTML page to show

    def for_model(self) -> str:
        if self.preview:
            head = "Your HTML page was saved and is now shown to the user."
        elif self.timed_out:
            head = f"Your {self.lang} code was stopped because it took too long."
        elif self.ok:
            head = f"Your {self.lang} code ran successfully. Output:"
        else:
            head = f"Your {self.lang} code failed. Output and error:"
        return f"{head}\n```\n{self.output}\n```"


def _which(*names):
    for n in names:
        p = shutil.which(n)
        # The Microsoft Store "python.exe" stub only opens the Store.
        if p and "windowsapps" not in p.lower():
            return p
    return None


def system_python():
    return _which("python", "python3", "py")


def tools() -> dict:
    """Which languages work on this PC right now, and how."""
    t = {
        "python": system_python() or "built in",
        "sql": "built in (SQLite)",
        "html": "built in (preview)",
    }
    if WIN:
        t["powershell"] = _which("pwsh", "powershell") or "powershell"
        t["cmd"] = "cmd"
    else:
        t["shell"] = "sh"
    opt = {
        "javascript": _which("node"), "typescript": _which("deno", "bun") or (_which("npx") and "npx tsx"),
        "bash": _which("bash"), "c": _which("gcc", "clang", "cl"), "cpp": _which("g++", "clang++", "cl"),
        "java": _which("java"), "go": _which("go"), "rust": _which("rustc"), "ruby": _which("ruby"),
        "php": _which("php"), "lua": _which("lua", "lua54", "luajit"), "perl": _which("perl"),
        "kotlin": _which("kotlinc"), "csharp": _which("dotnet") and None,
    }
    t.update({k: v for k, v in opt.items() if v})
    return t


def normalize(lang: str | None) -> str:
    l = (lang or "").strip().lower()
    table = {
        "": "python", "py": "python", "python3": "python", "py3": "python",
        "js": "javascript", "jsx": "javascript", "mjs": "javascript", "node": "javascript", "nodejs": "javascript",
        "ts": "typescript", "tsx": "typescript",
        "htm": "html", "xhtml": "html", "web": "html",
        "sqlite": "sql", "sqlite3": "sql",
        "ps": "powershell", "ps1": "powershell", "pwsh": "powershell",
        "bat": "cmd", "batch": "cmd", "cmd": "cmd",
        "sh": "shell", "shell": "shell", "terminal": "shell", "console": "shell", "zsh": "shell", "shellscript": "shell",
        "c++": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp",
        "kt": "kotlin", "kts": "kotlin", "golang": "go", "rs": "rust", "rb": "ruby", "pl": "perl",
    }
    l = table.get(l, l)
    if l == "shell":
        return "powershell" if WIN else "shell"
    return l


RUNNABLE_FENCES = {"python", "py", "python3", "sh", "bash", "shell", "powershell", "ps1", "pwsh", "bat", "cmd", "batch",
                   "js", "javascript", "node", "ts", "typescript", "html", "sql", "sqlite", "c", "cpp", "c++", "java",
                   "kotlin", "kt", "go", "golang", "rust", "rs", "ruby", "rb", "php", "lua", "perl"}


def _trim(t: str) -> str:
    if len(t) > MAX_OUTPUT:
        return t[: MAX_OUTPUT // 2] + "\n...[output trimmed]...\n" + t[-MAX_OUTPUT // 2:]
    return t


def _kill_tree(p: subprocess.Popen):
    try:
        if WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
        else:
            p.kill()
    except Exception:
        pass


def _exec(cmd, cwd: Path, timeout: int, lang: str, shell=False) -> RunResult:
    try:
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, shell=shell)
    except FileNotFoundError:
        return RunResult(lang, False, False, f"{lang} isn't installed on this PC.")
    try:
        out, _ = p.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        out, _ = p.communicate()
        timed_out = True
    text = (out or b"").decode("utf-8", "replace")
    if WIN and "�" in text:
        try:
            text = (out or b"").decode("mbcs", "replace")
        except Exception:
            pass
    text = _trim(text.replace("\r\n", "\n").rstrip())
    if timed_out:
        text += f"\n[Stopped: ran longer than {timeout}s]"
    elif p.returncode != 0:
        text += f"\n[exit code {p.returncode}]"
    return RunResult(lang, p.returncode == 0 and not timed_out, timed_out, text.strip() or "(no output)")


def _python(code: str, wd: Path) -> RunResult:
    d = wd / "run-python"
    d.mkdir(parents=True, exist_ok=True)
    script = d / "main.py"
    script.write_text(code, encoding="utf-8")
    py = system_python()
    if py:
        cmd = [py, "-X", "utf8", str(script)] if not py.lower().endswith("py.exe") else [py, "-3", "-X", "utf8", str(script)]
        env_r = _exec(cmd, wd, TIMEOUT, "python")
        return env_r
    # Bundled Python: the program itself runs the script and writes the output to a file.
    out_file = d / "output.txt"
    out_file.unlink(missing_ok=True)
    if config.frozen():
        cmd = [sys.executable, "--run-python", str(script), str(out_file)]
    else:
        cmd = [sys.executable, "-m", "localforge.main", "--run-python", str(script), str(out_file)]
    r = _exec(cmd, wd, TIMEOUT, "python")
    text = out_file.read_text(encoding="utf-8", errors="replace").rstrip() if out_file.exists() else ""
    ok = r.ok and "Traceback (most recent call last)" not in text
    if r.timed_out:
        text += f"\n[Stopped: ran longer than {TIMEOUT}s]"
    return RunResult("python", ok, r.timed_out, _trim(text).strip() or "(no output)")


def run_python_inline(script: str, out_file: str) -> int:
    """Entry point for the bundled Python (LocalForge.exe --run-python script out)."""
    import runpy
    import traceback
    buf = open(out_file, "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = buf
    sys.stdin = io.StringIO("")
    sys.argv = [script]
    sys.path.insert(0, str(Path(script).parent))
    code = 0
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        traceback.print_exc()
        code = 1
    buf.flush()
    return code


def _sql(code: str, wd: Path) -> RunResult:
    db = wd / "localforge.db"
    out = []
    try:
        con = sqlite3.connect(str(db))
        cur = con.cursor()
        stmts = [s for s in _split_sql(code) if s.strip()]
        for s in stmts:
            cur.execute(s)
            if cur.description:
                cols = [c[0] for c in cur.description]
                rows = cur.fetchmany(200)
                out.append(" | ".join(cols))
                out.append("-" * min(80, len(out[-1])))
                out += [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows]
                out.append("")
        con.commit()
        con.close()
        return RunResult("sql", True, False, _trim("\n".join(out).strip()) or f"OK ({len(stmts)} statement(s)) — database {db}")
    except Exception as e:
        return RunResult("sql", False, False, "\n".join(out + [f"SQL error: {e}"]))


def _split_sql(code: str):
    parts, cur, quote = [], [], None
    for ch in code:
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == ";":
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return parts


SPECS = {
    # lang: (file name, command builder, compile?)
    "javascript": ("main.js", lambda t, f: [t, f], False),
    "bash": ("main.sh", lambda t, f: [t, f], False),
    "shell": ("main.sh", lambda t, f: ["sh", f], False),
    "ruby": ("main.rb", lambda t, f: [t, f], False),
    "php": ("main.php", lambda t, f: [t, f], False),
    "lua": ("main.lua", lambda t, f: [t, f], False),
    "perl": ("main.pl", lambda t, f: [t, f], False),
    "go": ("main.go", lambda t, f: [t, "run", f], True),
    "java": ("Main.java", lambda t, f: [t, f], True),
}


def run(lang: str, code: str, wd: Path | None = None) -> RunResult:
    lang = normalize(lang)
    wd = wd or config.work_dir()
    wd.mkdir(parents=True, exist_ok=True)
    try:
        if lang == "python":
            return _python(code, wd)
        if lang == "sql":
            return _sql(code, wd)
        if lang == "html":
            d = wd / "pages"
            d.mkdir(exist_ok=True)
            f = d / f"page-{int(time.time())}.html"
            f.write_text(code, encoding="utf-8")
            return RunResult("html", True, False, f"Saved {f}", preview=str(f))
        avail = tools()
        d = wd / f"run-{lang}"
        if lang == "powershell":
            d.mkdir(parents=True, exist_ok=True)
            f = d / "main.ps1"
            f.write_text("$ProgressPreference='SilentlyContinue'\n" + code, encoding="utf-8-sig")
            exe = _which("pwsh", "powershell") or "powershell"
            return _exec([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(f)], wd, TIMEOUT, lang)
        if lang == "cmd":
            d.mkdir(parents=True, exist_ok=True)
            f = d / "main.bat"
            f.write_text("@echo off\r\n" + code.replace("\r\n", "\n").replace("\n", "\r\n"), encoding="utf-8")
            return _exec(["cmd", "/c", str(f)], wd, TIMEOUT, lang)
        if lang not in avail:
            have = ", ".join(sorted(avail))
            return RunResult(lang, False, False,
                             f"{lang} isn't installed on this PC. Languages that work here: {have}. "
                             f"Rewrite it in one of those, or the user can install {lang} and try again.")
        tool = avail[lang]
        d.mkdir(parents=True, exist_ok=True)
        if lang in SPECS:
            name, build, compiled = SPECS[lang]
            if lang == "java":
                m = re.search(r"public\s+(?:final\s+)?class\s+(\w+)", code)
                name = f"{m.group(1) if m else 'Main'}.java"
            f = d / name
            f.write_text(code, encoding="utf-8")
            return _exec(build(tool, str(f)), d, COMPILE_TIMEOUT if compiled else TIMEOUT, lang)
        if lang == "typescript":
            f = d / "main.ts"
            f.write_text(code, encoding="utf-8")
            if tool.endswith("tsx"):
                return _exec(f"npx --yes tsx \"{f}\"", d, COMPILE_TIMEOUT, lang, shell=True)
            if "deno" in tool.lower():
                return _exec([tool, "run", "-A", str(f)], d, COMPILE_TIMEOUT, lang)
            return _exec([tool, str(f)], d, COMPILE_TIMEOUT, lang)
        if lang in ("c", "cpp", "rust", "kotlin"):
            src = {"c": "main.c", "cpp": "main.cpp", "rust": "main.rs", "kotlin": "main.kt"}[lang]
            f = d / src
            f.write_text(code, encoding="utf-8")
            exe = d / ("main.exe" if WIN else "main")
            if lang == "rust":
                comp = [tool, str(f), "-o", str(exe)]
            elif lang == "kotlin":
                jar = d / "main.jar"
                comp = [tool, str(f), "-include-runtime", "-d", str(jar)]
            elif Path(tool).stem.lower() == "cl":
                comp = [tool, "/nologo", "/EHsc", str(f), f"/Fe:{exe}"]
            else:
                comp = [tool, str(f), "-o", str(exe)] + (["-std=c++17"] if lang == "cpp" else ["-lm"])
            c = _exec(comp, d, COMPILE_TIMEOUT, lang)
            if not c.ok:
                c.output = "Compile failed:\n" + c.output
                return c
            if lang == "kotlin":
                return _exec([_which("java") or "java", "-jar", str(d / "main.jar")], d, TIMEOUT, lang)
            r = _exec([str(exe)], d, TIMEOUT, lang)
            return r
        return RunResult(lang, False, False, f"Don't know how to run {lang}.")
    except Exception as e:
        return RunResult(lang, False, False, f"Couldn't run {lang}: {e}")
