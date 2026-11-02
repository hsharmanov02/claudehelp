"""The app's brain: settings, models, the chat with its tool loop, and background jobs."""
import json
import os
import platform
import re
import threading
import time
import urllib.parse
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
WEB_WORDS = re.compile(r"\b(web|online|internet|browser|browse|website|site|google|url|http|https|www|news|weather|price|prices|"
                       r"bank|banking|nationwide|transactions?|statement)\b", re.I)
DATA_WORDS = re.compile(r"\b(extract|scrape|table|list|top \d+|prices?|cheapest|compare|rank(ed|ing)?|csv|"
                        r"deals?|stories|results|data|population|market cap|sorted?|how many)\b", re.I)
PRIVATE = re.compile(r"passw|passwd|creds?\b|credential|secret|token|api.?key|private.?key|seed.?phrase|wallet|\.env\b|\.pem\b|\.kdbx", re.I)


def _hide_private(listing: str) -> str:
    """Automatic file context never includes names that look like passwords or keys (the user can still ask)."""
    lines, hidden = [], 0
    for line in listing.split("\n"):
        if PRIVATE.search(line) and not line.startswith(("Contents of", "Best matches")):
            hidden += 1
            continue
        lines.append(line)
    if hidden:
        lines.append(f"({hidden} private-looking file name(s) left out)")
    return "\n".join(lines)


# The user is asking about an earlier message: "what was my last prompt", "what did I just ask"…
REFERS_BACK = re.compile(
    r"\b(previous|last|earlier|before|prior|first)\b.{0,30}\b(prompt|question|message|request|ask|asked|said|typed|wrote)|"
    r"what (did|was) i (just )?(ask|say|said|tell|type|write|want)|"
    r"\b(remind me what|you said|i said|i asked|i told you|i just asked)\b", re.I)
# A job to do on websites (not a quick lookup): gets more steps, a plan, saved notes and a progress block.
TASK_WORDS = re.compile(r"\b(go to|open|log ?in|sign ?in|fill|form|click|book|order|buy|add to (cart|basket)|checkout|apply|"
                        r"register|subscribe|post|reply|send|download|navigate|find .{0,40} on|compare|every|each|all the|"
                        r"step|then|and then|after that|analy[sz]e|scan|check|audit|transactions?|spending|save money|saving)\b", re.I)
URL_IN = re.compile(r"https?://|www\.|\b[\w-]+\.(com|org|net|io|co|uk|dev|ai|gov|edu)\b", re.I)
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
        self.moved = False         # clicked, typed or opened a page for this request
        self.move_nudged = False
        self.blocked_sites = set()
        self.ran_code = False
        self.failed_code = set()
        self.browser_task = False
        self.task_notes = []
        self.visited = []
        self.rethink = False
        self.stop_requested = False
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
                   "maxTokens": int, "think": bool, "thinkBrowser": bool, "autoApprove": bool, "browserAutoApprove": bool, "hfToken": str}
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
        self.queued = []
        if self.generating:
            self.stop()                  # stop the reply, then clear (used to be ignored silently)
            for _ in range(50):
                if not self.generating:
                    break
                time.sleep(0.1)
        with self.lock:
            self.messages.clear()
            self.touch()
        self.chat_file.unlink(missing_ok=True)

    def stop(self):
        self.stop_requested = True
        self.engine.stop()

    def send(self, text: str, attachment: str | None = None, on_page: bool = False):
        text = (text or "").strip()
        if not text:
            return
        if self.generating or self.loading:
            # Busy: keep it and send it as soon as the current reply finishes.
            self.queued = getattr(self, "queued", []) + [(text, attachment)]
            self._msg("note", "Got it. This will be sent when the current reply finishes.")
            return
        model_text, display = None, text
        if attachment:
            try:
                body = Path(attachment).read_text(encoding="utf-8", errors="replace")[:16000]
            except Exception as e:
                body = f"(couldn't read file: {e})"
            model_text = f"{text}\n\nFile {attachment}:\n```\n{body}\n```"
            display = f"📎 {Path(attachment).name}\n{text}"
        if on_page and getattr(self.browser, "running", False) and self.browser.url and self.browser.url != "about:blank":
            # Asked from the side panel next to the browser: the question is about the page that's open.
            model_text = (f"[I'm looking at this page in your browser: \"{self.browser.title}\" {self.browser.url}. "
                          "My request is about it. If I ask you to open/click something, read the page with <page/> and click it first; "
                          "for data, use <extract/>. Then click/type on it as needed.]\n"
                          + (model_text or text))
        user = self._msg("user", display, model_text)
        self.browser_task = bool(on_page and TASK_WORDS.search(text)) or bool(
            (WEB_WORDS.search(text) or URL_IN.search(text)) and TASK_WORDS.search(text))
        self.task_notes = []
        self.visited = []
        self.rethink = self.browser_task
        self.choice_checked = False      # plan the task with (capped) thinking on the first step
        self.steps_left = 30 if self.browser_task else 12
        self.total_steps = 0
        self.last_reply = None
        self.seen_urls = set()
        self.ran_code = False
        self.failed_code = set()
        self.done_lookups = {}   # tool calls already made for this request -> result
        self.repeats = 0
        self.moved = False
        self.move_nudged = False
        self.blocked_sites = set()
        self.ran_code = False
        self.failed_code = set()
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
                self._send_queued()
        threading.Thread(target=job, daemon=True).start()

    def _send_queued(self):
        if (self.status or "").endswith("…") and not self.loading:
            self.status = ""             # a finished step's "Clicking…" shouldn't linger
        q = getattr(self, "queued", [])
        if q and not self.generating:
            text, att = q.pop(0)
            self.send(text, att)

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
        # Relative paths like "Documents\LocalForge": list that exact folder, not its parent.
        for m in re.finditer(r"\b([A-Za-z][\w .-]*(?:[\\/][\w .-]+)+)", text):
            cand = m.group(1).strip().rstrip(".,)?!").strip()
            p = home / cand
            while not p.is_dir() and " " in cand.split("/")[0].split("\\")[0]:
                cand = cand.split(" ", 1)[1]          # drop leading words: "files in Documents\X" -> "Documents\X"
                p = home / cand
            if p.is_dir() and str(p) not in used:
                used.add(str(p))
                used.update(str(x) for x in p.parents)
                parts.append(tools.execute(tools.Action("list", str(p)), self.index))
        try:
            for d in os.scandir(home):
                if not d.is_dir() or d.name.startswith("."):
                    continue
                n = d.name.lower()
                if re.search(rf"\b{re.escape(n)}s?\b", lower) or (n == "downloads" and "download" in lower):
                    if d.path not in used and not any(u.startswith(d.path + os.sep) for u in used):
                        used.add(d.path)
                        parts.append(tools.execute(tools.Action("list", d.path), self.index))
        except OSError:
            pass
        hits = [] if parts else [h for h in self.index.search(text, 25) if h[0] not in used]
        if hits:
            parts.append("Best matches on the PC:\n" + self.index.format(hits))
        if not parts:
            parts.append("Nothing matched by name. " + self.index.overview())
        return _hide_private("\n\n".join(parts))[:12000]

    CHARS_PER_TOKEN = 2.6     # web pages and code tokenise worse than prose; stay on the safe side
    OLD_RESULT_CHARS = 1200   # older tool results are cut to this; the newest one stays whole

    def _history(self, shrink: float = 1.0, extra: str = "") -> list[dict]:
        s = self.settings
        langs = sorted(runner.tools())
        overview = "PC overview: " + self.index.overview()
        sys_prompt = tools.system_prompt(str(Path.home()), str(config.work_dir()), langs, overview,
                                         f"{platform.system()} {platform.release()}")
        think = bool(s.get("think"))
        reply_room = min(max(s.get("maxTokens"), 8192 if think else 0), max(512, s.get("nCtx") // (2 if think else 3)))
        tokens = s.get("nCtx") - reply_room - len(sys_prompt) / self.CHARS_PER_TOKEN - 300
        budget = int(max(600, tokens * self.CHARS_PER_TOKEN * shrink))
        picked, used, newest_result = [], 0, True
        current = next((m for m in reversed(self.messages) if m["role"] == "user"), None)
        recap = self._recap(current["text"]) if current else ""
        for m in reversed(self.messages):
            role = m["role"]
            if role == "user":
                msg = {"role": "user", "content": (m["modelText"] or m["text"]) + (recap if m is current else "")}
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
        elif merged[-1]["role"] == "user" and picked and re.match(r"\[(Tool results|Code output)\]", picked[-1]["content"]):
            # After tool output small models forget what was asked. Repeat the question.
            q = (self.messages_question() or "")[:600]
            if q and getattr(self, "browser_task", False):
                merged[-1] = {"role": "user", "content": merged[-1]["content"] + "\n\n" + self._progress(q)}
            elif q:
                merged[-1] = {"role": "user", "content": merged[-1]["content"] +
                              f"\n\n[The user's question: {q}]\nIf the results above answer it, answer it now, fully and directly."}
        if extra and merged and merged[-1]["role"] == "user":
            merged[-1] = {"role": "user", "content": merged[-1]["content"] + extra}
        return [{"role": "system", "content": sys_prompt}] + merged

    def _progress(self, q: str) -> str:
        """For website tasks: the goal, what's been found and where the browser has been, after every result.
        Old pages get cut to fit the model's memory; this keeps a long task on track."""
        lines = [f"[Task progress - the user's task: {q}]"]
        notes = getattr(self, "task_notes", [])
        if notes:
            lines.append("Your notes so far: " + " | ".join(notes[-15:]))
        visited = getattr(self, "visited", [])
        if visited:
            lines.append("Pages visited, oldest first: " + " -> ".join(f"{t[:40]} ({u[:80]})" for t, u in visited[-6:]))
        if visited:
            lines.append(f"The browser is on: {visited[-1][0][:60]} ({visited[-1][1][:100]}). Clicks act on THIS page only.")
        lines.append(f"Step {getattr(self, 'total_steps', 0) + 1}, {self.steps_left} left. If every part of the task is "
                     "done, give the final answer now from your notes and the pages. Otherwise do the next step.")
        return "\n".join(lines)

    RECAP_MESSAGES = 8

    def _recap(self, text: str) -> str:
        """When the user refers back ("what did I just ask?"), a note naming their previous message.

        Models given the whole chat still mix up the order: asked what came just before, they quote the
        first message, or the one that held facts. The note makes it unmistakable. It's only added when the
        user refers back, because on other turns extra text in front of small models sends them off course.
        """
        if not REFERS_BACK.search(text or ""):
            return ""
        users = [m for m in self.messages if m["role"] == "user"][:-1]
        if not users:
            return ""

        def short(m):
            t = re.sub(r"\s+", " ", m["text"]).strip()
            return t if len(t) <= 200 else t[:200] + "…"
        note = f'\n\n[Note from the app: my previous message, the one just before this one, was: "{short(users[-1])}"'
        earlier = users[:-1][-(self.RECAP_MESSAGES - 1):]
        if earlier:
            first = len(users) - len(earlier)
            note += ". My earlier messages, oldest first: " + " ".join(
                f'{first + i}. "{short(m)}"' for i, m in enumerate(earlier))
        return note + "]"

    PLAN_THINK = 1200      # thinking tokens to plan a website task (about 30 s at 40 tok/s)
    FIX_THINK = 600        # ...and to work out another way after a step failed

    def _chat(self, on_piece, reply):
        """Asks the model, shrinking the history and retrying if it doesn't fit in the context."""
        s = self.settings
        think = bool(s.get("think"))
        cap = 0
        if not think and getattr(self, "rethink", False) and s.get("thinkBrowser"):
            # Website tasks: think briefly to plan, and again when a step fails. Not on every step: too slow.
            think, cap = True, (self.PLAN_THINK if getattr(self, "total_steps", 0) == 0 else self.FIX_THINK)
        self.rethink = False
        reply_room = min(max(s.get("maxTokens"), 8192 if think and not cap else 0) + cap,
                         max(512, s.get("nCtx") // (2 if think else 3)))
        extra = ""
        for shrink in (1.0, 0.6, 0.35, 0.15):
            try:
                full = self.engine.chat(self._history(shrink, extra), reply_room, s.get("temperature"), on_piece, think, cap)
                if getattr(self.engine, "last_finish", "") != "think_cap" or self.stop_requested:
                    return full
                # Thought for long enough: give it its own thoughts and ask it to act on them, without thinking.
                thought = re.sub(r"\s+", " ", self.engine.last_reasoning).strip()[-1800:]
                extra = (f"\n\n[Your thinking so far (cut short to save time): …{thought}]\n"
                         "Now act on it: write your short plan if you haven't, then the next step's tag.")
                reply["text"] = ""
                think, cap = False, 0
                reply_room = min(s.get("maxTokens"), max(512, s.get("nCtx") // 3))
                return self.engine.chat(self._history(shrink, extra), reply_room, s.get("temperature"), on_piece, False)
            except RuntimeError as e:
                if "context" not in str(e).lower() or shrink == 0.15:
                    raise
                reply["text"] = ""
        return ""

    def messages_question(self):
        for m in reversed(self.messages):
            if m["role"] == "user":
                return m["text"]
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
                self._msg("note", "This reply looks like gibberish. Try asking again; if it keeps happening, the model "
                                  "file may be broken. Download it again from the Models tab.")
                break
            if self.stop_requested:
                self._msg("note", "Stopped.")
                break
            self.total_steps = getattr(self, "total_steps", 0) + 1
            same = re.sub(r"\s+", " ", reply["text"]).strip() == getattr(self, "last_reply", None)
            self.last_reply = re.sub(r"\s+", " ", reply["text"]).strip()
            if same:
                self.repeats += 1
                self._msg("tool", "You sent exactly the same reply as before. Don't repeat it. "
                                  "Answer the user now with what you have.")
                if self.repeats >= 3:
                    break
                continue
            if getattr(self.engine, "last_finish", "") == "length":
                self._msg("tool", "Your reply was cut off because it was too long, so nothing in it was done. "
                                  "Use shorter steps. Never retype page text into code: read data from the live page "
                                  "with <js> and document.querySelectorAll.")
                continue
            actions = tools.parse(full)
            for n in tools.notes(full):
                if n not in self.task_notes:
                    self.task_notes.append(n)
            if not actions and getattr(self, "browser_task", False) and tools.NOTE.search(full) and \
                    len(tools.prose_of(full)) < 40 and self.steps_left > 0:
                self._msg("tool", "Noted. Carry on with the next step of the task.")
                continue
            if not any(a.kind == "run" for a in actions) and not self.ran_code and not self.repeats:
                # Only before any code ran: later, code blocks in a final answer are examples, not requests.
                fb = tools.fallback_run(self._last_user_text(), full)
                if fb:
                    actions.append(fb)
            # Small models often finish a full answer and then ask for a needless click. Drop it.
            dropped = False
            if len(tools.prose_of(full)) > (900 if getattr(self, "browser_task", False) else 350) and actions and all(a.kind in ("click", "type", "press", "scroll", "page")
                                                                  for a in actions):
                actions, dropped = [], True
            if not actions and not dropped and re.search(r"<(search|browse|fetch|extract|click|type|press|scroll|page|js|run|write|read|list|find)\b",
                                         tools.strip_thinking(full), re.I) and not self.repeats:
                self.repeats += 1
                self._msg("tool", "A tool tag in your reply wasn't understood, so nothing was done. "
                                  "Write it exactly like the examples, e.g. <search query=\"words\"/>.")
                continue
            if self._unchecked_choice(actions):
                continue
            if not actions and self._skipped_navigation():
                continue
            if not actions:
                self._check_claims(reply)
                if re.search(r"<run\b", full, re.I):
                    self._msg("tool", "Your <run> tag had no code in it. Put the whole program between "
                                      "<run lang=\"...\"> and </run>.")
                    continue
                break
            auto = s.get("autoApprove")
            browser_auto = auto or s.get("browserAutoApprove")
            results = []
            did_run = False
            navigated = False   # page opened in this reply: element numbers after it were guessed blind
            waiting = False   # a card waits for the user: everything after it depends on it, so stop there
            for a in actions:
                if a.kind in ("browse", "fetch"):
                    a = self._fix_url(a)
                if waiting and not (a.is_browser and not a.browser_read and reply["actions"] and
                                    reply["actions"][-1]["action"]["kind"] in ("batch",) + tools.BROWSER_KINDS):
                    rest = actions[actions.index(a):]
                    reply["actions"][-1]["queued"] = [x.to_dict() for x in rest]
                    break
                key = (a.kind, a.path, a.content)
                if a.kind in ("extract", "page", "scroll", "sitesearch"):
                    key += (getattr(self.browser, "url", ""),)
                site = self._site_for(a)
                if site and site in self.blocked_sites:
                    self.repeats += 1
                    results.append(f"Skipped ({a.label}): {site} blocks the browser. Don't use that site again. "
                                   "Use another result or answer with what you have.")
                    continue
                if (a.read_only or a.browser_read) and key in self.done_lookups:
                    self.repeats += 1
                    results.append(f"You already did this ({a.label}) and got the result above. "
                                   "Do not repeat it. Use what you have and answer the user now.")
                    continue
                if a.kind in ("run", "js") and self._code_key(a) in self.failed_code:
                    self.repeats += 1
                    results.append("That exact code already failed or the user said no to it. Don't run it again. "
                                   "Fix it differently, or answer the user with what you have.")
                    continue
                if a.read_only:
                    results.append(tools.execute(a, self.index))
                    self.done_lookups[key] = True
                    continue
                if navigated and a.kind in ("click", "type", "press", "js"):
                    results.append(f"Skipped ({a.label}): you planned it before seeing the new page. "
                                   "Look at the page above and pick element numbers from it.")
                    continue
                if a.is_browser and a.browser_read:
                    if a.kind in ("browse", "search", "sitesearch", "back"):
                        navigated = True
                    if a.kind == "browse" and os.path.normcase(a.path) in getattr(self, "fresh_pages", set()):
                        self.fresh_pages.discard(os.path.normcase(a.path))
                        try:
                            self.browser.ensure()
                        except Exception:
                            pass
                        self._fresh_storage()
                    self._set_status(a.label + "…")
                    r = self._exec_browser(a)
                    self._note_block(a, r)
                    if a.kind in ("back", "sitesearch"):
                        self._page_changed()
                    if a.kind == "browse" and DATA_WORDS.search(self._last_user_text()) and "[LocalForge note]" not in r:
                        r += "\n\n" + self._auto_extract()
                    if a.kind in ("search", "browse"):
                        self.seen_urls = getattr(self, "seen_urls", set()) | set(re.findall(r"https?://[^\s)\]\"'<>]+", r))
                    results.append(r)
                    if a.kind not in ("scroll", "back", "wait"):     # going back again is a real step, not a repeat
                        self.done_lookups[key] = True
                    continue
                bank = self._on_bank()     # banking: clicks, typing and scripts always wait for the user's OK
                if a.is_browser and (bank or not browser_auto) and reply["actions"] and \
                        reply["actions"][-1]["status"] == "pending" and reply["actions"][-1]["action"]["kind"] in ("batch",) + tools.BROWSER_KINDS:
                    # Several clicks/typing in one reply: one card for all of them, run in order.
                    self._add_to_batch(reply["actions"][-1], a)
                    self.touch()
                    continue
                pa = {"id": len(reply["actions"]), "action": a.to_dict(), "status": "pending", "result": None}
                reply["actions"].append(pa)
                self.touch()
                if a.is_browser and browser_auto and not bank:
                    self._set_status(a.label + "…")
                    pa["status"] = "done"
                    r = self._exec_browser(a)
                    r += self._extract_if_empty(a, r)
                    self._page_changed()
                    self._note_block(a, r)
                    pa["result"] = self._short(r)
                    results.append(r)
                elif a.kind == "run" and auto:
                    pa["status"] = "done"
                    self._execute_run(a, pa)
                    did_run = True
                elif auto and a.kind in ("write", "mkdir") or (
                        auto and a.kind == "open" and not a.path.lower().endswith(tools.RISKY_OPEN)):
                    pa["status"] = "done"
                    pa["result"] = tools.execute(a, self.index)
                    results.append(pa["result"])
                    self._files_changed(a)
                else:
                    waiting = True
            for r in results:
                m = re.search(r"Browser page: (.*)\nURL: (\S+)", r)
                if m and (not self.visited or self.visited[-1][1] != m.group(2)):
                    self.visited.append((m.group(1).strip(), m.group(2)))
                if getattr(self, "browser_task", False) and re.search(
                        r"^(⚠ Nothing on the page changed|Browser error|Skipped)|This site blocked|isn't on the page any more", r, re.M):
                    self.rethink = True      # a step failed: think once about another way
            if self.repeats >= (3 if getattr(self, "browser_task", False) else 2) and self.steps_left > 1 and not waiting:
                results.append("STOP using tools now. Write your final answer to the user from the results above. "
                               "If you couldn't get the information, say so and share what you did find.")
                self.steps_left = 1
            if results:
                self._msg("tool", "\n\n".join(results))
            if waiting or not (results or did_run):
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
        self.ran_code = True
        if not r.ok:
            self.failed_code.add(self._code_key(a))
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
        self._fresh_storage()
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
        a = tools.Action("page" if d["kind"] == "batch" else d["kind"], d["path"], d["content"], d.get("extra", ""))
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
                if d["kind"] == "batch":
                    outs = []
                    for st in d["steps"]:
                        sa = tools.Action(st["kind"], st["path"], st["content"], st.get("extra", ""))
                        self._set_status(sa.label + "…")
                        r = self._exec_browser(sa)
                        r += self._extract_if_empty(sa, r)
                        self._note_js(sa, r)
                        if r.startswith("Browser error"):
                            outs.append(f"{sa.label}: {r}")
                            break
                        outs.append(f"{sa.label}: done")
                    pa["result"] = f"Did {len(outs)} of {len(d['steps'])} steps"
                    try:
                        page = self.browser.describe(4000)
                    except Exception as e:
                        page = f"(couldn't read the page: {e})"
                    self._msg("tool", "\n".join(outs) + "\n\nThe page now (check it shows what you expected):\n" + page)
                    self._page_changed()
                elif a.is_browser:
                    self._set_status(a.label + "…")
                    r = self._exec_browser(a)
                    r += self._extract_if_empty(a, r)
                    self._note_block(a, r)
                    self._page_changed()
                    pa["result"] = self._short(r)
                    self._msg("tool", r)
                elif a.kind == "run":
                    self._execute_run(a, pa)
                else:
                    pa["result"] = tools.execute(a, self.index)
                    self._msg("tool", pa["result"])
                    self._files_changed(a)
                # Steps the model wrote after this card (e.g. open the page it just wrote) run now.
                queued = pa.pop("queued", [])
                outs = []
                for q in queued:
                    qa = tools.Action(q["kind"], q["path"], q["content"], q.get("extra", ""))
                    if qa.read_only:
                        outs.append(tools.execute(qa, self.index))
                    elif qa.browser_read:
                        outs.append(self._exec_browser(qa))
                    else:
                        outs.append(f"Not done yet: {qa.label}. Ask for it again if you still need it.")
                        break
                if outs:
                    self._msg("tool", "\n\n".join(outs))
                # Carry on once every card in that reply has been answered.
                waiting = msg and any(x["status"] == "pending" for x in msg["actions"])
                if self.engine.loaded and not waiting:
                    if self.steps_left <= 0 and getattr(self, "total_steps", 0) < (60 if getattr(self, "browser_task", False) else 24):
                        self.steps_left = 3
                    self._agent_loop()
            except Exception as e:
                self._msg("note", str(e))
            finally:
                self.generating = False
                self.save_chat()
                self.touch()
                self._send_queued()
        threading.Thread(target=job, daemon=True).start()

    def _site_for(self, a: tools.Action) -> str:
        """The website an action works on: the URL it opens, or the page that's open for clicks and typing."""
        url = a.path if a.kind in ("browse", "fetch") else \
            (getattr(self.browser, "url", "") or "") if a.kind in ("page", "click", "type", "press", "js", "scroll") else ""
        return tools.site_of(url) if re.match(r"(https?://)?[\w-]+(\.[\w-]+)+(/|$|:)", url.strip(), re.I) else ""

    @staticmethod
    def _code_key(a: tools.Action):
        code = re.sub(r"/\*[\s\S]*?\*/", "", a.content)
        code = re.sub(r"(?m)(--|#|//)[^\n'\"]*$", "", code)
        return (a.path, re.sub(r"\s+", " ", code).strip())

    def _page_changed(self):
        """After clicks or typing the page is different, so reading or reopening it is allowed again."""
        self.done_lookups = {k: v for k, v in self.done_lookups.items() if k[0] not in ("browse", "page", "fetch", "extract")}

    def _files_changed(self, a=None):
        """After a write/mkdir/delete, folder listings, file reads and pages may differ, so allow them again."""
        self.done_lookups = {k: v for k, v in self.done_lookups.items()
                             if k[0] not in ("list", "read", "find", "browse", "page")}
        if a is not None and a.kind == "write" and a.path.lower().endswith((".html", ".htm")):
            self.fresh_pages = getattr(self, "fresh_pages", set()) | {os.path.normcase(a.path)}

    def _fresh_storage(self):
        """A newly written page starts with no saved data left over from earlier tests."""
        try:
            if self.browser.running:
                self.browser.send("Storage.clearDataForOrigin", {"origin": "file://",
                                                                 "storageTypes": "local_storage,session_storage,indexeddb"})
        except Exception:
            pass

    def _fix_url(self, a: tools.Action) -> tools.Action:
        """Small models often cut a link short (…/japan/toky). If it's the start of exactly one link we've seen, use that."""
        seen = getattr(self, "seen_urls", set())
        url = a.path.strip()
        if not url or url in seen:
            return a
        # Only finish a link that is visibly cut mid-word (…/toky); never extend a complete one like a site root.
        path = re.sub(r"^https?://[^/]+", "", url)
        if url.endswith("/") or not re.search(r"[A-Za-z0-9]$", url) or path.count("/") < 1 or len(path) < 3:
            return a
        matches = [u for u in seen if u.startswith(url) and len(u) - len(url) <= 12
                   and re.fullmatch(r"[A-Za-z0-9_-]+/?", u[len(url):])]
        if len(matches) == 1:
            return tools.Action(a.kind, matches[0], a.content, a.extra)
        return a

    CLAIMS = re.compile(r"ran successfully|run successfully|executed successfully|server received|"
                        r"(saved|written|created) (it |the file |the csv |successfully)|tests? (all )?pass", re.I)

    def _exec_browser(self, a):
        if a.kind in ("browse", "click", "type", "press", "fill", "sitesearch", "back"):
            self.moved = True
        return tools.execute_browser(a, self.browser)

    BANK = re.compile(r"(^|\.)(nationwide\.co\.uk|barclays\.co\.uk|hsbc\.co\.uk|lloydsbank\.co(m|\.uk)|natwest\.com|"
                      r"santander\.co\.uk|halifax-online\.co\.uk|halifax\.co\.uk|monzo\.com|starlingbank\.com|revolut\.com|"
                      r"tsb\.co\.uk|metrobankonline\.co\.uk|firstdirect\.com|rbs\.co\.uk|chase\.(com|co\.uk)|paypal\.com|"
                      r"wise\.com|coventrybuildingsociety\.co\.uk|virginmoney\.com)$|bank|banking", re.I)

    def _on_bank(self) -> bool:
        """The open page is online banking (Nationwide etc.): the AI may read, but never act there without asking."""
        try:
            host = (urllib.parse.urlparse(getattr(self.browser, "url", "") or "").hostname or "").lower()
        except Exception:
            return False
        return bool(host) and bool(self.BANK.search(host))

    COMPARE = re.compile(r"\b(cheapest|lowest|highest|best|most|least|fastest|biggest|smallest|largest|newest|oldest|"
                         r"compare|top[- ]rated|better|lowest[- ]priced)\b", re.I)
    COMMIT = re.compile(r"add to (basket|cart|bag|trolley)|^\W*add\b|\bbuy\b|checkout|check out|place order|order now|"
                        r"\bbook\b|purchase|\bpay\b|reserve|subscribe", re.I)

    def _unchecked_choice(self, actions) -> bool:
        """The user wants the cheapest/best of something. Models grab the first option and call it the cheapest.
        Before the first add-to-basket/buy click (or the final answer), make it list every option's value once."""
        if not getattr(self, "browser_task", False) or getattr(self, "choice_checked", False):
            return False
        m = self.COMPARE.search(self.messages_question() or "")
        if not m:
            return False
        last = next((x["text"] for x in reversed(self.messages) if x["role"] == "tool"), "")

        def commits(a):
            if a.kind == "click" and a.extra == "text":
                return bool(self.COMMIT.search(a.path))
            if a.kind == "click" and a.extra != "row":
                el = re.search(rf"^\[{re.escape(a.path)}\] \S+ \"([^\"]*)\"", last, re.M)
                return bool(el and self.COMMIT.search(el.group(1)))
            return a.kind == "fill" and a.extra == "submit" and bool(self.COMMIT.search(a.content))
        if actions and not any(commits(a) for a in actions):
            return False
        self.choice_checked = True
        self.rethink = True
        self._msg("tool", f'Check before you choose: the user asked for the {m.group(1).lower()}. Have you seen the '
                          "deciding value (price, rating, size...) of EVERY option, not just the first? Write each one as a "
                          "note, e.g. <note>Option A: £12.50</note>. If any option's value isn't on a page you've seen yet "
                          "(e.g. prices only on product pages), open it first. Only then pick the right one and carry on.")
        return True

    NAV_ASK = re.compile(r"\b(open|click|go to|go into|navigate|visit|switch to|press)\b", re.I)

    def _skipped_navigation(self) -> bool:
        """The user asked to open/click something but the model answered without moving: send it back once."""
        if self.moved or self.move_nudged:
            return False
        asked, used_browser = "", False
        for m in reversed(self.messages):
            if m["role"] == "user":
                asked = m["text"]
                used_browser = used_browser or "looking at this page in your browser" in (m.get("modelText") or "")
                break
            if m["role"] == "tool" and re.match(r"(Browser page|Data on the page|Search results|Scrolled)", m["text"]):
                used_browser = True
        if not used_browser or not self.NAV_ASK.search(asked[:400]):
            return False
        self.move_nudged = True
        self._msg("tool", "The user asked you to open or click something, but you haven't opened, clicked or "
                          "navigated anything yet, so your answer is about the wrong page. Do that step first "
                          "(<click n=\"...\"/> on the right element, or <browse url=\"...\"/>), then answer from "
                          "the new page.")
        return True

    def _check_claims(self, reply):
        """Warns the user when the answer claims success that no tool output in this request shows,
        or gives links that never appeared in any page or result."""
        prose = tools.prose_of(reply["text"])
        links = set(re.findall(r"https?://[^\s)\]\"'<>|*]+", prose))
        seen_text = " ".join(m["text"] + " " + (m.get("modelText") or "") for m in self.messages
                             if m["role"] in ("tool", "run", "user")).replace("&amp;", "&")
        if links:
            core = lambda u: re.sub(r"^https?://(www\.)?", "", u.replace("&amp;", "&")).split("?")[0].split("#")[0].rstrip("/.,;:").lower()
            seen_cores = {core(u) for u in re.findall(r"https?://[^\s)\]\"'<>|*]+", seen_text)}
            made_up = [u for u in links if core(u) not in seen_cores]
            if made_up:
                self._msg("note", "⚠ These links didn't appear in any page LocalForge opened, so they may be made up: "
                                  + ", ".join(sorted(made_up)[:5]))
        # Prices and percentages that appear in no page or code output.
        nums = lambda t: {float(x.replace(",", "")) for x in re.findall(r"\d(?:[\d,]*\d)?(?:\.\d+)?", t)}
        seen_nums = nums(seen_text)
        odd = [m.group(0) for m in re.finditer(r"[£$€]\s?\d(?:[\d,]*\d)?(?:\.\d+)?|\b\d(?:[\d,]*\d)?(?:\.\d+)?\s?%", prose)
               if not nums(m.group(0)) <= seen_nums]
        if odd:
            self._msg("note", "⚠ These numbers didn't appear in any page or code output, so they may be estimates "
                              "or made up: " + ", ".join(dict.fromkeys(odd[:6])))
        if not self.CLAIMS.search(prose):
            return
        since = []
        for m in reversed(self.messages):
            if m["role"] == "user":
                break
            if m["role"] in ("run", "tool"):
                since.append(m)
        ok_run = any(m["role"] == "run" and m["text"].startswith("✓") for m in since)
        ok_tool = any(m["role"] == "tool" and re.match(r"(Saved|Created|Typed and submitted|Did \d+ of|Browser page|Clicked|"
                                                        r"Data |Fetched|Script result|Pressed)", m["text"]) for m in since)
        runs = [m for m in since if m["role"] == "run"]
        code_failed_last = bool(runs) and not runs[0]["text"].startswith("✓") and not ok_tool
        if (not ok_run and not ok_tool) or code_failed_last:
            self._msg("note", "⚠ LocalForge couldn't confirm this: no successful run or saved file in this "
                              "request matches what the answer says. Check the result before relying on it.")

    @staticmethod
    def _add_to_batch(pa, a: tools.Action):
        d = pa["action"]
        if d["kind"] != "batch":
            pa["action"] = d = {"kind": "batch", "path": "", "content": "", "extra": "", "steps": [d], "label": ""}
        d["steps"].append(a.to_dict())
        d["label"] = f"Do {len(d['steps'])} browser steps: " + "; ".join(st["label"] for st in d["steps"])[:300]

    def _extract_if_empty(self, a: tools.Action, r: str) -> str:
        """A <js> extractor that came back empty or broken: show the page's data the easy way."""
        if a.kind == "js" and (r.startswith("Browser error") or "returned nothing" in r[:80]
                               or re.search(r"^Script result:\s*(\[\s*\]|null|\{\s*\})", r) or r.count("null") > 5):
            ex = self._auto_extract()
            return ("\n\nYour script didn't get the data. Here it is extracted for you:\n" + ex) if ex else ""
        return ""

    def _auto_extract(self) -> str:
        try:
            ex = self.browser.extract(self._last_user_text()[:120])
        except Exception:
            return ""
        if ex.startswith("No tables"):
            return ""
        self.done_lookups[("extract", "", "", getattr(self.browser, "url", ""))] = True
        return ("Data extracted from this page automatically (tables/lists as rows; ▲/▼ = up/down shown by colour). "
                "Use these rows; sort, filter or total them with <run lang=\"python\">:\n" + ex)

    def _note_js(self, a: tools.Action, result: str):
        if a.kind == "js" and (result.startswith("Browser error") or "returned nothing" in result[:80]
                               or re.search(r"^Script result:\s*(\[\]|null|\{\})\s*$", result)):
            self.failed_code.add(self._code_key(a))

    def _note_block(self, a: tools.Action, result: str):
        self._note_js(a, result)
        if "This site blocked the browser" in result:
            site = self._site_for(a)
            if site:
                self.blocked_sites.add(site)

    def reject(self, msg_id: int, idx: int):
        pa = self._find_action(msg_id, idx)
        if not pa or pa["status"] != "pending":
            return
        pa["status"] = "rejected"
        d = pa["action"]
        if d["kind"] in ("run", "js"):
            self.failed_code.add(self._code_key(tools.Action(d["kind"], d["path"], d["content"])))
        for st in d.get("steps", []):
            if st["kind"] == "js":
                self.failed_code.add(self._code_key(tools.Action("js", st["path"], st["content"])))
        self.save_chat()
        self.touch()
        msg = next((m for m in self.messages if m["id"] == msg_id), None)
        if self.generating or not self.engine.loaded or (msg and any(x["status"] == "pending" for x in msg["actions"])):
            return
        # Tell the model, so it answers with what it has instead of the turn ending silently.
        self.generating = True

        def job():
            try:
                self._msg("tool", f'The user said no to: {pa["action"]["label"]}. Don\'t ask for it again. '
                                  "Answer the user now with what you already found.")
                self.steps_left = max(self.steps_left, 2)
                self._agent_loop()
            except Exception as e:
                self._msg("note", str(e))
            finally:
                self.generating = False
                self.save_chat()
                self.touch()
                self._send_queued()
        threading.Thread(target=job, daemon=True).start()

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
        prose = tools.prose_of(prose)
        if len(prose) < 60:
            return False
        letters = sum(1 for c in prose if c.isalpha() or c == " ")
        return letters / len(prose) < 0.5
