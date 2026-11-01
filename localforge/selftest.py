"""End-to-end check used by the build: engines, Hugging Face, a real tiny model, code running and tools."""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from . import config, hardware, hf, runner, tools
from .engine import Engine, available_backends


def run() -> int:
    log_path = Path(os.environ.get("LOCALFORGE_SELFTEST_LOG", "selftest.txt")).resolve()
    log = open(log_path, "w", encoding="utf-8", buffering=1)

    def say(*a):
        line = " ".join(str(x) for x in a)
        log.write(line + "\n")
        try:
            print(line, flush=True)
        except Exception:
            pass

    failures = []

    def check(name, fn, required=True):
        t0 = time.time()
        try:
            r = fn()
            say(f"PASS {name} ({time.time() - t0:.1f}s) {r if r is not None else ''}")
            return r
        except Exception as e:
            say(f"{'FAIL' if required else 'WARN'} {name}: {e}")
            say(traceback.format_exc())
            if required:
                failures.append(name)
            return None

    pc = check("hardware scan", lambda: hardware.scan())
    if pc:
        say("  ", pc.to_dict())
    check("engines bundled", lambda: (available_backends() or (_ for _ in ()).throw(RuntimeError("no engines"))))

    def tags():
        acts = tools.parse('Sure.\n<find query="cv pdf"/>\n<read path="C:\\Users\\me\\a.txt"/>\n<run lang="py">\nprint(1)\n</run>')
        assert [a.kind for a in acts] == ["find", "read", "run"], acts
        return [a.label for a in acts]
    check("tool tag parsing", tags)

    def bundled_python():
        if not config.frozen():
            return "skipped (not frozen)"
        d = config.work_dir() / "selftest"
        d.mkdir(parents=True, exist_ok=True)
        script, out = d / "t.py", d / "out.txt"
        script.write_text("import json, csv, sqlite3, random, datetime, collections\nprint('bundled', sum(range(10)))\n")
        import subprocess
        subprocess.run([sys.executable, "--run-python", str(script), str(out)], timeout=60)
        text = out.read_text()
        assert "bundled 45" in text, text
        return text.strip()
    check("bundled python runner", bundled_python)
    check("python runner", lambda: runner.run("python", "print(6*7)").output)
    check("powershell/shell runner", lambda: runner.run("powershell" if os.name == "nt" else "shell", "echo hi").output)
    check("sql runner", lambda: runner.run("sql", "select 1+1 as two;").output)

    def browser_test():
        from .browser import Browser
        b = Browser()
        try:
            b.navigate("data:text/html,<title>LF</title><input id=a placeholder=name><button onclick=\"document.title='clicked '+a.value\">Go</button>")
            d = b.describe()
            assert "[1]" in d and "[2]" in d, d
            b.fill(1, "forge")
            b.click_element(2)
            title = b.run_js("return document.title")
            assert title == "clicked forge", title
            ok, out = b.run_code("console.log([1, 2, 3].map(x => x * 2)); return 'done'")
            assert ok and "[2, 4, 6]" in out and "done" in out, out
            ok, out = b.run_code("undefinedThing()")
            assert not ok and "undefinedThing" in out, out
            b.navigate("https://example.com")
            text = b.snapshot()["text"]
            assert "domain" in text.lower(), text[:200]
            seq, frame = b.screenshot_wait(0, 5)
            assert frame, "no screen frames"
            return f"{b.exe} | {title} | example.com ok | {len(frame)} byte frame"
        finally:
            b.close()
    check("built-in browser (open, read, type, click, script)", browser_test)

    results = check("hugging face search", lambda: [r["id"] for r in hf.search("qwen", None)[:5]])
    if pc:
        check("recommendations", lambda: [(p["file"]["repo"], p["file"]["fileName"], p["reason"]) for p in hf.recommend(pc, None, 5)], required=False)

    # Real model: smallest decent uncensored GGUF we can find.
    def tiny_model():
        for q in ("0.5B abliterated", "0.6B abliterated", "1B abliterated"):
            for r in hf.search(q, None)[:8]:
                try:
                    files = hf.gguf_files(r["id"], None)
                except Exception:
                    continue
                ok = [f for f in files if f.quant in ("Q4_K_M", "Q8_0", "Q4_0") and 0 < f.size < 900 * 1024 ** 2 and len(f.parts) == 1]
                if ok:
                    return ok[0]
        raise RuntimeError("no tiny model found")
    f = check("find tiny model", tiny_model)
    if f:
        target = config.models_dir() / f.file_name

        def dl():
            ev = threading.Event()
            if not (target.exists() and target.stat().st_size == f.size):
                hf.download_part(f.repo, f.path, target, None, ev, lambda n, reset=False: None, f.size)
            assert target.stat().st_size == f.size, f"size {target.stat().st_size} != {f.size}"
            return f"{f.repo}/{f.file_name} {target.stat().st_size} bytes"
        check("download model", dl)
        if target.exists():
            eng = Engine()
            for backend in [b for b in ("cpu", "vulkan") if b in available_backends()] or list(available_backends()):
                def load_and_chat(backend=backend):
                    msg = eng.load(str(target), pc, 2048, 0, 0 if backend == "cpu" else -1, backend)
                    pieces = []
                    reply = eng.chat([{"role": "system", "content": "You are helpful."},
                                      {"role": "user", "content": "Say hello in five words."}], 48, 0.2, pieces.append)
                    eng.unload()
                    assert reply.strip(), "empty reply"
                    return f"{msg} | {eng.last_tps:.1f} tok/s | {reply.strip()[:80]!r}"
                check(f"load + chat ({backend})", load_and_chat, required=(backend == "cpu"))

    say("RESULT:", "OK" if not failures else "FAILED " + ", ".join(failures))
    log.close()
    return 0 if not failures else 1
