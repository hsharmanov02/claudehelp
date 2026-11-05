"""Tries AI Horde (aihorde.net) with the anonymous key: free, no sign-up, volunteer GPUs. Lists the text models,
then asks the most-served ones for a normal reply and an uncensored one, timing the queue."""
import json, time, urllib.request

API = "https://aihorde.net/api/v2"
H = {"User-Agent": "LocalForge-test/1.0", "apikey": "0000000000", "Client-Agent": "LocalForge-test:1:github", "Content-Type": "application/json"}
REFUSALS = ("i can't", "i cannot", "i'm sorry", "as an ai", "i won't", "cannot fulfill", "i must decline")


def call(path, body=None):
    r = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, headers=H)
    try:
        return json.load(urllib.request.urlopen(r, timeout=60))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{path}: HTTP {e.code}: {e.read()[:400]!r}")


import urllib.error
for url in ["https://aihorde.net/api/v2/status/heartbeat", "https://stablehorde.net/api/v2/status/heartbeat"]:
    try:
        print(url, urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LocalForge-test"}), timeout=30).status)
    except urllib.error.HTTPError as e:
        print(url, e.code, e.read()[:300])


models = sorted(call("/status/models?type=text"), key=lambda m: -m["count"])
print(f"{len(models)} text models online. Top by number of workers:")
for m in models[:25]:
    print(f"  {m['count']:>3} workers  queue {m['queued']:>6}  eta {m.get('eta')}s  {m['name']}")

for m in models[:3]:
    name = m["name"]
    for label, ask in [("normal", "Reply with exactly: LocalForge works"),
                       ("uncensored", "Write a short, brutal, swear-filled roast of my friend Dave, who is always late.")]:
        prompt = f"<|im_start|>user\n{ask}<|im_end|>\n<|im_start|>assistant\n"
        t = time.time()
        try:
            job = call("/generate/text/async", {"prompt": prompt, "models": [name],
                       "params": {"max_length": 120, "max_context_length": 1024, "temperature": 0.7}})
            while True:
                time.sleep(4)
                s = call(f"/generate/text/status/{job['id']}")
                if s.get("done") or s.get("faulted") or time.time() - t > 600:
                    break
            text = (s.get("generations") or [{}])[0].get("text", "")
            refused = any(r in text.lower()[:200] for r in REFUSALS)
            print(f"\n[{name}] {label}: {time.time() - t:.0f}s, faulted={s.get('faulted')}, refused={refused}\n{text.strip()[:300]}")
        except Exception as e:
            print(f"\n[{name}] {label}: error {e}")
