"""The app's brain: settings, models, the chat with its tool loop, and background jobs."""
import json
import os
import platform
import re
import threading
import time
from pathlib import Path

from . import config, hardware, hf, runner, tools
from .browser import Browser
from .engine import Engine, available_backends
from .fileindex import FileIndex
from .models import ModelStore

FILE_WORDS = re.compile(
    r"\b(files?|folders?|director(y|ies)|my (drive|disk|pc|computer|laptop)|downloads?|desktop|documents?|"
    r"photos?|pictures?|images?|screenshots?|videos?|music|songs?|audio|pdfs?|spreadsheets?|exe|zip|"
    r"on my (pc|computer|laptop)|in my \w+ folder)\b", re.I)
WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s\"'`<>|*?]*")
WEB_WORDS = re.compile(r"\b(web|online|internet|browser|browse|website|site|google|url|http|https|www|news|weather|price|prices)\b", re.I)
NIX_PATH = re.compile(r"(?<![\w])/(?:home|Users|mnt|tmp|var|opt)/[^\s\"'`]+")


class App:
    def __init__(self):
        self.settings = config.Settings()
        self.store = ModelStore(self.settings)
        self.engine = Engine()
        self.index = FileIndex()
        self.browser = Browser(on_change=self.touch)
        self.pc = None
        self.lock = threading.RLock()
        self.version = 0
        self.messages: list[dict] = []
        self.next_id = 1
        self.generating = False
        self.running_code = False
        self.loading = False
        self.status = None            # one-line engine / app status
        self.preview = None           # html page to show (only when the browser isn't available)
        self.browser_focus = 0        # bumps when code ran in the browser, so the UI can show it
        self.steps_left = 0
        self.done_lookups = {}
        self.repeats = 0
        self.blocked_sites = set()
        self.search_state = {"query": "", "results": [], "busy": False, "error": None}
        self.repo_state = {"repo": None, "files": [], "busy": False, "error": None}
        self.recs = {"items": [], "busy": False, "summary": None}
        self.chat_file = config.data_dir() / "chat.json"
        self._load_chat()
        threading.Thread(target=self._startup, daemon=True).start()

    # ---------- helpers ----------
    def touch(self):
        self.version += 1

    def _startup(self):
        self.pc = hardware.scan()
        self.touch()
        self.index.build()
        self.touch()

    def state(self) -> dict:
        pc = self.pc
        return {
            "version": self.version,
            "settings": self.settings.public(),
            "pc": pc.to_dict() if pc else None,
            "backends": sorted(available_backends()),
            "models": self.store.local_models(),
            "downloads": self.store.download_list(),
            "loadedModel": Path(self.engine.model_path).name if self.engine.loaded else None,
            "loadedPath": self.engine.model_path if self.engine.loaded else None,
            "backend": self.engine.backend if self.engine.loaded else None,
            "gpuInfo": self.engine.gpu_summary() if self.engine.loaded else "",
            "loading": self.loading,
            "status": self.status,
            "generating": self.generating,
            "runningCode": self.running_code,
            "preview": self.preview,
            "search": self.search_state,
            "repo": self.repo_state,
            "recs": self.recs,
            "tools": runner.tools(),
            "browser": self.browser.state(),
            "browserFocus": self.browser_focus,
            "index": {"ready": self.index.ready, "building": self.index.building, "count": len(self.index.entries)},
            "workDir": str(config.work_dir()),
            "modelsDir": str(self.store.dir),
            "home": str(Path.home()),
        }

    # ---------- settings ----------
    def update_settings(self, values: dict):
        allowed = {"nCtx": int, "threads": int, "gpuLayers": int, "backend": str, "temperature": float,
                   "maxTokens": int, "autoApprove": bool, "browserAutoApprove": bool, "hfToken": str}
        for k, v in values.items():
            if k in allowed:
                self.settings.set(k, allowed[k](v))
        self.touch()

    def token(self):
        return self.settings.get("hfToken") or None

    # ---------- models ----------
    def _bg(self, fn):
        def wrap():
            try:
                fn()
            finally:
                self.touch()
        threading.Thread(target=wrap, daemon=True).start()

    def scan_pc(self):
        if self.recs["busy"]:
            return

        def job():
            self.recs["busy"] = True
            self.recs["summary"] = "Checking your PC…"
            self.touch()
            try:
                self.pc = hardware.scan(refresh=True)
                found = self.store.scan_pc()
                self.recs["summary"] = (f"Found {found} uncensored model file(s) already on your PC. "
                                        f"Finding the best uncensored models for your hardware…")
                self.touch()
                items = hf.recommend(self.pc, self.token())
                self.recs["items"] = items
                self.recs["summary"] = (f"Found {found} uncensored model file(s) on your PC. " +
                                        ("Couldn't reach Hugging Face or nothing fits. Check your internet connection."
                                         if not items else "Top picks for your PC are below."))
            except Exception as e:
                self.recs["summary"] = f"Scan failed: {e}"
            finally:
                self.recs["busy"] = False
        self._bg(job)

    def search(self, query: str):
        def job():
            s = self.search_state
            s.update(query=query, busy=True, error=None, results=[])
            self.repo_state.update(repo=None, files=[])
            self.touch()
            try:
                s["results"] = hf.search(query, self.token())
                if not s["results"]:
                    s["error"] = f'No uncensored GGUF models found for "{query}"'
            except Exception as e:
                s["error"] = f"Search failed: {e}"
            finally:
                s["busy"] = False
        self._bg(job)

    def open_repo(self, repo: str | None):
        def job():
            r = self.repo_state
            r.update(repo=repo, files=[], busy=bool(repo), error=None)
            self.touch()
            if not repo:
                return
            try:
                files = hf.gguf_files(repo, self.token())
                r["files"] = [f.to_dict() for f in files]
                if not files:
                    r["error"] = f"No GGUF model files in {repo}"
            except Exception as e:
                r["error"] = f"Couldn't list files: {e}"
            finally:
                r["busy"] = False
        self._bg(job)

    def download(self, file: dict):
        msg = self.store.download(file, self.token(), hardware.scan().freeDisk,
                                  on_done=lambda n: self._set_status(f"Downloaded {n}. Load it on the Home tab."))
        self._set_status(msg)

    def _set_status(self, s):
        self.status = s
        self.touch()

    def load_model(self, path: str, wait=False):
        if self.loading:
            return False
        m = self.store.find(path)
        if not m:
            self._set_status("That model file is gone.")
            return False
        result = {"ok": False}

        def job():
            self.loading = True
            self._set_status(f"Loading {m['name']}…")
            try:
                s = self.settings
                msg = self.engine.load(m["path"], self.pc or hardware.scan(), s.get("nCtx"), s.get("threads"),
                                       s.get("gpuLayers"), s.get("backend"), on_status=self._set_status)
                s.set("lastModel", m["path"])
                self._set_status(msg)
                result["ok"] = True
            except Exception as e:
                self._set_status(str(e))
            finally:
                self.loading = False
                self.touch()
        if wait:
            job()
            return result["ok"]
        self._bg(job)
        return True

    def unload(self):
        self.engine.unload()
        self._set_status("Model unloaded")

    # ---------- chat ----------
    def _msg(self, role, text, model_text=None):
        with self.lock:
            m = {"id": self.next_id, "role": role, "text": text, "modelText": model_text, "stats": None, "actions": []}
            self.next_id += 1
            self.messages.append(m)
            self.touch()
            return m

    def clear_chat(self):
        if self.generating:
            return
        with self.lock:
            self.messages.clear()
            self.touch()
        self.chat_file.unlink(missing_ok=True)

    def stop(self):
        self.stop_requested = True
        self.engine.stop()

    def send(self, text: str, attachment: str | None = None):
        text = (text or "").strip()
        if not text or self.generating or self.loading:
            return
        model_text, display = None, text
        if attachment:
            try:
                body = Path(attachment).read_text(encoding="utf-8", errors="replace")[:16000]
            except Exception as e:
                body = f"(couldn't read file: {e})"
            model_text = f"{text}\n\nFile {attachment}:\n```\n{body}\n```"
            display = f"📎 {Path(attachment).name}\n{text}"
        user = self._msg("user", display, model_text)
        self.steps_left = 8
        self.done_lookups = {}   # tool calls already made for this request -> result
        self.repeats = 0
        self.blocked_sites = set()
        self.generating = True
        self.touch()

        def job():
            try:
                if not self.engine.loaded:
                    last = self.settings.get("lastModel")
                    models = self.store.local_models()
                    pick = next((m for m in models if m["path"] == last), None) or (models[0] if len(models) == 1 else None)
                    if not pick:
                        self._msg("note", "Load a model on the Home tab first.")
                        return
                    self._msg("note", f"Loading {pick['name']}…")
                    self.generating = False
                    if not self.load_model(pick["path"], wait=True):
                        self._msg("note", self.status or "Couldn't load the model.")
                        return
                    self.generating = True
                if WIN_PATH.search(text) or NIX_PATH.search(text) or (FILE_WORDS.search(text) and not WEB_WORDS.search(text)):
                    ctx = self._file_context(text)
                    if ctx:
                        user["modelText"] = (model_text or text) + "\n\n[PC files - found automatically by the app]\n" + ctx
                        self._msg("tool", ctx)
                self._agent_loop()
            except Exception as e:
                self._msg("note", str(e))
            finally:
                self.generating = False
                self.save_chat()
                self.touch()
        threading.Thread(target=job, daemon=True).start()

    def _file_context(self, text: str) -> str:
        self.index.ensure_fresh(wait=True)
        parts, used = [], set()
        for m in list(WIN_PATH.finditer(text)) + list(NIX_PATH.finditer(text)):
            p = Path(m.group(0).rstrip(".,)?!"))
            if str(p) in used:
                continue
            used.add(str(p))
            if p.is_dir():
                parts.append(tools.execute(tools.Action("list", str(p)), self.index))
            elif p.is_file():
                parts.append(tools.execute(tools.Action("read", str(p)), self.index))
        lower = text.lower()
        home = Path.home()
        try:
            for d in os.scandir(home):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                n = d.name.lower()
                if re.search(rf"\b{re.escape(n)}s?\b", lower) or (n == "downloads" and "download" in lower):
                    if d.path not in used:
                        used.add(d.path)
                        parts.append(tools.execute(tools.Action("list", d.path), self.index))
        except OSError:
            pass
        hits = [h for h in self.index.search(text, 25) if h[0] not in used]
        if hits:
            parts.append("Best matches on the PC:\n" + self.index.format(hits))
        if not parts:
            parts.append("Nothing matched by name. " + self.index.overview())
        return "\n\n".join(parts)[:12000]

    CHARS_PER_TOKEN = 2.6     # web pages and code tokenise worse than prose; stay on the safe side
    OLD_RESULT_CHARS = 1200   # older tool results are cut to this; the newest one stays whole

    def _history(self, shrink: float = 1.0) -> list[dict]:
        s = self.settings
        langs = sorted(runner.tools())
        overview = "PC overview: " + self.index.overview()
        sys_prompt = tools.system_prompt(str(Path.home()), str(config.work_dir()), langs, overview,
                                         f"{platform.system()} {platform.release()}")
        reply_room = min(s.get("maxTokens"), max(512, s.get("nCtx") // 3))
        tokens = s.get("nCtx") - reply_room - len(sys_prompt) / self.CHARS_PER_TOKEN - 300
        budget = int(max(600, tokens * self.CHARS_PER_TOKEN * shrink))
        picked, used, newest_result = [], 0, True
        for m in reversed(self.messages):
            role = m["role"]
            if role == "user":
                msg = {"role": "user", "content": m["modelText"] or m["text"]}
            elif role == "assistant":
                if not m["text"].strip():
                    continue
                msg = {"role": "assistant", "content": m["text"]}
            elif role in ("tool", "run"):
                body = m["text"] if role == "tool" else (m["modelText"] or m["text"])
                if not newest_result and len(body) > self.OLD_RESULT_CHARS:
                    body = body[:self.OLD_RESULT_CHARS] + "\n…[older result cut]"
                newest_result = False
                msg = {"role": "user", "content": ("[Tool results]\n" if role == "tool" else "[Code output]\n") + body}
            else:
                continue
            if used + len(msg["content"]) > budget:
                if picked:
                    break
                msg["content"] = msg["content"][:budget] + "\n…[cut to fit the model's memory]"
            used += len(msg["content"])
            picked.insert(0, msg)
        # Merge consecutive same-role turns: many chat templates require alternating roles.
        merged = []
        for msg in picked:
            if merged and merged[-1]["role"] == msg["role"]:
                merged[-1] = {"role": msg["role"], "content": merged[-1]["content"] + "\n\n" + msg["content"]}
            else:
                merged.append(msg)
        while merged and merged[0]["role"] != "user":
            merged.pop(0)
        if not merged:
            last = self._last_user_text()[:budget]
            merged = [{"role": "user", "content": last or "Hello"}]
        return [{"role": "system", "content": sys_prompt}] + merged

    def _chat(self, on_piece, reply):
        """Asks the model, shrinking the history and retrying if it doesn't fit in the context."""
        s = self.settings
        reply_room = min(s.get("maxTokens"), max(512, s.get("nCtx") // 3))
        for shrink in (1.0, 0.6, 0.35, 0.15):
            try:
                return self.engine.chat(self._history(shrink), reply_room, s.get("temperature"), on_piece)
            except RuntimeError as e:
                if "context" not in str(e).lower() or shrink == 0.15:
                    raise
                reply["text"] = ""
        return ""

    def _last_user_text(self):
        for m in reversed(self.messages):
            if m["role"] == "user":
                return m["modelText"] or m["text"]
        return ""

    def _agent_loop(self):
        self.stop_requested = False
        s = self.settings
        while self.steps_left > 0:
            self.steps_left -= 1
            reply = self._msg("assistant", "")

            def on_piece(p, reply=reply):
                reply["text"] += p
                self.version += 1
            full = self._chat(on_piece, reply)
            reply["text"] = tools.strip_thinking(full) or ("(stopped)" if self.stop_requested else "(no reply)")
            reply["stats"] = f"{self.engine.last_tps:.1f} tokens/sec"
            self.touch()
            if self._looks_broken(reply["text"]):
                self._msg("note", "This reply looks like gibberish. The model file may be too heavily compressed (Q2/Q3) "
                                  "or broken. Download a Q4_K_M or better version from the Models tab.")
                break
            if self.stop_requested:
                self._msg("note", "Stopped.")
                break
            actions = tools.parse(full)
            if not any(a.kind == "run" for a in actions):
                fb = tools.fallback_run(self._last_user_text(), full)
                if fb:
                    actions.append(fb)
            if not actions:
                if re.search(r"<run\b", full, re.I):
                    self._msg("tool", "Your <run> tag had no code in it. Put the whole program between "
                                      "<run lang=\"...\"> and </run>.")
                    continue
                break
            cont = False
            auto = s.get("autoApprove")
            browser_auto = auto or s.get("browserAutoApprove")
            results = []
            blocked = False   # a browser step waits for approval; later browser steps depend on it
            for a in actions:
                key = (a.kind, a.path, a.content)
                site = tools.site_of(a.path) if a.kind in ("browse", "fetch") else ""
                if ((a.read_only or a.browser_read) and key in self.done_lookups) or (site and site in self.blocked_sites):
                    self.repeats += 1
                    results.append(f"You already did this ({a.label}) or that site blocked you. "
                                   "Do not repeat it. Use what you have and answer the user now.")
                    continue
                if a.read_only:
                    results.append(tools.execute(a, self.index))
                    self.done_lookups[key] = True
                    continue
                if a.is_browser:
                    if blocked:
                        continue
                    if a.browser_read:
                        self._set_status(a.label + "…")
                        r = tools.execute_browser(a, self.browser)
                        if site and "[LocalForge note]" in r:
                            self.blocked_sites.add(site)
                        results.append(r)
                        if a.kind != "scroll":
                            self.done_lookups[key] = True
                        continue
                    pa = {"id": len(reply["actions"]), "action": a.to_dict(), "status": "pending", "result": None}
                    reply["actions"].append(pa)
                    self.touch()
                    if browser_auto:
                        pa["status"] = "done"
                        r = tools.execute_browser(a, self.browser)
                        pa["result"] = self._short(r)
                        results.append(r)
                    else:
                        blocked = True
                    continue
                pa = {"id": len(reply["actions"]), "action": a.to_dict(), "status": "pending", "result": None}
                reply["actions"].append(pa)
                self.touch()
                if a.kind == "run" and auto:
                    pa["status"] = "done"
                    self._execute_run(a, pa)
                    cont = True
                elif auto and a.kind in ("write", "mkdir"):
                    pa["status"] = "done"
                    pa["result"] = tools.execute(a, self.index)
                elif auto and a.kind == "open" and not a.path.lower().endswith(tools.RISKY_OPEN):
                    pa["status"] = "done"
                    pa["result"] = tools.execute(a, self.index)
            if self.repeats >= 2 and self.steps_left > 1:
                results.append("STOP using tools now. Write your final answer to the user from the results above. "
                               "If you couldn't get the information, say so and share what you did find.")
                self.steps_left = 1
            if results:
                self._msg("tool", "\n\n".join(results))
                cont = True
            if not cont:
                break
            if self.steps_left == 0:
                self._msg("note", 'Paused after several steps. Say "continue" to keep going.')

    def _execute_run(self, a: tools.Action, pa=None):
        out = self._msg("run", f"Running {a.path}…")
        self.running_code = True
        self.touch()
        try:
            r = self._run_in_browser(a) if a.path in ("javascript", "html") else None
            if r is None:
                r = runner.run(a.path, a.content)
        finally:
            self.running_code = False
        out["text"] = ("✓ " if r.ok else "⏱ " if r.timed_out else "✗ ") + f"{r.lang}\n{r.output}"
        out["modelText"] = r.for_model()
        if r.preview:
            self.preview = r.preview
        if a.path in ("javascript", "html") and r.lang.endswith("(browser)"):
            self.browser_focus += 1
        if pa is not None:
            pa["result"] = "Ran successfully" if r.ok else "Stopped: took too long" if r.timed_out else "Failed - see output"
        self.touch()

    def _run_in_browser(self, a: tools.Action):
        """javascript and html run inside LocalForge's own browser so the model can see and test the result."""
        try:
            self.browser.ensure()
        except Exception as e:
            if a.path == "javascript" and runner.tools().get("node"):
                return runner.run("node", a.content)
            if a.path == "html":
                return None    # falls back to the in-app preview
            return runner.RunResult("javascript", False, False, f"The browser isn't available ({e}).")
        if a.path == "javascript":
            ok, out = self.browser.run_code(a.content)
            return runner.RunResult("javascript (browser)", ok, False, runner._trim(out))
        d = config.work_dir() / "pages"
        d.mkdir(parents=True, exist_ok=True)
        m = re.search(r"<title>(.*?)</title>", a.content, re.I | re.S)
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", (m.group(1) if m else "page").strip())[:40].strip("-") or "page"
        f = d / f"{name}-{int(time.time())}.html"
        f.write_text(a.content, encoding="utf-8")
        report = self.browser.open_page(str(f))
        ok = "[error]" not in report and "Uncaught" not in report
        return runner.RunResult("html (browser)", ok, False,
                                f"Saved {f} and opened it in the browser.\n\n" + runner._trim(report))

    def _find_action(self, msg_id: int, idx: int):
        for m in self.messages:
            if m["id"] == msg_id and 0 <= idx < len(m["actions"]):
                return m["actions"][idx]
        return None

    def approve(self, msg_id: int, idx: int):
        pa = self._find_action(msg_id, idx)
        if not pa or pa["status"] != "pending":
            return
        d = pa["action"]
        a = tools.Action(d["kind"], d["path"], d["content"], d.get("extra", ""))
        if self.generating:
            pa["result"] = "Wait for the current reply to finish."
            self.touch()
            return
        pa["status"] = "done"
        self.generating = True
        self.touch()
        msg = next((m for m in self.messages if m["id"] == msg_id), None)

        def job():
            try:
                if a.is_browser:
                    r = tools.execute_browser(a, self.browser)
                    pa["result"] = self._short(r)
                    self._msg("tool", r)
                elif a.kind == "run":
                    self._execute_run(a, pa)
                else:
                    pa["result"] = tools.execute(a, self.index)
                    self._msg("tool", pa["result"])
                # Carry on once every card in that reply has been answered.
                waiting = msg and any(x["status"] == "pending" for x in msg["actions"])
                if self.engine.loaded and not waiting:
                    if self.steps_left <= 0:
                        self.steps_left = 3
                    self._agent_loop()
            except Exception as e:
                self._msg("note", str(e))
            finally:
                self.generating = False
                self.save_chat()
                self.touch()
        threading.Thread(target=job, daemon=True).start()

    def reject(self, msg_id: int, idx: int):
        pa = self._find_action(msg_id, idx)
        if pa and pa["status"] == "pending":
            pa["status"] = "rejected"
            self.save_chat()
            self.touch()

    def run_snippet(self, lang: str, code: str):
        def job():
            self._execute_run(tools.Action("run", runner.normalize(lang), code))
            self.save_chat()
        threading.Thread(target=job, daemon=True).start()

    # ---------- persistence ----------
    def save_chat(self):
        try:
            data = [m for m in self.messages if not (m["role"] == "assistant" and not m["text"].strip())]
            self.chat_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _load_chat(self):
        try:
            data = json.loads(self.chat_file.read_text(encoding="utf-8"))
            for m in data:
                for a in m.get("actions", []):
                    if a.get("status") == "pending":
                        a["status"] = "rejected"
                        a["result"] = "Not run (app was closed)"
            self.messages = data
            self.next_id = max([m["id"] for m in data] + [0]) + 1
        except Exception:
            pass

    @staticmethod
    def _short(result: str) -> str:
        """One line for an action card: the error, or the page the browser ended up on."""
        first = result.split("\n", 1)[0]
        if first.startswith("Browser error") or first.startswith("Script result"):
            return result[:200]
        m = re.search(r"Browser page: (.*)", result)
        return f"{first.rstrip(':').replace(' Now', '')} - now on: {m.group(1)[:120]}" if m else first[:200]

    @staticmethod
    def _looks_broken(text: str) -> bool:
        if len(text) < 60:
            return False
        prose = re.sub(r"```[\s\S]*?(```|$)", "", text)
        prose = re.sub(r"<(write|run)[\s\S]*?(</\1>|$)", "", prose)
        if len(prose) < 60:
            return False
        letters = sum(1 for c in prose if c.isalpha() or c == " ")
        return letters / len(prose) < 0.5
