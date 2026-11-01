"""LocalForge's own web browser.

A headless Microsoft Edge (or Chrome/Chromium) runs in the background with its own profile, so logins stick.
The app shows it live in the Browser tab (screencast frames) and both you and the AI can click, type and scroll.
The AI can also read page text, list the clickable things on a page, fill forms and run JavaScript.
"""
import base64
import glob
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .procs import kill_matching, tie_to_app

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
WIDTH, HEIGHT = 1280, 800

# Injected into every page: keep new tabs/popups in the same view, and auto-dismiss nothing else.
INIT_SCRIPT = r"""
(() => {
  try {
    window.open = function (u) { if (u) location.href = new URL(u, location.href).href; return window; };
    document.addEventListener('click', e => {
      const a = e.target && e.target.closest && e.target.closest('a[target]');
      if (a && a.target !== '_self') a.target = '_self';
    }, true);
  } catch (e) {}
})();
"""

# Numbers the visible interactive elements and returns them with the page text.
SNAPSHOT_JS = r"""
(() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  document.querySelectorAll('[data-lf-id]').forEach(e => e.removeAttribute('data-lf-id'));
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=option],[contenteditable=true],[onclick],summary,label[for]';
  const out = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el) || n >= 150) continue;
    n++; el.setAttribute('data-lf-id', n);
    const tag = el.tagName.toLowerCase();
    let label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder || el.title || el.alt || el.name || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    let kind = tag === 'a' ? 'link' : tag === 'input' ? 'input:' + (el.type || 'text') : tag === 'textarea' ? 'textbox' : tag === 'select' ? 'select' : tag === 'button' ? 'button' : (el.getAttribute('role') || tag);
    let extra = '';
    if (tag === 'input' || tag === 'textarea') { extra = el.type === 'password' ? ' value=***' : (el.value ? ' value="' + el.value.slice(0, 60) + '"' : ''); if (el.placeholder) extra += ' placeholder="' + el.placeholder.slice(0, 40) + '"'; }
    if (tag === 'select') extra = ' options=' + [...el.options].slice(0, 12).map(o => o.text.trim()).join('|');
    if (el.type === 'checkbox' || el.type === 'radio') extra += el.checked ? ' [checked]' : ' [unchecked]';
    if (tag === 'a') { try { extra += ' -> ' + new URL(el.href).href.slice(0, 100); } catch (e) {} }
    out.push('[' + n + '] ' + kind + ' "' + label + '"' + extra);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
  return JSON.stringify({ url: location.href, title: document.title, text, elements: out });
})()
"""

KEYS = {
    "enter": ("Enter", "Enter", 13, "\r"), "tab": ("Tab", "Tab", 9, None), "escape": ("Escape", "Escape", 27, None),
    "backspace": ("Backspace", "Backspace", 8, None), "delete": ("Delete", "Delete", 46, None),
    "arrowup": ("ArrowUp", "ArrowUp", 38, None), "arrowdown": ("ArrowDown", "ArrowDown", 40, None),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, None), "arrowright": ("ArrowRight", "ArrowRight", 39, None),
    "pageup": ("PageUp", "PageUp", 33, None), "pagedown": ("PageDown", "PageDown", 34, None),
    "home": ("Home", "Home", 36, None), "end": ("End", "End", 35, None), "space": (" ", "Space", 32, " "),
}


def find_browser() -> str | None:
    env = os.environ.get("LOCALFORGE_BROWSER")
    if env and Path(env).is_file():
        return env
    cands = []
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [Path(base) / "Microsoft/Edge/Application/msedge.exe", Path(base) / "Google/Chrome/Application/chrome.exe",
                          Path(base) / "BraveSoftware/Brave-Browser/Application/brave.exe"]
    else:
        cands += [Path(p) for p in glob.glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")]
        for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            w = shutil.which(n)
            if w:
                cands.append(Path(w))
    for c in cands:
        if c.is_file():
            return str(c)
    return None


class Browser:
    def __init__(self, on_change=None):
        self.on_change = on_change or (lambda: None)
        self.proc = None
        self.ws = None
        self.exe = None
        self.url = ""
        self.title = ""
        self.loading = False
        self.frame: bytes | None = None
        self.frame_seq = 0
        self.frame_cond = threading.Condition()
        self.error = None
        self._id = 0
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self.profile = config.data_dir() / "browser-profile"
        self.width, self.height = WIDTH, HEIGHT
        import atexit
        atexit.register(self.close)

    # ---------- lifecycle ----------
    @property
    def running(self):
        if self.ws is None:
            return False
        return self.proc is None or self.proc.poll() is None

    def ensure(self):
        if self.running:
            return
        with self._start_lock:
            if self.running:
                return
            self._start()

    def _start(self):
        self.exe = find_browser()
        if not self.exe:
            raise RuntimeError("No Edge or Chrome found on this PC. Install Microsoft Edge or Google Chrome to use the browser.")
        self.profile.mkdir(parents=True, exist_ok=True)
        port_file = self.profile / "DevToolsActivePort"
        # A browser left over from an earlier run may still own the profile: reuse it if it answers.
        try:
            old = int(port_file.read_text().split()[0])
            with urllib.request.urlopen(f"http://127.0.0.1:{old}/json/version", timeout=2):
                pass
            self.proc = None
            self._connect(old, time.time() + 15)
            return
        except Exception:
            pass
        port_file.unlink(missing_ok=True)
        args = [self.exe, "--headless=new", "--remote-debugging-port=0", "--remote-allow-origins=*",
                f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-features=Translate,MediaRouter", "--hide-scrollbars", "--mute-audio",
                f"--window-size={WIDTH},{HEIGHT}", "about:blank"]
        if os.name != "nt" and os.geteuid() == 0:
            args.insert(1, "--no-sandbox")
        port = None
        for attempt in range(2):
            self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            tie_to_app(self.proc)
            port = self._wait_port(port_file, time.time() + 30)
            if port:
                break
            if self.proc.poll() is not None and attempt == 0:
                # A stuck browser from a crashed run is holding the profile: close it and try again.
                kill_matching(str(self.profile))
                time.sleep(1.5)
                for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
                    try:
                        (self.profile / lock).unlink()
                    except OSError:
                        pass
                continue
            raise RuntimeError("The browser closed right after starting." if self.proc.poll() is not None
                               else "The browser didn't start in time.")
        self._connect(port, time.time() + 30)

    def _wait_port(self, port_file, deadline):
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return None
            try:
                return int(port_file.read_text().split()[0])
            except Exception:
                time.sleep(0.2)
        return None

    def _connect(self, port, deadline):
        import websocket  # websocket-client
        ws_url = None
        while time.time() < deadline and not ws_url:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
                    pages = [t for t in json.loads(r.read()) if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                else:
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
                    urllib.request.urlopen(req, timeout=3).close()
            except Exception:
                time.sleep(0.3)
        if not ws_url:
            raise RuntimeError("Couldn't connect to the browser.")
        self.ws = websocket.create_connection(ws_url, timeout=None, suppress_origin=True, enable_multithread=True)
        threading.Thread(target=self._reader, daemon=True).start()
        self.send("Page.enable")
        self.send("Runtime.enable")
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": INIT_SCRIPT})
        self.send("Emulation.setDeviceMetricsOverride", {"width": self.width, "height": self.height,
                                                         "deviceScaleFactor": 1, "mobile": False})
        try:
            self.send("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(Path.home() / "Downloads"),
                                                      "eventsEnabled": True})
        except Exception:
            pass
        self._start_screencast()
        self.error = None

    def _start_screencast(self):
        self.send("Page.startScreencast", {"format": "jpeg", "quality": 70, "maxWidth": self.width,
                                           "maxHeight": self.height, "everyNthFrame": 1})

    def resize(self, w: int, h: int):
        self.width, self.height = max(320, min(w, 2560)), max(240, min(h, 1600))
        if self.running:
            self.send("Emulation.setDeviceMetricsOverride", {"width": self.width, "height": self.height,
                                                             "deviceScaleFactor": 1, "mobile": False})
            self.send("Page.stopScreencast")
            self._start_screencast()

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.ws = None
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    # ---------- CDP plumbing ----------
    def _reader(self):
        ws = self.ws
        while ws is self.ws and ws is not None:
            try:
                raw = ws.recv()
            except Exception:
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if "id" in msg:
                p = self._pending.get(msg["id"])
                if p:
                    p["result"] = msg
                    p["event"].set()
                continue
            self._event(msg.get("method"), msg.get("params") or {})
        if ws is self.ws:
            self.ws = None
            self.on_change()

    def _event(self, method, p):
        if method == "Page.screencastFrame":
            try:
                self.ws.send(json.dumps({"id": self._next_id(), "method": "Page.screencastFrameAck",
                                         "params": {"sessionId": p["sessionId"]}}))
            except Exception:
                pass
            with self.frame_cond:
                self.frame = base64.b64decode(p["data"])
                self.frame_seq += 1
                self.frame_cond.notify_all()
        elif method == "Page.frameNavigated":
            f = p.get("frame") or {}
            if not f.get("parentId"):
                self.url = f.get("url", "")
                self.on_change()
        elif method == "Page.loadEventFired":
            self.loading = False
            threading.Thread(target=self._refresh_title, daemon=True).start()
        elif method == "Page.frameStartedLoading":
            self.loading = True
            self.on_change()
        elif method == "Page.javascriptDialogOpening":
            threading.Thread(target=lambda: self.send("Page.handleJavaScriptDialog", {"accept": True}), daemon=True).start()

    def _refresh_title(self):
        try:
            r = self.eval("JSON.stringify([location.href, document.title])")
            self.url, self.title = json.loads(r)
        except Exception:
            pass
        self.on_change()

    def _next_id(self):
        with self._lock:
            self._id += 1
            return self._id

    def send(self, method, params=None, timeout=30):
        if self.ws is None:
            raise RuntimeError("Browser isn't running")
        i = self._next_id()
        ev = {"event": threading.Event(), "result": None}
        self._pending[i] = ev
        try:
            self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            if not ev["event"].wait(timeout):
                raise RuntimeError(f"Browser didn't answer ({method})")
            res = ev["result"]
            if "error" in res:
                raise RuntimeError(res["error"].get("message", "browser error"))
            return res.get("result", {})
        finally:
            self._pending.pop(i, None)

    def eval(self, expr: str, timeout=30):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True,
                                           "userGesture": True}, timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError((d.get("exception") or {}).get("description") or d.get("text") or "script error")
        return (r.get("result") or {}).get("value")

    # ---------- actions ----------
    def wait_loaded(self, timeout=20):
        end = time.time() + timeout
        time.sleep(0.3)
        while time.time() < end:
            try:
                if self.eval("document.readyState", 5) == "complete":
                    break
            except Exception:
                pass
            time.sleep(0.25)
        time.sleep(0.4)

    def navigate(self, url: str):
        self.ensure()
        url = url.strip()
        if not url:
            return
        if url.startswith(("http://", "https://", "about:", "file:", "data:")):
            pass
        elif " " in url or ("." not in url and not url.startswith("localhost")):
            url = "https://duckduckgo.com/?q=" + urllib.parse.quote(url)
        else:
            host = url.split("/")[0].split(":")[0]
            local = host == "localhost" or all(x.isdigit() for x in host.split(".") if x) and host.count(".") == 3
            url = ("http://" if local else "https://") + url
        self.loading = True
        self.url = url
        self.on_change()
        r = self.send("Page.navigate", {"url": url})
        if r.get("errorText"):
            raise RuntimeError(f"Couldn't open {url}: {r['errorText']}")
        self.wait_loaded()
        self._refresh_title()

    def history(self, delta: int):
        self.ensure()
        h = self.send("Page.getNavigationHistory")
        i = h["currentIndex"] + delta
        if 0 <= i < len(h["entries"]):
            self.send("Page.navigateToHistoryEntry", {"entryId": h["entries"][i]["id"]})
            self.wait_loaded(10)

    def reload(self):
        self.ensure()
        self.send("Page.reload")
        self.wait_loaded(10)

    def click_xy(self, x: float, y: float, button="left", count=1):
        self.ensure()
        self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        for t in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": button, "clickCount": count})

    def scroll(self, dy: float, x: float | None = None, y: float | None = None):
        self.ensure()
        self.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x if x is not None else self.width / 2,
                                               "y": y if y is not None else self.height / 2, "deltaX": 0, "deltaY": dy})

    def type_text(self, text: str):
        self.ensure()
        self.send("Input.insertText", {"text": text})

    def key(self, name: str, modifiers=0):
        self.ensure()
        k = KEYS.get(name.lower().replace(" ", ""))
        if not k:
            if len(name) == 1:
                return self.type_text(name)
            raise RuntimeError(f"Unknown key {name}")
        key, code, vk, text = k
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": modifiers}
        self.send("Input.dispatchKeyEvent", {**base, "type": "keyDown" if text is None else "keyDown", **({"text": text} if text else {})})
        self.send("Input.dispatchKeyEvent", {**base, "type": "keyUp"})

    def snapshot(self, max_text=7000) -> dict:
        self.ensure()
        data = json.loads(self.eval(SNAPSHOT_JS))
        self.url, self.title = data["url"], data["title"]
        if len(data["text"]) > max_text:
            data["text"] = data["text"][:max_text] + "\n…[page text cut — scroll or ask for more]"
        return data

    def describe(self, max_text=7000) -> str:
        d = self.snapshot(max_text)
        els = "\n".join(d["elements"][:120])
        return (f"Browser page: {d['title']}\nURL: {d['url']}\n\nPage text:\n{d['text'] or '(empty)'}\n\n"
                f"Clickable / fillable elements (use their numbers):\n{els or '(none)'}")

    def _center(self, n: int):
        r = self.eval(f"""(() => {{ const el = document.querySelector('[data-lf-id="{int(n)}"]');
            if (!el) return null; el.scrollIntoView({{block: 'center', inline: 'center'}});
            const b = el.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }})()""")
        if not r:
            raise RuntimeError(f"Element [{n}] isn't on the page any more. Read the page again.")
        return r

    def click_element(self, n: int):
        x, y = self._center(n)
        time.sleep(0.15)
        self.click_xy(x, y)
        self.wait_loaded(8)

    def fill(self, n: int, text: str, submit=False):
        x, y = self._center(n)
        tag = self.eval(f"document.querySelector('[data-lf-id=\"{int(n)}\"]').tagName")
        if tag == "SELECT":
            ok = self.eval(f"""(() => {{ const s = document.querySelector('[data-lf-id="{int(n)}"]'); const want = {json.dumps(text)}.toLowerCase();
                const o = [...s.options].find(o => o.text.trim().toLowerCase() === want || o.value.toLowerCase() === want)
                       || [...s.options].find(o => o.text.toLowerCase().includes(want));
                if (!o) return false; s.value = o.value; s.dispatchEvent(new Event('input', {{bubbles: true}}));
                s.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()""")
            if not ok:
                raise RuntimeError(f'No option "{text}" in [{n}]')
            return
        self.click_xy(x, y)
        self.eval(f"""(() => {{ const el = document.querySelector('[data-lf-id="{int(n)}"]'); el.focus();
            if ('value' in el) {{ el.select && el.select(); }} else {{ document.execCommand('selectAll'); }} }})()""")
        self.send("Input.insertText", {"text": text})
        if submit:
            self.key("enter")
            self.wait_loaded(10)

    def run_js(self, code: str) -> str:
        self.ensure()
        expr = f"(async () => {{ {code} }})()" if "return" in code else code
        v = self.eval(expr)
        if v is None:
            return "(script ran, returned nothing)"
        return v if isinstance(v, str) else json.dumps(v, indent=1)[:8000]

    def search(self, query: str) -> str:
        self.navigate("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
        res = self.eval(r"""JSON.stringify([...document.querySelectorAll('.result')].slice(0, 10).map(r => {
            const a = r.querySelector('a.result__a'); const s = r.querySelector('.result__snippet');
            let href = a ? a.href : ''; try { const u = new URL(href); if (u.searchParams.get('uddg')) href = u.searchParams.get('uddg'); } catch (e) {}
            return { title: a ? a.innerText.trim() : '', url: href, snippet: s ? s.innerText.trim() : '' }; }))""")
        items = json.loads(res or "[]")
        if not items:
            return "Search results:\n" + self.describe(4000)
        return f'Web search results for "{query}":\n' + "\n".join(
            f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet'][:200]}" for i, r in enumerate(items))

    def screenshot_wait(self, since: int, timeout=20):
        with self.frame_cond:
            if self.frame_seq == since:
                self.frame_cond.wait(timeout)
            return self.frame_seq, self.frame

    def state(self) -> dict:
        return {"running": self.running, "url": self.url, "title": self.title, "loading": self.loading,
                "frame": self.frame_seq, "width": self.width, "height": self.height,
                "available": bool(self.exe or find_browser()), "error": self.error}
