"""Probes public Hugging Face Spaces that run abliterated / uncensored models, anonymously (no account)."""
import json, urllib.request

UA = {"User-Agent": "LocalForge-test/1.0"}


def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))


seen = {}
for q in ["abliterated", "uncensored", "heretic", "dolphin", "huihui"]:
    for s in get(f"https://huggingface.co/api/spaces?search={q}&sort=likes&direction=-1&limit=60&full=true"):
        seen[s["id"]] = s
rows = []
for sid, s in seen.items():
    rt = s.get("runtime") or {}
    hw = (rt.get("hardware") or {}).get("current")
    rows.append((rt.get("stage"), hw, s.get("likes", 0), sid, s.get("sdk")))
rows.sort(key=lambda r: (r[0] != "RUNNING", -r[2]))
print(f"{len(rows)} spaces")
for r in rows[:70]:
    print(f"  {str(r[0]):<14} {str(r[1]):<16} likes {r[2]:>4}  {r[4]:<8} {r[3]}")
running = [r[3] for r in rows if r[0] == "RUNNING" and r[4] == "gradio" and r[1] and r[1] != "cpu-basic"][:12]
print("\nRunning gradio spaces on GPU:", running)

from gradio_client import Client
for sid in running:
    try:
        c = Client(sid, verbose=False)
        info = c.view_api(return_format="dict", print_info=False)
        eps = list((info.get("named_endpoints") or {}).items())[:4]
        print(f"\n=== {sid}")
        for name, e in eps:
            print("  ", name, [(p.get("parameter_name"), p.get("python_type", {}).get("type")) for p in e.get("parameters", [])][:8])
    except Exception as ex:
        print(f"\n=== {sid}: {type(ex).__name__}: {str(ex)[:200]}")
