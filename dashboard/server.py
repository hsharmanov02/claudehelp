"""Read-only localhost dashboard; Python standard library, no downloaded UI assets."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

STATIC = Path(__file__).resolve().parent / "static"

def create_dashboard_server(hub, port=8899):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                self.send_error(403)
                return
            path = urlsplit(self.path).path
            routes = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/static/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/static/app.js": ("app.js", "application/javascript; charset=utf-8")}
            if path == "/api/status":
                body = json.dumps(hub.get_full_state(), allow_nan=False).encode()
                mime = "application/json"
            elif path in routes:
                name, mime = routes[path]
                body = (STATIC / name).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server

