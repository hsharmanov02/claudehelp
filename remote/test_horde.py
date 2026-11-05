"""Tries AI Horde (aihorde.net) with the anonymous key: free, no sign-up, volunteer GPUs.
Prints what the text workers allow (context, reply length), then checks a long agent-sized prompt."""
import json, time, urllib.error, urllib.request

API = "https://aihorde.net/api/v2"
H = {"User-Agent": "LocalForge-test/1.0", "apikey": "0000000000", "Client-Agent": "LocalForge:1.0:github.com/hsharmanov02/claudehelp",
     "Content-Type": "application/json"}


def call(path, body=None, method=None):
    r = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, headers=H, method=method)
    try:
        return json.load(urllib.request.urlopen(r, timeout=60))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: HTTP {e.code}: {e.read()[:400]!r}")


workers = call("/workers?type=text")
print(f"{len(workers)} text workers")
for w in sorted(workers, key=lambda w: w.get("models", [""])[0]):
    print(f"  ctx {w.get('max_context_length'):>6}  reply {w.get('max_length'):>5}  speed {w.get('performance')}  "
          f"trusted {w.get('trusted')}  {', '.join(w.get('models', []))}")

print("\nuser info for anonymous:", {k: v for k, v in call("/find_user").items() if k in ("username", "kudos", "concurrency")}
      if True else "")

filler = "The quick brown fox jumps over the lazy dog. " * 600   # about 6000 tokens
for model, ctx in [("koboldcpp/gemma-4-31B-it-heretic", 8192), ("koboldcpp/L3-8B-Stheno-v3.2", 8192)]:
    t = time.time()
    try:
        prompt = f"<start_of_turn>user\n{filler}\nIgnore the text above. Reply with exactly: LocalForge works<end_of_turn>\n<start_of_turn>model\n"
        job = call("/generate/text/async", {"prompt": prompt, "models": [model],
                   "params": {"max_length": 512, "max_context_length": ctx, "temperature": 0.3}})
        print(f"\n[{model}] long prompt job: {job}")
        while True:
            time.sleep(3)
            c = call(f"/generate/text/check/{job['id']}")
            if c.get("done") or c.get("faulted") or not c.get("is_possible", True) or time.time() - t > 400:
                print("check:", c)
                break
        s = call(f"/generate/text/status/{job['id']}")
        g = (s.get("generations") or [{}])[0]
        print(f"{time.time() - t:.0f}s faulted={s.get('faulted')} worker={g.get('worker_name')} -> {g.get('text', '')[:200]!r}")
    except Exception as e:
        print(f"[{model}] error {e}")
