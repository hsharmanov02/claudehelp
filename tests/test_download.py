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
        start, end = 0, len(DATA) - 1
        if rng:
            a, b = rng.split("=")[1].split("-")
            start, end = int(a), (int(b) if b else len(DATA) - 1)
        body = DATA[start:end + 1]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
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


def test_parallel_download_and_resume(server, tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(hf, "SEGMENT_MIN", 1)
    t = tmp_path / "big.gguf"
    dl(t)
    assert t.read_bytes() == DATA and not (tmp_path / "big.gguf.segs").exists()
    t.unlink()
    # Half-finished parallel download: each segment got some bytes.
    step = len(DATA) // hf.SEGMENTS
    segs = [[i * step, len(DATA) - 1 if i == hf.SEGMENTS - 1 else (i + 1) * step - 1, 1000] for i in range(hf.SEGMENTS)]
    buf = bytearray(len(DATA))
    for a, b, d in segs:
        buf[a:a + d] = DATA[a:a + d]
    (tmp_path / "big.gguf.part").write_bytes(bytes(buf))
    (tmp_path / "big.gguf.segs").write_text(json.dumps(segs))
    dl(t)
    assert t.read_bytes() == DATA
