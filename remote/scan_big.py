"""Second angle: start from the MODELS (70B+ with uncensored/abliterated in the name or card tags), then find every
public Space that runs one of them, and every free hosted endpoint that serves it. Prints what answers with no account."""
import json, re, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

UA = {"User-Agent": "LocalForge-test/1.0"}


def get(url, t=40):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=t))
    except Exception:
        return None


def size(text):
    n = [float(x) for x in re.findall(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z])", text, re.I)]
    return max(n) if n else 0.0


models = {}
for term in ["abliterated", "uncensored", "abliteration"]:
    for sort in ["downloads", "likes", "lastModified"]:
        for m in get(f"https://huggingface.co/api/models?search={term}&sort={sort}&limit=1000") or []:
            if size(m["id"]) >= 70:
                models[m["id"]] = m
big = sorted(models, key=lambda i: -size(i))
print(len(big), "uncensored/abliterated models of 70B+ on the Hub")


def spaces_for(mid):
    out = []
    for s in get(f"https://huggingface.co/api/spaces?models={urllib.parse.quote(mid)}&limit=100") or []:
        out.append(s["id"])
    prov = (get(f"https://huggingface.co/api/models/{urllib.parse.quote(mid, safe='/')}?expand[]=inferenceProviderMapping") or {}).get("inferenceProviderMapping")
    return mid, out, prov


with ThreadPoolExecutor(10) as ex:
    found = list(ex.map(spaces_for, big))

seen = set()
rows = []
for mid, sps, prov in found:
    if prov:
        print(f"  PROVIDER {size(mid):>5.0f}B {mid}: {list(prov)[:5] if isinstance(prov, dict) else prov}")
    for sid in sps:
        if sid in seen:
            continue
        seen.add(sid)
        rows.append((mid, sid))
print(len(rows), "spaces use one of them")


def check(row):
    mid, sid = row
    info = get(f"https://huggingface.co/api/spaces/{sid}") or {}
    rt = info.get("runtime") or {}
    stage, hw = rt.get("stage"), (rt.get("hardware") or {}).get("current")
    host = info.get("host") or ""
    api = None
    if stage == "RUNNING" and info.get("sdk") == "gradio" and host:
        a = get(host + "/gradio_api/info", 30)
        api = list((a or {}).get("named_endpoints", {}).keys())[:5] if a else "no api"
    return size(mid), mid, sid, stage, hw, info.get("sdk"), host, api


with ThreadPoolExecutor(10) as ex:
    res = list(ex.map(check, rows))
res.sort(key=lambda r: (r[3] != "RUNNING", -r[0]))
for r in res:
    print(f"  {r[0]:>5.0f}B {str(r[3]):<14} {str(r[4]):<14} {str(r[5]):<7} {r[2]}  model={r[1]}  host={r[6]}  api={r[7]}")
