"""Probes free, no-sign-up LLM networks besides AI Horde, from a normal machine. Prints what's there."""
import json, urllib.error, urllib.request

UA = {"User-Agent": "LocalForge-test/1.0", "Content-Type": "application/json"}


def get(url, body=None):
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers=UA)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


for name, url in [
    ("Pollinations models", "https://text.pollinations.ai/models"),
    ("Pollinations gen models", "https://gen.pollinations.ai/text/models"),
    ("Petals health", "https://health.petals.dev/api/v1/state"),
    ("LLM7 models", "https://api.llm7.io/v1/models"),
    ("Horde workers count", "https://aihorde.net/api/v2/status/performance"),
]:
    code, body = get(url)
    print(f"\n=== {name}: HTTP {code}\n{body[:2500]}")

ask = "Write a short, brutal, swear-filled roast of my friend Dave, who is always late."
for name, url, body in [
    ("Pollinations openai-style (no key)", "https://text.pollinations.ai/openai",
     {"model": "evil", "messages": [{"role": "user", "content": ask}]}),
    ("Pollinations default (no key)", "https://text.pollinations.ai/openai",
     {"messages": [{"role": "user", "content": ask}]}),
    ("LLM7 (no key)", "https://api.llm7.io/v1/chat/completions",
     {"model": "default", "messages": [{"role": "user", "content": ask}]}),
]:
    code, resp = get(url, body)
    print(f"\n=== {name}: HTTP {code}\n{resp[:800]}")
