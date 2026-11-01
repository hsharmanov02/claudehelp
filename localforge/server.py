"""Local web server for the UI. Only listens on 127.0.0.1 and requires a per-launch token."""
import json
import mimetypes
import os
import secrets
import shutil
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, tools

TOKEN = secrets.token_urlsafe(24)
MAX_EDIT = 2 * 1024 * 1024


def make_handler(app, pick_file=None):
    web = config.resource_dir() / "web"

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode("utf-8"))

        def _authed(self, q=None):
            t = self.headers.get("X-LF-Token") or (q or {}).get("t", [None])[0]
            return t == TOKEN

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                html = (web / "index.html").read_text(encoding="utf-8").replace("__LF_TOKEN__", TOKEN)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if u.path.startswith("/static/"):
                f = (web / u.path[len("/static/"):]).resolve()
                if web.resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    return self._send(200, f.read_bytes(), ctype)
                return self._send(404, b"not found", "text/plain")
            if not self._authed(q):
                return self._send(403, b"forbidden", "text/plain")
            if u.path == "/api/state":
                since = int(q.get("v", ["-1"])[0])
                deadline = time.time() + 20
                while app.version == since and time.time() < deadline:
                    time.sleep(0.05)
                return self._json(app.state())
            if u.path == "/api/messages":
                return self._json({"version": app.version, "messages": app.messages})
            if u.path == "/api/files":
                return self._json(list_dir(q.get("dir", [str(Path.home())])[0]))
            if u.path == "/api/file":
                return self._json(read_file(q.get("path", [""])[0]))
            if u.path == "/preview":
                p = Path(q.get("path", [""])[0])
                if p.is_file() and p.suffix.lower() in (".html", ".htm"):
                    return self._send(200, p.read_bytes(), "text/html; charset=utf-8")
                return self._send(404, b"not found", "text/plain")
            return self._send(404, b"not found", "text/plain")

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            if not self._authed():
                return self._send(403, b"forbidden", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            try:
                result = route(app, u.path.removeprefix("/api/"), body, pick_file)
            except Exception as e:
                result = {"error": str(e)}
            return self._json(result if result is not None else {"ok": True})

    return H


def route(app, action, b, pick_file):
    if action == "settings":
        app.update_settings(b)
    elif action == "scan":
        app.scan_pc()
    elif action == "search":
        app.search(b.get("query", ""))
    elif action == "repo":
        app.open_repo(b.get("repo"))
    elif action == "download":
        app.download(b["file"])
    elif action == "cancel-download":
        app.store.cancel(b["name"])
        app.touch()
    elif action == "load":
        app.load_model(b["path"])
    elif action == "unload":
        app.unload()
    elif action == "delete-model":
        if app.engine.model_path == b["path"]:
            app.unload()
        app._set_status(app.store.delete(b["path"]))
    elif action == "add-model":
        path = b.get("path") or (pick_file and pick_file(["GGUF models (*.gguf)"]))
        if path:
            app.store.add_file(path)
            app.touch()
    elif action == "send":
        app.send(b.get("text", ""), b.get("attachment"))
    elif action == "stop":
        app.stop()
    elif action == "clear":
        app.clear_chat()
    elif action == "approve":
        app.approve(int(b["msg"]), int(b["idx"]))
    elif action == "reject":
        app.reject(int(b["msg"]), int(b["idx"]))
    elif action == "run-snippet":
        app.run_snippet(b["lang"], b["code"])
    elif action == "close-preview":
        app.preview = None
        app.touch()
    elif action == "pick-file":
        return {"path": pick_file(["All files (*.*)"]) if pick_file else None}
    elif action == "open-path":
        return {"result": tools.open_with_system(b["path"])}
    elif action == "reindex":
        threading.Thread(target=lambda: (app.index.build(), app.touch()), daemon=True).start()
    elif action == "quit":
        def bye():
            time.sleep(0.3)
            app.engine.unload()
            os._exit(0)
        threading.Thread(target=bye, daemon=True).start()
    elif action == "file-op":
        return file_op(b)
    else:
        return {"error": f"unknown action {action}"}
    return {"ok": True}


def list_dir(d: str) -> dict:
    if d == "::drives":
        from .models import drives
        return {"dir": "::drives", "parent": None,
                "entries": [{"name": str(x), "path": str(x), "dir": True, "size": 0, "mtime": 0} for x in drives()]}
    p = Path(d)
    out = []
    try:
        for e in os.scandir(p):
            try:
                st = e.stat()
                out.append({"name": e.name, "path": e.path, "dir": e.is_dir(), "size": st.st_size, "mtime": st.st_mtime,
                            "hidden": e.name.startswith(".")})
            except OSError:
                out.append({"name": e.name, "path": e.path, "dir": False, "size": 0, "mtime": 0})
    except OSError as ex:
        return {"dir": str(p), "parent": str(p.parent) if p.parent != p else "::drives", "entries": [], "error": str(ex)}
    out.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    parent = str(p.parent) if p.parent != p else ("::drives" if os.name == "nt" else None)
    return {"dir": str(p), "parent": parent, "entries": out}


def read_file(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"error": "Not a file"}
    if p.stat().st_size > MAX_EDIT:
        return {"path": str(p), "readOnly": True, "text": f"File is too large to edit here ({p.stat().st_size // 1024} KB)."}
    data = p.read_bytes()
    if b"\x00" in data[:4000]:
        return {"path": str(p), "readOnly": True, "text": "This looks like a binary file and can't be shown as text."}
    return {"path": str(p), "readOnly": False, "text": data.decode("utf-8", "replace")}


def file_op(b: dict) -> dict:
    op, path = b.get("op"), Path(b.get("path", ""))
    try:
        if op == "create":
            f = path / b["name"].strip()
            if f.exists():
                return {"message": f"{f.name} already exists"}
            f.touch()
            return {"message": f"Created {f.name}"}
        if op == "mkdir":
            f = path / b["name"].strip()
            f.mkdir(parents=True)
            return {"message": f"Created folder {f.name}"}
        if op == "rename":
            t = path.with_name(b["name"].strip())
            if t.exists():
                return {"message": f"{t.name} already exists"}
            path.rename(t)
            return {"message": f"Renamed to {t.name}"}
        if op == "delete":
            if len(path.resolve().parts) <= 2:
                return {"message": "Refused to delete a drive or top-level folder"}
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            return {"message": f"Deleted {path.name}"}
        if op == "save":
            path.write_text(b["text"], encoding="utf-8")
            return {"message": f"Saved {path.name}"}
    except Exception as e:
        return {"message": f"Error: {e}"}
    return {"message": "Unknown operation"}


def serve(app, port=0, pick_file=None):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, pick_file))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
