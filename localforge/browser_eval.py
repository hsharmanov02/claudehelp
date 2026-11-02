"""Real-model browser-task test: runs LocalForge's own agent loop against a small local test website and scores it.

LocalForge.exe --browser-eval [MODEL.gguf]   (uses the last loaded model; never touches your chat or settings)
python -m localforge.browser_eval CODE_DIR MODEL_GGUF LABEL   (CI: compare two versions of the code)
"""
import json, os, re, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


STATE = {"basket": [], "signup": None}
PRODUCTS = [("p1", "Red kettle 1.7L", 24.99), ("p2", "Blue kettle 1.5L", 31.50), ("p3", "Blue kettle compact 1.0L", 19.75),
            ("p4", "Blue toaster", 22.00), ("p5", "Steel kettle", 29.00), ("p6", "Blue kettle deluxe 1.7L", 44.00)]
NAV = "".join(f'<a href="/info/{i}">Help topic {i}</a> ' for i in range(40))   # lots of links, like real sites


def page(title, body):
    return f"<!doctype html><title>{title}</title><body><h1>{title}</h1>{body}<footer>{NAV}</footer></body>"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, html, code=200):
        b = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/":
            return self.send(page("KitchenHub shop", '<form action="/search"><input name="q" placeholder="Search products">'
                                  '<button>Search</button></form><p>Welcome! Use the search box to find products.</p>'))
        if u.path == "/search":
            words = (q.get("q", [""])[0]).lower().split()
            hits = [p for p in PRODUCTS if all(w in p[1].lower() for w in words)]
            rows = "".join(f'<li><a href="/product/{p[0]}">{p[1]}</a></li>' for p in hits) or "<li>No products found</li>"
            return self.send(page("Search results", f"<p>Prices are shown on each product page.</p><ul>{rows}</ul>"))
        if u.path.startswith("/product/"):
            p = next((p for p in PRODUCTS if p[0] == u.path.split("/")[-1]), None)
            if not p:
                return self.send(page("Not found", ""), 404)
            # The real button sits below a long description; a decoy "Add" link above does nothing.
            return self.send(page(p[1], f'<p class=price>Price: £{p[2]:.2f}</p><a href="#" onclick="return false">Add</a>'
                                  + "<p>Great product. </p>" * 40 +
                                  f'<form method=post action="/basket/add"><input type=hidden name=id value={p[0]}>'
                                  f'<button>Add to basket</button></form><a href="/basket">View basket</a>'))
        if u.path == "/basket":
            items = [p for p in PRODUCTS if p[0] in STATE["basket"]]
            rows = "".join(f"<li>{p[1]} - £{p[2]:.2f}</li>" for p in items) or "<li>Your basket is empty</li>"
            return self.send(page("Your basket", f"<ul>{rows}</ul><p>Basket total: £{sum(p[2] for p in items):.2f}</p>"))
        if u.path == "/signup":
            return self.send(page("Create account - step 1 of 2", '<form action="/signup2"><label>Full name <input name=name></label>'
                                  '<label>Email <input name=email type=email></label><button>Next</button></form>'))
        if u.path == "/signup2":
            hidden = "".join(f'<input type=hidden name={k} value="{v[0]}">' for k, v in q.items())
            return self.send(page("Create account - step 2 of 2", f'<form method=post action="/signup/done">{hidden}'
                                  '<label>Country <select name=country><option>Choose…</option><option>France</option>'
                                  '<option>Canada</option><option>Japan</option></select></label>'
                                  '<label><input type=checkbox name=terms> I accept the terms</label>'
                                  '<button>Create account</button></form>'))
        if u.path == "/status":
            # Results appear a moment after the page loads, like many real sites.
            return self.send(page("Service status", '<div id=s>Loading status…</div><script>setTimeout(() => '
                                  'document.getElementById("s").innerHTML = "<p>All systems normal. Incident ID: INC-4471. '
                                  'API rate limit: 900 requests per minute.</p>", 3500)</script>'))
        if u.path.startswith("/info/"):
            return self.send(page("Help", "<p>Nothing useful here.</p>"))
        self.send(page("Not found", ""), 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        f = urllib.parse.parse_qs(self.rfile.read(n).decode())
        if self.path == "/basket/add":
            STATE["basket"].append(f.get("id", [""])[0])
            return self.send(page("Added to basket", '<p>Item added.</p><a href="/basket">View basket</a>'))
        if self.path == "/signup/done":
            STATE["signup"] = {k: v[0] for k, v in f.items()}
            if "terms" not in f:
                return self.send(page("Error", "<p>You must accept the terms. Go back and tick the box.</p>"))
            return self.send(page("Account created", "<p>Welcome! Your confirmation code is LF-73920.</p>"))
        self.send(page("Not found", ""), 404)


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}"


BASE = ""

def tasks(BASE):
    return [
        ("shop", f"Go to {BASE}/ and add the cheapest blue kettle in the shop to my basket, then go to the basket and tell me the basket total.",
         lambda ans: (STATE["basket"] == ["p3"], "19.75" in ans)),
        ("signup", f"Go to {BASE}/signup and create an account: name Alex Doe, email alex@example.com, country Canada, accept the terms. "
                   "Then tell me the confirmation code.",
         lambda ans: (bool(STATE["signup"]) and STATE["signup"].get("name") == "Alex Doe" and STATE["signup"].get("email") == "alex@example.com"
                      and STATE["signup"].get("country") == "Canada" and "terms" in STATE["signup"], "LF-73920" in ans)),
        ("status", f"Open {BASE}/status and tell me the incident ID and the API rate limit.",
         lambda ans: (True, "INC-4471" in ans and "900" in ans)),
    ]
    



def run_eval(agent, model, label, log=print, limit=2700):
    base = serve()
    app = agent.App()
    app.settings.set("autoApprove", True)
    app.settings.set("browserAutoApprove", True)
    app.settings.set("nCtx", 16384)
    app.settings.set("foundModels", [str(model)])
    m = app.store.find(str(model)) or app.store.find(Path(model).name) or next(iter(app.store.local_models()), None)
    assert m and app.load_model(m["path"], wait=True), (app.status, app.store.local_models())
    log(f"=== {label}: {app.status}")
    app.browser.ensure()          # fail fast if the browser can't start on this machine
    log(f"browser ok: {app.browser.exe}")
    score, total = 0, 0
    for name, task, check in tasks(base):
        STATE.update(basket=[], signup=None)
        app.clear_chat()
        t0 = time.time()
        app.send(task)
        time.sleep(1)
        while app.generating:
            if time.time() - t0 > limit:
                app.stop()
            time.sleep(1)
        answers = [m for m in app.messages if m["role"] == "assistant" and m["text"].strip()]
        final = answers[-1]["text"] if answers else ""
        did, said = check(final)
        ok = did and said
        score += ok
        total += 1
        log(f"\n##### {label} {name}: {'PASS' if ok else 'FAIL'} (did it: {did}, answer right: {said}) "
            f"{len(answers)} replies, {time.time() - t0:.0f}s")
        for m in app.messages:
            t = m["text"].replace("\n", " ⏎ ")
            log(f"  [{m['role']}] {(m.get('stats') or '')} {t[:400]}")
    log(f"\nSCORE {label}: {score}/{total}")
    app.engine.unload()
    app.browser.close()
    return score, total


def run_installed(model=None) -> int:
    """For LocalForge.exe --browser-eval: a separate data folder so the user's chat and settings stay as they are."""
    from . import config
    real = json.loads((config.data_dir() / "settings.json").read_text(encoding="utf-8")) if (config.data_dir() / "settings.json").exists() else {}
    model = model or real.get("lastModel")
    if not model or not Path(model).exists():
        print("No model: pass a .gguf path or load one in LocalForge first.")
        return 2
    home = config.data_dir() / "browser-eval"
    home.mkdir(parents=True, exist_ok=True)
    keep = {k: real[k] for k in ("backend", "gpuLayers", "threads", "thinkBrowser") if k in real}
    (home / "settings.json").write_text(json.dumps(keep), encoding="utf-8")
    os.environ["LOCALFORGE_HOME"] = str(home)
    out = open(Path(os.environ.get("LOCALFORGE_EVAL_LOG") or (home / "browser-eval.txt")), "w", encoding="utf-8", buffering=1)

    def log(line):
        out.write(line + "\n")
        try:
            print(line, flush=True)
        except Exception:
            pass
    from . import agent
    try:
        score, total = run_eval(agent, model, Path(model).name, log, limit=900)
    except Exception as e:
        log(f"FAILED: {e!r}")
        return 1
    finally:
        out.close()
    return 0 if score == total else 3


def main():
    code = Path(sys.argv[1]).resolve()
    model, label = sys.argv[2], sys.argv[3]
    sys.path.insert(0, str(code))
    import importlib
    agent = importlib.import_module("localforge.agent")
    score, _ = run_eval(agent, model, label, lambda l: print(l, flush=True))
    Path("score.txt").write_text(f"{label} {score}\n")


if __name__ == "__main__":
    main()
