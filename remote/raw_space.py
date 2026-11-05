"""85B+ check: which big models are on AI Horde right now, and do their model cards call them uncensored/abliterated?"""
import json, re, urllib.request
UA = {"User-Agent": "LocalForge-test/1.0", "Client-Agent": "LocalForge:1.0:github.com/hsharmanov02/claudehelp"}
def get(url, raw=False):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")
    return r if raw else json.loads(r)
def size(n):
    nums = [float(x) for x in re.findall(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z])", n, re.I)]
    return max(nums) if nums else 0
models = get("https://aihorde.net/api/v2/status/models?type=text")
for m in sorted(models, key=lambda m: -size(m["name"])):
    print(f"  {size(m['name']):>5.0f}B  workers {m['count']}  queue {m['queued']}  eta {m.get('eta')}  {m['name']}")
for repo in ["TheDrummer/Behemoth-123B-v1", "TheDrummer/Behemoth-123B-v2", "TheDrummer/Behemoth-X-123B-v2", "TheDrummer/Behemoth-R1-123B-v2",
             "NousResearch/Hermes-3-Llama-3.1-405B", "NousResearch/Hermes-4-405B"]:
    try:
        card = get(f"https://huggingface.co/{repo}/raw/main/README.md", raw=True)
        hits = sorted(set(w.lower() for w in re.findall(r"uncensor\w*|abliterat\w*|unaligned|no refusals?|refus\w*", card, re.I)))
        print(f"{repo}: card mentions {hits}")
        for line in card.splitlines():
            if re.search(r"uncensor|abliterat|refus", line, re.I):
                print("    >", line.strip()[:200])
    except Exception as e:
        print(repo, "error", e)
