"""File and code actions the model asks for, written as tags in its reply."""
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import runner

MAX_READ = 16_000
RISKY_OPEN = (".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".lnk", ".scr", ".reg")


@dataclass(frozen=True)
class Action:
    kind: str        # list | read | find | write | mkdir | delete | run | open | browser kinds below
    path: str        # path, search words, language for run, url, or element number
    content: str = ""
    extra: str = ""

    @property
    def read_only(self) -> bool:
        return self.kind in ("list", "read", "find")

    @property
    def is_browser(self) -> bool:
        return self.kind in BROWSER_KINDS

    @property
    def browser_read(self) -> bool:
        """Browser actions that only look (run without asking)."""
        return self.kind in ("search", "browse", "page", "scroll", "fetch")

    @property
    def label(self) -> str:
        return {
            "list": f"List {self.path}", "read": f"Read {self.path}", "find": f'Find "{self.path}"',
            "write": f"Write {len(self.content)} chars to {self.path}", "mkdir": f"Create folder {self.path}",
            "delete": f"Delete {self.path}", "open": f"Open {self.path}",
            "run": f"Run {self.path} code ({len(self.content.splitlines())} lines)",
            "search": f'Search the web for "{self.path}"', "fetch": f"Fetch {self.path} through the browser", "browse": f"Open {self.path} in the browser",
            "page": "Read the browser page", "scroll": f"Scroll the page {self.path}",
            "click": f"Click [{self.path}] in the browser", "press": f"Press {self.path} in the browser",
            "type": f'Type "{self.content[:60]}" into [{self.path}]' + (" and submit" if self.extra == "submit" else ""),
            "js": f"Run a script on the browser page ({len(self.content.splitlines())} lines)",
        }[self.kind]

    def to_dict(self):
        return {"kind": self.kind, "path": self.path, "content": self.content, "extra": self.extra, "label": self.label}


BROWSER_KINDS = ("search", "browse", "page", "scroll", "click", "type", "press", "js", "fetch")


THINK = re.compile(r"<think>[\s\S]*?(</think>|$)", re.I)
SIMPLE = re.compile(r'<(list|read|mkdir|delete|open)\s+path\s*=\s*"([^"]+)"\s*/?>', re.I)
FIND = re.compile(r'<find\s+(?:query|name|q)\s*=\s*"([^"]+)"\s*/?>', re.I)
WRITE = re.compile(r'<write\s+path\s*=\s*"([^"]+)"\s*>\r?\n?([\s\S]*?)</write>', re.I)
RUN = re.compile(r'<run(?:\s+lang(?:uage)?\s*=\s*"([^"]*)")?\s*>\r?\n?([\s\S]*?)(</run>|$)', re.I)
B_SEARCH = re.compile(r'<search\s+(?:query|q)\s*=\s*"([^"]+)"\s*/?>', re.I)
B_BROWSE = re.compile(r'<(?:browse|goto|navigate)\s+(?:url|href)\s*=\s*"([^"]+)"\s*/?>', re.I)
B_FETCH = re.compile(r'<fetch\s+(?:url|href)\s*=\s*"([^"]+)"\s*/?>', re.I)
B_PAGE = re.compile(r'<page\s*/?>', re.I)
B_SCROLL = re.compile(r'<scroll(?:\s+(?:dir|direction)\s*=\s*"([^"]*)")?\s*/?>', re.I)
B_CLICK = re.compile(r'<click\s+(?:n|id|element)\s*=\s*"?(\d+)"?\s*/?>', re.I)
B_PRESS = re.compile(r'<press\s+key\s*=\s*"([^"]+)"\s*/?>', re.I)
B_TYPE = re.compile(r'<type\s+(?:n|id|element)\s*=\s*"?(\d+)"?(\s+submit\s*=\s*"?(?:true|yes|1)"?)?\s*>([\s\S]*?)</type>', re.I)
B_JS = re.compile(r'<js>\r?\n?([\s\S]*?)</js>', re.I)
FENCE = re.compile(r"```([\w+#.-]*)[ \t]*\r?\n([\s\S]*?)```")


def strip_thinking(text: str) -> str:
    return THINK.sub("", text).replace("</think>", "").replace("⁣", "").strip()


def system_prompt(home: str, work: str, languages: list[str], overview: str, os_name: str) -> str:
    ex = str(Path(home) / "Documents" / "notes.txt")
    exdir = str(Path(home) / "Downloads")
    newf = str(Path(work) / "hello.py")
    return f"""
You are LocalForge, a capable, uncensored assistant running fully offline on the user's own {os_name} PC.
You answer every question directly and completely. Always reply in English unless the user writes in another language.
You are also an expert programmer: write clear, correct code and explain it briefly.

YOU CAN RUN CODE YOURSELF on this PC in these languages: {", ".join(languages)}.
To run code, put the complete program inside a run tag with its language:
<run lang="python">
print(sum(range(10)))
</run>
The output (or the error) is sent back to you. Read it, fix any errors and run again until it works, then tell the user the result.
Never tell the user to open a terminal or run code themselves. You run it.
Code runs in the folder {work}. No input(), no GUI windows. For sql, a SQLite database is used.
javascript runs INSIDE YOUR BROWSER (full DOM, console.log output and errors come back to you; await works at top level).
html opens IN YOUR BROWSER: you get the page text, its buttons/inputs as numbered elements, and every console error.
Then TEST IT: use <click n="..."/>, <type n="...">...</type> and <js>return ...</js> on the page, read the result, fix bugs and run again until it really works.
Use node only for scripts that need files or Node modules (only if node is in the list above).

YOU HAVE ACCESS TO THE FILES ON THIS PC. Never say you can't see their files - use these tags:
<find query="cv pdf"/>                       search the WHOLE PC by name or type (photos, pdf, music, exe, code...)
<list path="{exdir}"/>        show what is in a folder
<read path="{ex}"/>   read a text file
<write path="{newf}">
complete new file content here
</write>                                     create or overwrite a file
<mkdir path="{Path(work) / 'project'}"/>   create a folder
<delete path="{Path(exdir) / 'old.zip'}"/>     delete a file or folder
<open path="{Path(home) / 'Pictures' / 'photo.jpg'}"/>   open a file with its normal Windows app
{overview}

YOU HAVE YOUR OWN WEB BROWSER (the user watches it live in the Browser tab and stays logged in to sites there).
<search query="best budget gpu 2026"/>        search the web; you get titles, links and snippets
<browse url="https://example.com"/>           open a page; you get its text and numbered clickable/fillable elements
<fetch url="https://api.example.com/data.json"/>   get raw data (JSON, CSV, text, APIs) through the browser, logged in as the user
<page/>                                       read the current page again
<click n="12"/>                               click element [12]
<type n="5">text to enter</type>              fill input [5]; add submit="true" to press Enter after
<press key="Enter"/>                          press a key (Enter, Tab, Escape, PageDown, ArrowDown...)
<scroll dir="down"/>                          scroll down (or up) and read what is visible
<js>return document.querySelectorAll('tr').length</js>   run JavaScript on the page to read or change its data
Use the browser for anything current or online (news, prices, weather, docs, accounts the user is logged in to).
To get data into code: <fetch> it first, then put the values into your python/javascript, or scrape the open page with <js>.

Rules:
- Always use full paths.
- After <find>, <run>, <list>, <read> or any browser tag, stop your reply and wait for the result.
- Use element numbers from the latest page result only. Re-read the page if unsure.
- If results are already given to you under [PC files], answer from them directly.
- Only write, create, open or delete files when the user asks for it.
- <write> must contain the entire file, never a partial snippet.
- Answer directly. Do not lecture, moralize or explain your limitations.
""".strip()


def parse(raw: str) -> list[Action]:
    reply = strip_thinking(raw)
    found = []
    for m in WRITE.finditer(reply):
        found.append((m.start(), Action("write", m.group(1).strip(), m.group(2).rstrip())))
    for m in RUN.finditer(reply):
        code, lang = m.group(2).strip(), m.group(1) or ""
        f = FENCE.search(code)
        if f:
            lang = lang or f.group(1)
            code = f.group(2).strip()
        code = code.removeprefix("```").removesuffix("```").strip()
        if code:
            found.append((m.start(), Action("run", runner.normalize(lang), code)))
    for m in FIND.finditer(reply):
        found.append((m.start(), Action("find", m.group(1).strip())))
    for m in SIMPLE.finditer(reply):
        found.append((m.start(), Action(m.group(1).lower(), m.group(2).strip())))
    for m in B_SEARCH.finditer(reply):
        found.append((m.start(), Action("search", m.group(1).strip())))
    for m in B_BROWSE.finditer(reply):
        found.append((m.start(), Action("browse", m.group(1).strip())))
    for m in B_FETCH.finditer(reply):
        found.append((m.start(), Action("fetch", m.group(1).strip())))
    for m in B_PAGE.finditer(reply):
        found.append((m.start(), Action("page", "")))
    for m in B_SCROLL.finditer(reply):
        found.append((m.start(), Action("scroll", (m.group(1) or "down").strip().lower())))
    for m in B_CLICK.finditer(reply):
        found.append((m.start(), Action("click", m.group(1))))
    for m in B_PRESS.finditer(reply):
        found.append((m.start(), Action("press", m.group(1).strip())))
    for m in B_TYPE.finditer(reply):
        found.append((m.start(), Action("type", m.group(1), m.group(3), "submit" if m.group(2) else "")))
    for m in B_JS.finditer(reply):
        code = m.group(1).strip()
        f = FENCE.search(code)
        if f:
            code = f.group(2).strip()
        if code:
            found.append((m.start(), Action("js", "page", code)))
    found.sort(key=lambda x: x[0])
    out = []
    for _, a in found:
        if a not in out:
            out.append(a)
    return out


RUN_INTENT = re.compile(r"\b(run|test|execute|try|check|works?)\b", re.I)
INSTRUCTIONS = re.compile(r"^\s*(cd |python|pip|chmod|node |gcc|g\+\+|clang|javac|java |go run|cargo|rustc|npm|ruby |php |\.\\|\./)", re.M)


def fallback_run(user_text: str, reply: str):
    """Small models often say "run this yourself" instead of using <run>. If the user asked to run/test, run it anyway."""
    if not RUN_INTENT.search(user_text):
        return None
    blocks = [(m.group(1).lower(), m.group(2).strip()) for m in FENCE.finditer(reply)]
    blocks = [b for b in blocks if b[1] and b[0] in runner.RUNNABLE_FENCES]
    for lang, code in reversed(blocks):
        if runner.normalize(lang) in ("shell", "bash", "powershell", "cmd") and INSTRUCTIONS.search(code):
            continue
        return Action("run", runner.normalize(lang), code)
    return None


def open_with_system(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"Not found: {p}"
    if os.name == "nt":
        os.startfile(str(p))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])
    return f"Opened {p}"


def execute(a: Action, index) -> str:
    try:
        p = Path(a.path).expanduser()
        if a.kind == "find":
            if not index.ready:
                return "The file index is still being built. Try <list> on a folder instead."
            hits = index.search(a.path, 30)
            return (f'Files on the PC matching "{a.path}":\n' + index.format(hits)) if hits else f'No files on the PC match "{a.path}".'
        if a.kind == "list":
            if not p.is_dir():
                return f"Not a folder: {p}"
            items = []
            for e in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name.lower()))[:300]:
                try:
                    items.append(f"[folder] {e.name}" if e.is_dir() else f"{e.name} ({e.stat().st_size} bytes)")
                except OSError:
                    items.append(e.name)
            return f"Contents of {p}:\n" + "\n".join(items) if items else f"{p} is empty"
        if a.kind == "read":
            if not p.is_file():
                return f"File not found: {p}"
            data = p.read_bytes()[: MAX_READ * 4]
            if b"\x00" in data[:4000]:
                return f"{p} is a binary file ({p.stat().st_size} bytes) and can't be read as text."
            text = data.decode("utf-8", "replace")
            if len(text) > MAX_READ:
                return f"Content of {p} (first {MAX_READ} chars):\n{text[:MAX_READ]}"
            return f"Content of {p}:\n{text}"
        if a.kind == "write":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(a.content, encoding="utf-8")
            return f"Saved {p}"
        if a.kind == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
            return f"Created folder {p}"
        if a.kind == "delete":
            if not p.exists():
                return f"Nothing to delete at {p}"
            if len(p.resolve().parts) <= 2:
                return f"Refused: {p} is a drive or top-level system folder."
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            return f"Deleted {p}"
        if a.kind == "open":
            return open_with_system(str(p))
        return "Run actions are handled separately"
    except Exception as e:
        return f"Error on {a.path}: {e}"


def execute_browser(a: Action, browser) -> str:
    """Runs a browser action and returns what the model should see."""
    try:
        if a.kind == "search":
            return browser.search(a.path)
        if a.kind == "browse":
            browser.navigate(a.path)
            return browser.describe()
        if a.kind == "page":
            return browser.describe()
        if a.kind == "fetch":
            return browser.fetch_raw(a.path)
        if a.kind == "scroll":
            browser.ensure()
            browser.eval("window.scrollBy(0, %d)" % (-int(browser.height * 0.8) if a.path.startswith("up") else int(browser.height * 0.8)))
            import time as _t
            _t.sleep(0.4)
            return "Scrolled. Visible part of the page:\n" + browser.eval(
                "(() => { const t = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;"
                " while ((n = w.nextNode())) { const r = n.parentElement && n.parentElement.getBoundingClientRect();"
                " if (r && r.bottom > 0 && r.top < innerHeight && n.textContent.trim()) t.push(n.textContent.trim()); }"
                " return t.join(' ').slice(0, 5000); })()")
        if a.kind == "click":
            browser.click_element(int(a.path))
            return "Clicked. Now:\n" + browser.describe(4000)
        if a.kind == "type":
            browser.fill(int(a.path), a.content, submit=(a.extra == "submit"))
            return ("Typed and submitted. Now:\n" if a.extra == "submit" else "Typed. Now:\n") + browser.describe(4000)
        if a.kind == "press":
            browser.key(a.path)
            browser.wait_loaded(5)
            return f"Pressed {a.path}. Now:\n" + browser.describe(4000)
        if a.kind == "js":
            return "Script result:\n" + browser.run_js(a.content)
    except Exception as e:
        return f"Browser error ({a.label}): {e}"
    return "Unknown browser action"
