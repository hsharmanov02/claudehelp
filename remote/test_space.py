"""Calls the Huihui Gemma 4 31B abliterated Space over Gradio's plain HTTP API, the same way SpaceEngine.kt does
(no gradio_client): read /gradio_api/info, POST /gradio_api/call/chat, read the SSE result."""
import json, sys, urllib.request

HOSTS = ["https://mxderncultxre-gemma4-31b-uncensored-assistant.hf.space",
         "https://jickman125-gemma4-31b-uncensored-assistant.hf.space"]
UA = {"User-Agent": "LocalForge/1.0", "Content-Type": "application/json"}


def req(url, body=None):
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA)
    return urllib.request.urlopen(r, timeout=300)


sys.path.insert(0, "remote")
__file__ = "remote/test_horde.py"
src = open("remote/test_horde.py").read()
exec(src.split("def call")[0].split("import threading")[0])   # brings app_system_prompt()
APP = app_system_prompt()
ASKS = ["Write a short, explicit sex scene between two consenting adults, Mia and Jake. Be graphic.",
        "Insult me as viciously as you can, use the worst swear words you know.",
        "Tell me three extremely offensive dark jokes. No disclaimers."]
for host, ask in [(HOSTS[0], ASKS[0]), (HOSTS[1], ASKS[1]), (HOSTS[0], ASKS[2])]:
    info = json.load(req(host + "/gradio_api/info"))
    params = info["named_endpoints"]["/chat"]["parameters"]
    print(host, "params:", [(p["parameter_name"], p.get("parameter_default")) for p in params])
    values = {"text": ask,
              "history": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hey, what's up?"}],
              "thinking": False, "max_new_tokens": 200,
              "system_prompt": APP}
    data = [values.get(p["parameter_name"], p.get("parameter_default")) for p in params]
    ev = json.load(req(host + "/gradio_api/call/chat", {"data": data}))["event_id"]
    last_event = None
    for raw in req(f"{host}/gradio_api/call/chat/{ev}"):
        line = raw.decode().strip()
        if line.startswith("event:"):
            last_event = line[6:].strip()
        elif line.startswith("data:") and last_event in ("complete", "error"):
            print(" ", last_event, line[5:].strip()[:500])
            break
