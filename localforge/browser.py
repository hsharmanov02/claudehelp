"""LocalForge's own web browser.

A headless Microsoft Edge (or Chrome/Chromium) runs in the background with its own profile, so logins stick.
The app shows it live in the Browser tab (screencast frames) and both you and the AI can click, type and scroll.
The AI can also read page text, list the clickable things on a page, fill forms and run JavaScript.
"""
import base64
import glob
import json
import re
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
EXTRACT_JS = r"""(q) => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const clean = t => (t || '').trim().replace(/\s+/g, ' ');
  const mark = (el, t) => {
    // Up/down shown only by colour, class or icon becomes a visible ▲/▼.
    if (!/^[$£€]?\s?[\d][\d.,]*\s?(%|[kKmMbB])?$/.test(t)) return t;   // only plain numbers, prices and percents
    const html = el.innerHTML.slice(0, 400);
    const leaf = el.querySelector('span,div,p,b') || el;
    const c = (getComputedStyle(leaf).color.match(/\d+/g) || [0, 0, 0]).map(Number);
    if (/(down|neg|loss|decrease|red|danger|fall)/i.test(html + el.className) || (c[0] > 150 && c[1] < 120 && c[2] < 120)) return '▼' + t;
    if (/(\bup\b|caret-up|pos|gain|increase|green|success|rise)/i.test(html + el.className) || (c[1] > 130 && c[0] < 120)) return '▲' + t;
    return t;
  };
  const here = location.href.split('#')[0];
  const link = el => { const a = [...el.querySelectorAll('a[href]')].find(a => clean(a.innerText).length > 8 && a.href.startsWith('http') && a.href.split('#')[0] !== here);
    return a ? ' -> ' + a.href.slice(0, 160) : ''; };
  const BUTTONISH = /^(buy|sell|select|add|add to (cart|basket|bag)|view|details|more|opens in a new (window|tab)|opens in a new window or tab|watch|compare|share|save)$/i;
  const words = clean(q).toLowerCase().split(' ').filter(Boolean);
  const score = el => words.length ? words.filter(w => el.innerText.toLowerCase().includes(w)).length : 0;
  const parts = [];
  // 1) Tables (header cells can be <th>, also inside rows)
  const tables = [...document.querySelectorAll('table')].filter(t => vis(t) && t.rows.length >= 3)
    .sort((a, b) => score(b) - score(a) || b.rows.length - a.rows.length).slice(0, 3);
  tables.forEach((t, i) => {
    const rows = [...t.rows].filter(vis).slice(0, 60).map(r =>
      [...r.cells].map(c => mark(c, clean(c.innerText))).filter(x => x && !BUTTONISH.test(x)).join(' | ') + link(r)).filter(x => x.replace(/[ |]/g, ''));
    parts.push(`Table ${i + 1} (${t.rows.length} rows${t.rows.length > 60 ? ', first 60 shown' : ''}):\n` + rows.join('\n'));
  });
  // 2) Repeated items (cards, search results, product tiles)
  const groups = [];
  for (const p of document.querySelectorAll('body *')) {
    if (p.closest('table') || !vis(p) || p.children.length < 5) continue;
    const sig = el => el.tagName + '.' + ([...el.classList].filter(c => !/\d/.test(c)).slice(0, 2).join('.') || el.getAttribute('data-component-type') || '');
    const counts = {};
    for (const c of p.children) counts[sig(c)] = (counts[sig(c)] || 0) + 1;
    const [best, n] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
    if (n < 5) continue;
    const items = [...p.children].filter(c => sig(c) === best && vis(c) && clean(c.innerText).length > 20);
    if (items.length < 5) continue;
    const avg = items.reduce((s, c) => s + clean(c.innerText).length, 0) / items.length;
    if (avg > 1500) continue;
    groups.push({ items, value: items.length * Math.min(avg, 300) + score(p) * 5000 });
  }
  groups.sort((a, b) => b.value - a.value);
  const used = new Set();
  for (const g of groups.slice(0, 6)) {
    if (parts.length >= 5 || g.items.some(x => used.has(x))) continue;
    g.items.forEach(x => used.add(x));
    const rows = g.items.slice(0, 40).map((it, i) => {
      const texts = []; const w = document.createTreeWalker(it, NodeFilter.SHOW_TEXT); let n;
      while ((n = w.nextNode())) {
        const t = clean(n.textContent);
        if (!t || texts.includes(t) || !n.parentElement || !vis(n.parentElement)) continue;
        if (t.length < 6 && texts.slice(-3).some(x => x.includes(t))) continue;
        if (BUTTONISH.test(t)) continue;   // "£ | 374 | . | 99" pieces of a price already shown
        texts.push(mark(n.parentElement, t));
      }
      return (i + 1) + '. ' + texts.join(' | ').slice(0, 300) + link(it);
    });
    parts.push(`List of ${g.items.length} items${g.items.length > 40 ? ' (first 40 shown)' : ''}:\n` + rows.join('\n'));
  }
  return parts.join('\n\n').slice(0, 9000);
}"""


COOKIE_JS = r"""(() => {
  const words = /^(reject all|reject all cookies|reject|decline|decline all|refuse all|necessary only|only necessary|use necessary cookies only|essential only|only essential cookies|continue without accepting)$/i;
  const roots = [document, ...[...document.querySelectorAll('iframe')].map(f => { try { return f.contentDocument; } catch (e) { return null; } }).filter(Boolean)];
  for (const d of roots) {
    for (const b of d.querySelectorAll('button, [role=button], a, input[type=submit], input[type=button]')) {
      const t = (b.innerText || b.value || '').trim().replace(/\s+/g, ' ');
      if (t && t.length < 45 && words.test(t)) { b.click(); return t; }
    }
  }
  return '';
})()"""


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
    if (label.length < 4 || el.type === 'checkbox' || el.type === 'radio') {
      // Short labels like "X" or "on": add the text of the row/item it sits in, so it's clear which one it is.
      const row = el.closest('li,tr,[role=row],[role=listitem],label,.item,.row,div');
      const ctx = row ? row.innerText.trim().replace(/\s+/g, ' ').replace(label, '').trim().slice(0, 50) : '';
      if (ctx) extra += ' (in: "' + ctx + '")';
      if (el.type === 'checkbox' || el.type === 'radio') label = label === 'on' ? '' : label;
    }
    out.push('[' + n + '] ' + kind + ' "' + label + '"' + extra);
  }
  let text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
  // Meaning shown only by style: struck-through (done) items and red/down numbers lose it in plain text.
  const struck = [], down = [], up = [];
  for (const el of document.querySelectorAll('body *')) {
    if (struck.length + down.length + up.length > 80) break;
    if (!el.childNodes.length || el.children.length > 3) continue;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 80) continue;
    const cs = getComputedStyle(el);
    if (cs.textDecorationLine.includes('line-through')) { struck.push(t); continue; }
    if (/%|[\d.,]+/.test(t) && t.length < 25) {
      const cls = ((el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '') + ' ' +
                  ((el.querySelector('i,svg,span') || {}).className || '');
      const c = cs.color.match(/\d+/g) || [0, 0, 0];
      const red = +c[0] > 150 && +c[1] < 110 && +c[2] < 110, green = +c[1] > 130 && +c[0] < 110;
      if (/down|neg|loss|decrease|fall|red|danger/i.test(cls) || red || /^[▼↓]/.test(t)) down.push(t);
      else if (/\bup\b|pos|gain|increase|rise|green|success/i.test(cls) || green || /^[▲↑]/.test(t)) up.push(t);
    }
  }
  const cues = [];
  if (struck.length) cues.push('Struck through (e.g. done/completed): ' + struck.slice(0, 30).join(' | '));
  if (down.length) cues.push('Shown as negative/down (red or down arrow): ' + down.slice(0, 40).join(' | '));
  if (up.length) cues.push('Shown as positive/up (green or up arrow): ' + up.slice(0, 40).join(' | '));
  return JSON.stringify({ url: location.href, title: document.title, text, elements: out, cues });
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


def _fmt_remote(a: dict) -> str:
    """Turns a console.log argument into readable text, e.g. [2, 4, 6] or {name: "x", price: 42}."""
    if "value" in a:
        v = a["value"]
        return v if isinstance(v, str) else json.dumps(v)
    if "unserializableValue" in a:
        return a["unserializableValue"]
    pv = a.get("preview")
    if pv and pv.get("properties") is not None:
        def val(pr):
            if pr.get("type") == "string":
                return json.dumps(pr.get("value", ""))
            return str(pr.get("value", pr.get("type")))
        props = pv["properties"]
        more = ", …" if pv.get("overflow") else ""
        if pv.get("subtype") == "array":
            return "[" + ", ".join(val(x) for x in props) + more + "]"
        if pv.get("subtype") in ("map", "set") and pv.get("entries"):
            return pv.get("description", "") + " {" + ", ".join(
                (val(e["key"]) + " => " if e.get("key") else "") + val(e["value"]) for e in pv["entries"]) + more + "}"
        return "{" + ", ".join(f"{x.get('name')}: {val(x)}" for x in props) + more + "}"
    return str(a.get("description", a.get("type", "")))


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
        import collections
        self.console = collections.deque(maxlen=300)   # (seq, level, text) since the last page load
        self.console_seq = 0
        self.sandbox_url = None      # set by the app: a blank page served by LocalForge for running code
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
        try:
            self.send("Log.enable")
        except Exception:
            pass
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": INIT_SCRIPT})
        # Look like the normal browser: many sites (search engines especially) block "Headless" user agents.
        try:
            ua = self.send("Browser.getVersion").get("userAgent", "")
            ua = ua.replace("HeadlessChrome", "Chrome").replace("HeadlessEdg", "Edg")
            self.send("Network.setUserAgentOverride", {"userAgent": ua, "acceptLanguage": "en-US,en;q=0.9"})
        except Exception:
            pass
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
                self.console.clear()
                self.on_change()
        elif method == "Runtime.consoleAPICalled":
            parts = [_fmt_remote(a) for a in p.get("args") or []]
            self._log(p.get("type", "log"), " ".join(parts))
        elif method == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails") or {}
            ex = d.get("exception") or {}
            where = f" (line {d.get('lineNumber', 0) + 1})" if "lineNumber" in d else ""
            self._log("error", "Uncaught " + (ex.get("description") or d.get("text") or "error") + where)
        elif method == "Log.entryAdded":
            e = p.get("entry") or {}
            if e.get("level") in ("error", "warning"):
                self._log(e["level"], f"{e.get('text', '')} {e.get('url', '')}".strip())
        elif method == "Page.loadEventFired":
            self.loading = False
            threading.Thread(target=self._refresh_title, daemon=True).start()
        elif method == "Page.frameStartedLoading":
            self.loading = True
            self.on_change()
        elif method == "Page.javascriptDialogOpening":
            threading.Thread(target=lambda: self.send("Page.handleJavaScriptDialog", {"accept": True}), daemon=True).start()

    def _log(self, level, text):
        self.console_seq += 1
        self.console.append((self.console_seq, level, text[:1000]))

    def console_text(self, since=0, only_problems=False, limit=60) -> str:
        rows = [(lv, t) for (sq, lv, t) in list(self.console) if sq > since and (not only_problems or lv in ("error", "warning", "assert"))]
        rows = rows[-limit:]
        return "\n".join(f"[{lv}] {t}" for lv, t in rows)

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

    def eval(self, expr: str, timeout=30, repl=False):
        params = {"expression": expr, "returnByValue": True, "awaitPromise": True, "userGesture": True}
        if repl:
            params["replMode"] = True    # like the DevTools console: last expression is the result, let/const can repeat
        r = self.send("Runtime.evaluate", params, timeout)
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
        url = url.strip().strip('"')
        if re.match(r"^[A-Za-z]:[\\/]", url) or url.startswith("\\\\") or (url.startswith("/") and Path(url).exists()):
            url = Path(url).resolve().as_uri()      # a file on this PC, not a web address
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
        self.dismiss_cookies()
        self._refresh_title()

    def dismiss_cookies(self):
        """Closes cookie banners by choosing the privacy-friendly option (reject / necessary only), never accept."""
        try:
            hit = self.eval(COOKIE_JS, 5)
            if not hit:
                time.sleep(1.0)     # many banners appear a moment after the page loads
                hit = self.eval(COOKIE_JS, 5)
            if hit:
                time.sleep(0.6)
        except Exception:
            pass

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

    def describe(self, max_text=4500, console=True) -> str:
        d = self.snapshot(max_text)
        self._last_read = "describe"
        els = "\n".join(e[:110] for e in d["elements"][:70])
        problems = self.console_text(only_problems=True, limit=15) if console else ""
        cues = "\n".join(d.get("cues") or [])
        return (f"Browser page: {d['title']}\nURL: {d['url']}\n\nPage text:\n{d['text'] or '(empty)'}\n\n"
                + (f"Visual cues (meaning shown by colour/style, not in the text):\n{cues[:1500]}\n\n" if cues else "") +
                f"Clickable / fillable elements (use their numbers):\n{els or '(none)'}"
                + (f"\n\nConsole errors/warnings on this page:\n{problems}" if problems else ""))

    def _center(self, n: int):
        r = self.eval(f"""(() => {{ const el = document.querySelector('[data-lf-id="{int(n)}"]');
            if (!el) return null; el.scrollIntoView({{block: 'center', inline: 'center'}});
            const old = el.style.outline; el.style.outline = '3px solid #ff7a28'; el.style.outlineOffset = '2px';
            setTimeout(() => {{ el.style.outline = old; el.style.outlineOffset = ''; }}, 900);
            const b = el.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; }})()""")
        if not r:
            raise RuntimeError(f"Element [{n}] isn't on the page any more. Read the page again.")
        return r

    def click_element(self, n: int):
        x, y = self._center(n)
        time.sleep(0.45)        # the orange outline shows the user what's about to be clicked
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
        kind = self.eval(f"(document.querySelector('[data-lf-id=\"{int(n)}\"]').type || '').toLowerCase()")
        if kind in ("time", "date", "datetime-local", "month", "week", "number", "range", "color"):
            # These inputs ignore typed keys in headless mode: set the value the way a page script would.
            self.eval(f"""(() => {{ const el = document.querySelector('[data-lf-id="{int(n)}"]');
                const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                set.call(el, {json.dumps(text)}); el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}})); }})()""")
            if submit:
                self.key("enter")
                self.wait_loaded(10)
            return
        self.click_xy(x, y)
        self.eval(f"""(() => {{ const el = document.querySelector('[data-lf-id="{int(n)}"]'); el.focus();
            if ('value' in el) {{ el.select && el.select(); }} else {{ document.execCommand('selectAll'); }} }})()""")
        self.send("Input.insertText", {"text": text})
        if submit:
            self.key("enter")
            self.wait_loaded(10)

    def extract(self, query: str = "") -> str:
        """Pulls tables and repeated lists (search results, products, stories) off the page as rows."""
        self.ensure()
        out = self.eval(f"({EXTRACT_JS})({json.dumps(query)})", 20) or ""
        # Remember each numbered list row's link: models often write <click n="3"/> meaning "row 3".
        self._extract_rows = {int(m.group(1)): m.group(2) for m in re.finditer(r"^(\d+)\. .*? -> (https?://\S+)$", out, re.M)}
        self._extract_url = self.url
        self._last_read = "extract"
        return out or "No tables or repeated lists found on this page. Read it with <page/> or use <js>."

    def extract_link(self, n: int):
        """Row n's link from the last extract on this page, but only when page element n doesn't exist."""
        rows = getattr(self, "_extract_rows", {})
        if n not in rows or getattr(self, "_extract_url", None) != self.url:
            return None
        if getattr(self, "_last_read", "") == "extract":
            return rows[n]          # the model's latest view of this page was the extracted rows
        try:
            if self.eval(f"!!document.querySelector('[data-lf-id=\"{int(n)}\"]')"):
                return None
        except Exception:
            pass
        return rows[n]

    def run_js(self, code: str) -> str:
        self.ensure()
        try:
            v = self.eval(code, repl=True)     # last expression is the result, like the browser console
        except RuntimeError as e:
            if "Illegal return" not in str(e):
                raise
            v = self.eval(f"(async () => {{\n{code}\n}})()")   # the script uses a top-level return
        if v is None:
            return "(script ran, returned nothing)"
        return v if isinstance(v, str) else json.dumps(v, indent=1)[:8000]

    def run_code(self, code: str, timeout=60):
        """Runs JavaScript on LocalForge's blank code page in the browser. Returns (ok, output)."""
        self.ensure()
        if self.sandbox_url and not self.url.startswith(self.sandbox_url):
            self.navigate(self.sandbox_url)
        mark = self.console_seq
        wrapped = "(async () => {\n" + code + "\n})()"
        ok = True
        try:
            v = self.eval(wrapped, timeout)
        except Exception as e:
            ok, v = False, None
            err = str(e)
        time.sleep(0.3)   # let late console calls and timers flush
        out = self.console_text(since=mark, limit=200)
        if not ok:
            out = (out + "\n" if out else "") + "Error: " + err
        elif v is not None:
            out = (out + "\n" if out else "") + "Returned: " + (v if isinstance(v, str) else json.dumps(v))
        if any(lv == "error" for (sq, lv, _) in list(self.console) if sq > mark):
            ok = False
        # Show the output on the page too, so the user sees it in the Browser tab.
        try:
            self.eval("window.__lfShow && window.__lfShow(" + json.dumps(out[-20000:] or "(no output)") + ", " + ("true" if ok else "false") + ")", 5)
        except Exception:
            pass
        return ok, out or "(no output)"

    def open_page(self, path_or_url: str, settle=1.5) -> str:
        """Opens a page (HTML file or URL), waits for scripts to run and reports text, elements and console."""
        p = Path(path_or_url)
        url = p.resolve().as_uri() if p.exists() else path_or_url
        self.navigate(url)
        time.sleep(settle)
        logs = self.console_text(limit=40)
        return self.describe(3500, console=False) + (f"\n\nConsole output:\n{logs}" if logs else "\n\nConsole: no output, no errors.")

    def fetch_raw(self, url: str, max_chars=8000) -> str:
        """Loads a URL in the browser (with the user's cookies/logins) and returns its raw text, e.g. JSON or CSV."""
        self.navigate(url)
        txt = self.eval("document.contentType + '\\n' + (document.body ? (document.querySelector('pre') && document.body.children.length === 1 ? document.querySelector('pre').innerText : document.body.innerText) : '')") or ""
        ctype, _, body = txt.partition("\n")
        cut = len(body) > max_chars
        return f"Fetched {self.url} ({ctype}, {len(body)} chars{', cut' if cut else ''}):\n{body[:max_chars]}"

    def search(self, query: str) -> str:
        """Web search in the real browser: Bing first, then DuckDuckGo (which sometimes shows a CAPTCHA)."""
        q = urllib.parse.quote(query)
        engines = [
            ("Bing", f"https://www.bing.com/search?q={q}&setlang=en", r"""JSON.stringify([...document.querySelectorAll('#b_results > li.b_algo')].slice(0, 10).map(r => {
                const a = r.querySelector('h2 a'); const s = r.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, .b_algoSlug');
                let href = a ? a.href : '';
                try { const u = new URL(href); const enc = u.searchParams.get('u');
                  if (u.hostname.endsWith('bing.com') && enc && enc.startsWith('a1')) {
                    const b = enc.slice(2).replace(/-/g, '+').replace(/_/g, '/'); href = atob(b + '==='.slice((b.length + 3) % 4)); } } catch (e) {}
                return { title: a ? a.innerText.trim() : '', url: href, snippet: s ? s.innerText.trim() : '' }; }))"""),
            ("DuckDuckGo", f"https://html.duckduckgo.com/html/?q={q}", r"""JSON.stringify([...document.querySelectorAll('.result')].slice(0, 10).map(r => {
                const a = r.querySelector('a.result__a'); const s = r.querySelector('.result__snippet');
                let href = a ? a.href : ''; try { const u = new URL(href); if (u.searchParams.get('uddg')) href = u.searchParams.get('uddg'); } catch (e) {}
                return { title: a ? a.innerText.trim() : '', url: href, snippet: s ? s.innerText.trim() : '' }; }))"""),
        ]
        generic = r"""JSON.stringify([...document.querySelectorAll('h2 a[href^="http"], h3 a[href^="http"], a[href^="http"] h3')]
            .map(x => x.closest('a')).filter((a, i, all) => a && all.indexOf(a) === i && !a.href.includes(location.hostname))
            .slice(0, 10).map(a => ({ title: a.innerText.trim(), url: a.href, snippet: '' })))"""
        for name, url, js in engines:
            try:
                self.navigate(url)
                items = [r for r in json.loads(self.eval(js) or "[]") if r.get("title") and r.get("url")]
                if not items:
                    items = [r for r in json.loads(self.eval(generic) or "[]") if r.get("title")]
            except Exception:
                items = []
            if items:
                return f'Web search results for "{query}" ({name}):\n' + "\n".join(
                    f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet'][:220]}" for i, r in enumerate(items))
        return f'No search results could be read for "{query}" (the search page may be asking for a CAPTCHA). ' \
               "Current page:\n" + self.describe(3000)

    def screenshot_wait(self, since: int, timeout=20):
        with self.frame_cond:
            if self.frame_seq == since:
                self.frame_cond.wait(timeout)
            return self.frame_seq, self.frame

    def state(self) -> dict:
        return {"running": self.running, "url": self.url, "title": self.title, "loading": self.loading,
                "frame": self.frame_seq, "width": self.width, "height": self.height,
                "available": bool(self.exe or find_browser()), "error": self.error}
