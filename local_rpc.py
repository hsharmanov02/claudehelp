"""Read-only, bounded loopback JSON requests; no proxy or redirects."""
import json
import os
from urllib.parse import urlsplit
from urllib.request import (Request, build_opener, ProxyHandler, HTTPRedirectHandler,
                            HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("RPC redirects are not permitted")

def local_url(url):
    p = urlsplit(url)
    if (p.scheme != "http" or p.hostname not in ("127.0.0.1", "::1")
            or p.username or p.password or p.fragment or not p.port):
        raise ValueError("Use an HTTP loopback address with an explicit port, no credentials")
    return url

def request_json(url, payload=None, *, wallet_auth=False):
    local_url(url)
    handlers = [ProxyHandler({}), NoRedirect()]
    headers = {"Accept": "application/json"}
    if wallet_auth:
        user, password = os.environ.get("MONERO_RPC_USER"), os.environ.get("MONERO_RPC_PASSWORD")
        if user and password:
            manager = HTTPPasswordMgrWithDefaultRealm()
            manager.add_password(None, url, user, password)
            handlers.append(HTTPDigestAuthHandler(manager))
    elif payload is None and os.environ.get("XMRIG_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["XMRIG_API_TOKEN"]
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    with build_opener(*handlers).open(Request(url, data=data, headers=headers), timeout=3) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Response exceeds 4 MB; narrow history_from_height")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Expected JSON object")
        return result

READ_METHODS = {"get_address", "get_transfers", "get_height", "validate_address", "get_info"}

def rpc(url, method, params=None, *, wallet_auth=True):
    if method not in READ_METHODS:
        raise ValueError("Only read-only RPC methods are permitted")
    result = request_json(url, {"jsonrpc": "2.0", "id": "observer", "method": method,
                               "params": params or {}}, wallet_auth=wallet_auth)
    if "error" in result or not isinstance(result.get("result"), dict):
        raise ValueError("RPC returned an error or missing result")
    return result["result"]

