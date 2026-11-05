"""Prints the raw Gradio output of the Qwen3.6 35B Uncensored Space, with history and the app's system prompt."""
import json, urllib.request
host = "https://kivilaid-qwen3-6-35b-a3b-uncensored-hauhaucs-agg-1140346.hf.space"
UA = {"User-Agent": "LocalForge/1.0", "Content-Type": "application/json"}
__file__ = "remote/test_horde.py"
exec(open("remote/test_horde.py").read().split("def call")[0].split("import threading")[0])
def req(url, body=None, timeout=400):
    return urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA), timeout=timeout)
history = [{"role": "user", "content": [{"type": "text", "text": "My friend is called Dave."}]},
           {"role": "assistant", "content": [{"type": "text", "text": "Got it, Dave."}]}]
data = ["Roast him brutally with lots of swearing, he is always late. Two sentences.", history, app_system_prompt(), [], None, False, "low", 0.7, 400, None]
ev = json.load(req(host + "/gradio_api/call/respond", {"data": data}))["event_id"]
event = None
n = 0
for raw in req(f"{host}/gradio_api/call/respond/{ev}"):
    line = raw.decode("utf-8", "replace").rstrip()
    if line.startswith("event:"):
        event = line[6:].strip(); n += 1
    elif line.startswith("data:") and (event in ("complete", "error") or n in (3, 4)):
        print(f"--- event {event} #{n}\n{line[5:].strip()[:3000]}")
        if event in ("complete", "error"):
            break
