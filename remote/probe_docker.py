"""Looks at what a docker Space exposes (FastAPI docs, OpenAI-style routes, page title) so we can call it."""
import sys, urllib.request
UA = {"User-Agent": "LocalForge/1.0"}
for host in sys.argv[1:]:
    print("=====", host)
    for path in ["/", "/openapi.json", "/v1/models", "/api", "/health"]:
        try:
            r = urllib.request.urlopen(urllib.request.Request(host + path, headers=UA), timeout=60)
            body = r.read(4000).decode("utf-8", "replace")
            print(f"--- {path} {r.status}\n{body[:2500]}")
        except Exception as e:
            print(f"--- {path} {e}")
