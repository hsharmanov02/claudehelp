"""Diagnostic: runs the real App chat flow against llama-server and shows exactly what the model is sent."""
import json, os, sys, time, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from localforge import agent, config, hf

MODELS = [("mradermacher/Huihui-Qwen3.5-2B-abliterated-GGUF", "Huihui-Qwen3.5-2B-abliterated.Q4_K_M.gguf"),
          ("bartowski/mlabonne_Qwen3-1.7B-abliterated-GGUF", "mlabonne_Qwen3-1.7B-abliterated-Q4_K_M.gguf")]

def fetch(repo, name):
    t = config.models_dir() / name
    if not t.exists():
        urllib.request.urlretrieve(hf.download_url(repo, name), t)
    return t

def wait(app):
    time.sleep(0.5)
    while app.generating or app.loading:
        time.sleep(0.5)

for repo, name in MODELS:
    print("=" * 30, name, flush=True)
    path = fetch(repo, name)
    app = agent.App()
    app.clear_chat()
    app.settings.set("autoApprove", True)
    app.settings.set("nCtx", 8192)
    sent = []
    orig = app.engine.chat
    def spy(messages, *a, **k):
        sent.append(messages)
        r = orig(messages, *a, **k)
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{app.engine.port}/apply-template",
                                         data=json.dumps({"messages": messages}).encode(),
                                         headers={"Content-Type": "application/json"})
            sent.append(json.loads(urllib.request.urlopen(req).read())["prompt"])
        except Exception as e:
            sent.append(f"(apply-template failed: {e})")
        return r
    app.engine.chat = spy
    assert app.load_model(str(path), wait=True), app.status
    print("loaded:", app.status, flush=True)
    for turn in ["My dog is called Biscuit and my favourite colour is teal. Just reply OK.",
                 "Write a python program that prints 17*23 and run it.",
                 "What is the capital of Japan?",
                 "What did I ask you just before this message? Quote it.",
                 "What is my dog's name and my favourite colour?"]:
        sent.clear()
        app.send(turn)
        wait(app)
        print("\n### USER:", turn)
        for m in app.messages[-4:]:
            print(f"  [{m['role']}] {m['text'][:300]!r}")
        last_msgs = [s for s in sent if isinstance(s, list)][-1]
        print("  -> messages sent on last call:", [(m["role"], m["content"][:80]) for m in last_msgs[1:]])
        prompt = [s for s in sent if isinstance(s, str)][-1]
        print("  -> rendered prompt (after system):", prompt[prompt.find("<|im_end|>"):][-1500:])
    print("\n--- engine log tail ---")
    print("\n".join(list(app.engine.log)[-25:]))
    app.engine.unload()
