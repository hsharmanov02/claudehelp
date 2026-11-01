import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from localforge import hf

DATA = bytes(range(256)) * 4096   # 1 MiB


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        start = int(rng.split("=")[1].split("-")[0]) if rng else 0
        body = DATA[start:]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(hf, "download_url", lambda repo, path: f"http://127.0.0.1:{srv.server_address[1]}/f")
    yield
    srv.shutdown()


def dl(target):
    hf.download_part("r", "f.gguf", target, None, threading.Event(), lambda n, reset=False: None, len(DATA))


def test_fresh_resume_and_oversized(server, tmp_path):
    t = tmp_path / "m.gguf"
    dl(t)
    assert t.read_bytes() == DATA
    t.unlink()
    (tmp_path / "m.gguf.part").write_bytes(DATA[:300_000])          # partial -> resume
    dl(t)
    assert t.read_bytes() == DATA
    t.unlink()
    (tmp_path / "m.gguf.part").write_bytes(DATA + b"x" * 5000)       # corrupt, too big -> restart
    dl(t)
    assert t.read_bytes() == DATA
    assert not (tmp_path / "m.gguf.lock").exists()


def test_second_writer_refused(server, tmp_path):
    t = tmp_path / "m.gguf"
    (tmp_path / "m.gguf.lock").write_text("123")
    with pytest.raises(RuntimeError, match="already downloading"):
        dl(t)
