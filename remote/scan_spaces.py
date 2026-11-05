"""Finds the biggest uncensored/abliterated chat models anyone can use for free right now on public Hugging Face
Spaces (no account): running, on a GPU (or an OpenAI-style API), with "uncensored" or "abliterated" in the Space's or
model's name. Prints them biggest first."""
import json, re, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "LocalForge-test/1.0"}
TERMS = ["abliterated", "abliteration", "uncensored", "huihui", "dolphin", "lexi", "abliterate", "70b", "72b", "100b", "120b", "123b", "235b", "405b", "behemoth", "mistral-large", "magnum", "hermes", "goliath", "miqu", "euryale", "grok", "deepseek-v3", "glm", "gpt-oss-120b", "kimi", "qwen3-235b", "llama-3.1-405b", "nemotron"]
NOT_TEXT = re.compile(r"flux|image|img|video|wan2|sdxl|diffusion|tts|voice|speech|face|photo|lora|nsfw-un|pixel|vl-|-vl|vision|music|audio|ocr", re.I)
GOOD = re.compile(r"uncensor|abliterat|behemoth|magnum|euryale|goliath|miqu|dolphin|hermes|heretic", re.I)


def get(url):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))
    except Exception:
        return None


def size(text):
    nums = [float(x) for x in re.findall(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z])", text, re.I)]
    nums += [float(x) * 8 for x in re.findall(r"(\d+)x(\d+)b", text, re.I) for x in x[:1]]
    return max(nums) if nums else 0.0


ids = {}
for t in TERMS:
    for s in get(f"https://huggingface.co/api/spaces?search={t}&limit=500&full=true") or []:
        ids[s["id"]] = s
print(len(ids), "spaces matched the search terms")


def check(sid):
    s = ids[sid]
    models = s.get("models") or []
    names = " ".join([sid] + models)
    if NOT_TEXT.search(names) or not GOOD.search(names):
        return None
    b = size(names)
    if b < 85:
        return None
    rt = get(f"https://huggingface.co/api/spaces/{sid}/runtime") or {}
    hw = (rt.get("hardware") or {}).get("current")
    return {"id": sid, "b": b, "stage": rt.get("stage"), "hw": hw, "sdk": s.get("sdk"), "likes": s.get("likes", 0),
            "models": models[:3]}


with ThreadPoolExecutor(12) as ex:
    rows = [r for r in ex.map(check, list(ids)) if r]
rows.sort(key=lambda r: (r["stage"] != "RUNNING", -r["b"], -r["likes"]))
print(f"\n{len(rows)} uncensored/abliterated text-model spaces of 85B+:")
for r in rows:
    print(f"  {r['b']:>6.0f}B  {str(r['stage']):<15} {str(r['hw']):<14} {str(r['sdk']):<8} likes {r['likes']:>4}  {r['id']}  {r['models']}")

# Which running ones actually expose an API we can call without an account?
usable = [r for r in rows if r["stage"] == "RUNNING" and r["hw"] and r["hw"] != "cpu-basic" or (r["stage"] == "RUNNING" and r["sdk"] == "docker")]
print("\nRunning on a GPU (or docker):")
for r in usable:
    host = f"https://huggingface.co/api/spaces/{r['id']}"
    info = get(host) or {}
    h = info.get("host") or ""
    api = get(h + "/gradio_api/info") if r["sdk"] == "gradio" else get(h + "/v1/models")
    eps = list((api or {}).get("named_endpoints", {}).keys())[:6] if r["sdk"] == "gradio" else ("openai api" if api else None)
    print(f"  {r['b']:>6.0f}B {r['hw']:<14} {r['id']}  host={h}  api={eps}")
