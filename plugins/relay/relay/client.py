"""Small HTTP client used by the MCP server and the read-only viewer."""

import json
import urllib.error
import urllib.request

from relay.errors import RelayClientError


class RelayClient:
    def __init__(self, base_url, token=None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method, path, body=None, timeout=30):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read()
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError):
                payload = {"error": "http", "message": "Relay returned HTTP %s." % err.code}
            raise RelayClientError(
                err.code,
                str(payload.get("error") or "http"),
                str(payload.get("message") or err.reason),
            )
        except urllib.error.URLError as err:
            raise RelayClientError(503, "server_down", "Relay is not reachable at %s (%s)." % (self.base_url, err.reason))


def server_healthy(base_url, timeout=0.4):
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=timeout) as res:
            return res.status == 200
    except Exception:
        return False
