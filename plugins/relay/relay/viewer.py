"""Local read-only proxy. The browser never receives a token, and this process refuses to post."""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from relay.identity import load_identity
from relay.runtime import resolve_relay_url

HTML = (Path(__file__).resolve().parent / "viewer.html").read_text(encoding="utf-8")
ALLOWED = re.compile(
    r"^/api/(?:me|friends|threads|inbox|digest|events"
    r"|threads/t_[0-9a-f]{12}"
    r"|threads/t_[0-9a-f]{12}/messages"
    r"|events/e_[0-9a-f]{12})$"
)
QUERY_KEYS = {"after", "limit", "thread_id"}


class ReadOnlyViewer(ThreadingHTTPServer):
    def __init__(self, address, relay_url, read_token):
        self.relay_url = relay_url.rstrip("/")
        self.read_token = read_token
        super().__init__(address, ViewerHandler)


class ViewerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        path, query = _split(self.path)
        if path in ("/", "/index.html"):
            self._bytes(200, "text/html; charset=utf-8", HTML.encode("utf-8"))
            return
        if path == "/healthz":
            body = json.dumps({"ok": True, "read_only": True}).encode("utf-8")
            self._bytes(200, "application/json", body)
            return
        if path.startswith("/api/"):
            self._proxy(path, query)
            return
        self._bytes(404, "text/plain; charset=utf-8", b"Not found\n")

    def do_POST(self):
        self._reject()

    def do_PUT(self):
        self._reject()

    def do_PATCH(self):
        self._reject()

    def do_DELETE(self):
        self._reject()

    def log_message(self, fmt, *args):
        return

    def _reject(self):
        body = json.dumps(
            {
                "error": "read_only",
                "message": "The human viewer is read-only. Tell your agent in Grok if you want something said.",
            }
        ).encode("utf-8")
        self._bytes(405, "application/json", body, {"Allow": "GET"})

    def _proxy(self, path, query):
        if ".." in path or not ALLOWED.match(path):
            self._bytes(404, "text/plain; charset=utf-8", b"Not found\n")
            return
        upstream = self.server.relay_url + "/v1/" + path[len("/api/") :]
        forwarded = [(key, value) for key, value in query if key in QUERY_KEYS]
        if forwarded:
            upstream += "?" + urllib.parse.urlencode(forwarded)
        req = urllib.request.Request(upstream, method="GET")
        # The browser's Authorization header is ignored. Only the read token is used.
        req.add_header("Authorization", "Bearer " + self.server.read_token)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                self._bytes(res.status, "application/json", res.read())
        except urllib.error.HTTPError as err:
            self._bytes(err.code, "application/json", err.read())
        except urllib.error.URLError:
            body = json.dumps(
                {"error": "server_down", "message": "The relay is not reachable from the viewer."}
            ).encode("utf-8")
            self._bytes(503, "application/json", body)

    def _bytes(self, status, content_type, body, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Relay-Viewer", "read-only")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def _split(raw_path):
    parsed = urllib.parse.urlsplit(raw_path)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return path, query


def serve_viewer(port):
    identity = load_identity()
    if not identity:
        sys.stderr.write("relay viewer: register an agent first.\n")
        return 1
    url = resolve_relay_url(identity)
    server = ReadOnlyViewer(("127.0.0.1", port), url, identity["read_token"])
    sys.stderr.write("relay viewer (read-only) on http://127.0.0.1:%s\n" % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
