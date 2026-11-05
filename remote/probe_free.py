"""Probes public Hugging Face Spaces that run 20B+ abliterated / uncensored chat models, anonymously (no account):
is it running, on what hardware, what API does it expose, and does it answer without refusing?"""
import json, time, urllib.request, urllib.error

UA = {"User-Agent": "LocalForge-test/1.0"}
CANDIDATES = [
    "JonathanColetti/Qwen3.8-27B-Uncensored-Demo",
    "Rikunarita-ORG/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive_GGUF-Q4_K_P",
    "Silavon/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive_GGUF-Q6_K_P",
    "NanoBotAIAgent/qwen3.6-27b-uncensored-api",
    "mxderncultxre/Gemma4-31b-uncensored-assistant",
    "jickman125/Gemma4-31b-uncensored-assistant",
    "damabu/dphn-Dolphin-Mistral-24B-Venice-Edition",
    "DemonioX/huihui-ai-Mistral-Small-24B-Instruct-2501-abliterated",
    "Opro/huihui-ai-DeepSeek-R1-Distill-Qwen-32B-abliterated",
    "ukung/Muse-Glimmer-30B-DFlash-Abliterated",
]


def get(url):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))
    except urllib.error.HTTPError as e:
        return {"error": e.code}


from gradio_client import Client
for sid in CANDIDATES:
    rt = get(f"https://huggingface.co/api/spaces/{sid}/runtime")
    info = get(f"https://huggingface.co/api/spaces/{sid}")
    print(f"\n=== {sid}\n  stage={rt.get('stage')} hw={(rt.get('hardware') or {}).get('current')} sdk={info.get('sdk')} "
          f"models={info.get('models')}")
    host = (info.get("host") or f"https://{sid.replace('/', '-').replace('_', '-').replace('.', '-').lower()}.hf.space")
    print("  host:", host)
    if info.get("sdk") == "docker":
        for path in ["/v1/models", "/health", "/props"]:
            try:
                r = urllib.request.urlopen(urllib.request.Request(host + path, headers=UA), timeout=60)
                print(f"  {path}: {r.status} {r.read()[:300]!r}")
            except Exception as e:
                print(f"  {path}: {e}")
        continue
    try:
        c = Client(sid, verbose=False)
        api = c.view_api(return_format="dict", print_info=False)
        for name, e in list((api.get("named_endpoints") or {}).items())[:5]:
            print("  ", name, [(p.get("parameter_name"), (p.get("python_type") or {}).get("type"), p.get("parameter_default"))
                               for p in e.get("parameters", [])][:8])
    except Exception as ex:
        print(f"  gradio: {type(ex).__name__}: {str(ex)[:200]}")
