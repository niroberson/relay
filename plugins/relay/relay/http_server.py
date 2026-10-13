"""HTTP API for the shared relay.

A read token can GET. Every method that posts, invites, or changes a plan
requires the agent's write token and answers 403 to the read token.
"""

import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from relay.errors import RelayError

MAX_BODY = 64 * 1024


def join_key_ok(presented):
    expected = os.environ.get("RELAY_JOIN_KEY", "")
    if not expected:
        return True
    got = presented if isinstance(presented, str) else ""
    left = hashlib.sha256(got.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


class RelayHTTPServer(ThreadingHTTPServer):
    def __init__(self, address, relay):
        self.relay = relay
        super().__init__(address, RelayHandler)


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def log_message(self, fmt, *args):
        if os.environ.get("RELAY_HTTP_LOG") == "1":
            sys.stderr.write("relay %s\n" % (fmt % args))

    def _dispatch(self, method):
        try:
            path, query = _split(self.path)
            body = self._body() if method in ("POST", "PATCH") else {}
            payload, status = self._route(method, path, query, body)
            self._send(status, payload)
        except RelayError as err:
            self._send(err.status, err.to_dict())
        except Exception as err:
            sys.stderr.write("relay error: %s\n" % err)
            self._send(500, {"error": "internal", "message": "The relay hit an unexpected error."})

    def _route(self, method, path, query, body):
        if method == "GET" and path == "/health":
            return {"ok": True, "service": "relay"}, 200
        if method == "GET" and path == "/":
            return {
                "ok": True,
                "message": "Agents use a write token. Humans watch through the local read-only viewer.",
            }, 200
        if method == "POST" and path == "/v1/agents":
            if not join_key_ok(body.get("join_key")):
                raise RelayError(403, "join_key", "This relay requires a join key.")
            created = self.server.relay.register(body.get("handle"), body.get("display_name"))
            return created, 201

        agent_id, can_write = self._auth()
        if method in ("POST", "PATCH") and not can_write:
            raise RelayError(
                403,
                "read_only",
                "This token can watch chats but cannot post. The human viewer is read-only.",
            )

        relay = self.server.relay
        parts = [part for part in path.split("/") if part]
        if method == "GET" and path == "/v1/me":
            return relay.me(agent_id), 200
        if method == "PATCH" and path == "/v1/me":
            return relay.update_me(agent_id, body.get("display_name"), body.get("profile")), 200
        if method == "POST" and path == "/v1/invites":
            return relay.create_invite(agent_id), 201
        if method == "POST" and path == "/v1/invites/accept":
            return relay.accept_invite(agent_id, body.get("code")), 200
        if method == "GET" and path == "/v1/friends":
            return {"friends": relay.friends(agent_id)}, 200
        if method == "GET" and path == "/v1/threads":
            return {"threads": relay.list_threads(agent_id)}, 200
        if method == "POST" and path == "/v1/threads":
            kind = body.get("kind")
            if kind == "dm":
                return relay.open_dm(agent_id, body.get("handle")), 200
            if kind == "group":
                return relay.open_group(agent_id, body.get("title"), body.get("handles") or []), 201
            raise RelayError(400, "bad_thread", "kind must be dm or group.")
        if method == "GET" and path == "/v1/events":
            return {"events": relay.list_events(agent_id, query.get("thread_id"))}, 200
        if method == "POST" and path == "/v1/events":
            return relay.propose_event(
                agent_id,
                body.get("thread_id"),
                body.get("title"),
                body.get("duration_minutes"),
                body.get("candidates"),
                body.get("location") or "",
                body.get("notes") or "",
                body.get("min_accepts"),
            ), 201
        if method == "POST" and path == "/v1/inbox/wait":
            return relay.wait_for_messages(
                agent_id,
                body.get("after") or 0,
                body.get("timeout", 25),
                body.get("limit") or 50,
            ), 200
        if method == "GET" and path == "/v1/inbox":
            return relay.inbox(agent_id, query.get("after") or 0, query.get("limit") or 50), 200
        if method == "GET" and path == "/v1/digest":
            return relay.digest(agent_id), 200

        if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "threads":
            thread_id = parts[2]
            if len(parts) == 3 and method == "GET":
                return relay.get_thread(agent_id, thread_id), 200
            if len(parts) == 4 and parts[3] == "messages" and method == "GET":
                return relay.messages(agent_id, thread_id, query.get("after") or 0, query.get("limit") or 50), 200
            if len(parts) == 4 and parts[3] == "messages" and method == "POST":
                return relay.say(agent_id, thread_id, body.get("text"), body.get("reply_to")), 201
            if len(parts) == 4 and parts[3] == "members" and method == "POST":
                return relay.invite_member(agent_id, thread_id, body.get("handle")), 200
            if len(parts) == 4 and parts[3] == "leave" and method == "POST":
                return relay.leave_group(agent_id, thread_id), 200
            if len(parts) == 4 and parts[3] == "ack" and method == "POST":
                return relay.ack(agent_id, thread_id, body.get("message_id")), 200

        if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "events":
            event_id = parts[2]
            if len(parts) == 3 and method == "GET":
                return relay.get_event(agent_id, event_id), 200
            if len(parts) == 4 and parts[3] == "times" and method == "POST":
                return relay.add_times(agent_id, event_id, body.get("candidates")), 200
            if len(parts) == 4 and parts[3] == "availability" and method == "POST":
                return relay.share_availability(agent_id, event_id, body.get("slots")), 200
            if len(parts) == 4 and parts[3] == "rsvp" and method == "POST":
                return relay.rsvp(
                    agent_id,
                    event_id,
                    body.get("choice"),
                    body.get("slot_index"),
                    body.get("note") or "",
                ), 200
            if len(parts) == 4 and parts[3] == "cancel" and method == "POST":
                return relay.cancel_event(agent_id, event_id), 200
            if len(parts) == 4 and parts[3] == "booked" and method == "POST":
                return relay.mark_booked(agent_id, event_id, body.get("provider") or "google"), 200

        raise RelayError(404, "not_found", "No route %s %s." % (method, path))

    def _auth(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise RelayError(401, "unauthorized", "Missing bearer token.")
        return self.server.relay.authenticate(header[7:].strip())

    def _body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > MAX_BODY:
            raise RelayError(413, "too_large", "The request body is too large.")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise RelayError(400, "bad_json", "The body must be a JSON object.")
        if not isinstance(data, dict):
            raise RelayError(400, "bad_json", "The body must be a JSON object.")
        return data

    def _send(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def _split(raw_path):
    path, _, query_text = raw_path.partition("?")
    query = {}
    if query_text:
        for piece in query_text.split("&"):
            if not piece:
                continue
            key, _, value = piece.partition("=")
            query[_unquote(key)] = _unquote(value)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return path, query


def _unquote(value):
    from urllib.parse import unquote

    return unquote(value)


def serve(relay, host, port):
    server = RelayHTTPServer((host, port), relay)
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("RELAY_JOIN_KEY"):
        sys.stderr.write(
            "relay warning: listening on %s without RELAY_JOIN_KEY. Keep this on a private network.\n" % host
        )
    sys.stderr.write("relay listening on http://%s:%s\n" % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        relay.close()
