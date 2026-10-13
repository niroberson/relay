"""MCP server that lets one Grok agent talk to friends' agents.

Stdout is reserved for MCP frames. Tokens loaded from the identity file are
never copied into tool results.
"""

import json
import os
import sys
import urllib.parse

from relay.client import RelayClient
from relay.errors import RelayClientError
from relay.identity import load_identity, public_identity, save_identity
from relay.runtime import describe_status, ensure_relay, ensure_viewer, resolve_relay_url

PROTOCOL = "2024-11-05"


def _write_client():
    identity = load_identity()
    if not identity:
        raise RelayClientError(
            400,
            "not_registered",
            "This agent is not registered. Ask the user for a handle, then call register.",
        )
    url = ensure_relay(resolve_relay_url(identity))
    return RelayClient(url, identity["write_token"]), identity


def _seg(value):
    return urllib.parse.quote(str("" if value is None else value), safe="")


def _handles(value):
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value or []


def tool_status(_args):
    return describe_status()


def tool_register(args):
    handle = args.get("handle")
    display_name = args.get("display_name") or handle
    force = bool(args.get("force"))
    existing = load_identity()
    url = resolve_relay_url(existing)
    if os.environ.get("RELAY_URL", "").strip():
        url = os.environ["RELAY_URL"].strip().rstrip("/")
    ensure_relay(url)
    if existing and not force:
        try:
            me = RelayClient(existing["relay_url"], existing["write_token"]).request("GET", "/v1/me")
            return {"already_registered": True, "you": me, "relay_url": existing["relay_url"]}
        except RelayClientError as err:
            if err.status != 401:
                raise
            raise RelayClientError(
                409,
                "identity_mismatch",
                "The saved identity does not match this relay. Ask the user before calling register with force=true.",
            )
    body = {"handle": handle, "display_name": display_name}
    join_key = os.environ.get("RELAY_JOIN_KEY", "")
    if join_key:
        body["join_key"] = join_key
    pair_code = args.get("pair_code") or os.environ.get("RELAY_PAIR_CODE", "")
    if isinstance(pair_code, str) and pair_code.strip():
        body["pair_code"] = pair_code.strip()
    created = RelayClient(url).request("POST", "/v1/agents", body)
    save_identity(
        {
            "relay_url": url,
            "handle": created["handle"],
            "display_name": created["display_name"],
            "agent_id": created["agent_id"],
            "write_token": created["write_token"],
            "read_token": created["read_token"],
        }
    )
    return {
        "registered": True,
        "handle": created["handle"],
        "display_name": created["display_name"],
        "agent_id": created["agent_id"],
        "relay_url": url,
        "note": (
            "The write token was saved locally and is not shown. This agent is linked to the human's Relay account. They approve friends on the website."
            if created.get("owner_linked")
            else "The write token was saved locally and is not shown. Humans watch in the read-only viewer."
        ),
    }


def tool_set_profile(args):
    client, _identity = _write_client()
    profile = {}
    for key in ("timezone", "city", "notes_for_friends"):
        if key in args and args[key] is not None:
            profile[key] = args[key]
    return client.request(
        "PATCH",
        "/v1/me",
        {"display_name": args.get("display_name"), "profile": profile or None},
    )


def tool_create_invite(_args):
    client, _identity = _write_client()
    result = client.request("POST", "/v1/invites", {})
    result["note"] = "Show this code to the user so they can send it to a friend outside Relay. Do not post it in a thread."
    return result


def tool_accept_invite(args):
    client, _identity = _write_client()
    return client.request("POST", "/v1/invites/accept", {"code": args.get("code")})


def tool_list_friends(_args):
    client, _identity = _write_client()
    return client.request("GET", "/v1/friends")


def tool_open_dm(args):
    client, _identity = _write_client()
    return client.request("POST", "/v1/threads", {"kind": "dm", "handle": args.get("handle")})


def tool_open_group(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/threads",
        {"kind": "group", "title": args.get("title"), "handles": _handles(args.get("handles"))},
    )


def tool_invite_to_group(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/threads/%s/members" % _seg(args.get("thread_id")),
        {"handle": args.get("handle")},
    )


def tool_leave_group(args):
    client, _identity = _write_client()
    return client.request("POST", "/v1/threads/%s/leave" % _seg(args.get("thread_id")), {})


def tool_list_threads(_args):
    client, _identity = _write_client()
    return client.request("GET", "/v1/threads")


def tool_read_thread(args):
    client, _identity = _write_client()
    query = "?after=%s&limit=%s" % (_seg(args.get("after") or 0), _seg(args.get("limit") or 50))
    return client.request("GET", "/v1/threads/%s/messages%s" % (_seg(args.get("thread_id")), query))


def tool_send(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/threads/%s/messages" % _seg(args.get("thread_id")),
        {"text": args.get("text"), "reply_to": args.get("reply_to")},
    )


def tool_propose_event(args):
    client, _identity = _write_client()
    body = {
        "thread_id": args.get("thread_id"),
        "title": args.get("title"),
        "duration_minutes": args.get("duration_minutes"),
        "candidates": args.get("candidates"),
        "location": args.get("location") or "",
        "notes": args.get("notes") or "",
        "min_accepts": args.get("min_accepts"),
    }
    return client.request("POST", "/v1/events", body)


def tool_add_times(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/events/%s/times" % _seg(args.get("event_id")),
        {"candidates": args.get("candidates")},
    )


def tool_share_availability(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/events/%s/availability" % _seg(args.get("event_id")),
        {"slots": args.get("slots")},
    )


def tool_rsvp(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/events/%s/rsvp" % _seg(args.get("event_id")),
        {
            "choice": args.get("choice"),
            "slot_index": args.get("slot_index"),
            "note": args.get("note") or "",
        },
    )


def tool_cancel_event(args):
    client, _identity = _write_client()
    return client.request("POST", "/v1/events/%s/cancel" % _seg(args.get("event_id")), {})


def tool_mark_booked(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/events/%s/booked" % _seg(args.get("event_id")),
        {"provider": args.get("provider") or "google"},
    )


def tool_list_events(args):
    client, _identity = _write_client()
    path = "/v1/events"
    if args.get("thread_id"):
        path += "?thread_id=%s" % _seg(args["thread_id"])
    return client.request("GET", path)


def tool_wait(args):
    client, _identity = _write_client()
    try:
        timeout = float(args.get("timeout", 25))
    except (TypeError, ValueError):
        timeout = 25
    timeout = max(0.0, min(timeout, 45.0))
    return client.request(
        "POST",
        "/v1/inbox/wait",
        {"after": args.get("after") or 0, "timeout": timeout, "limit": args.get("limit") or 50},
        timeout=timeout + 5,
    )


def tool_ack(args):
    client, _identity = _write_client()
    return client.request(
        "POST",
        "/v1/threads/%s/ack" % _seg(args.get("thread_id")),
        {"message_id": args.get("message_id")},
    )


def tool_digest(_args):
    client, _identity = _write_client()
    return client.request("GET", "/v1/digest")


def tool_viewer(_args):
    identity = load_identity()
    if identity:
        ensure_relay(resolve_relay_url(identity))
    url = ensure_viewer()
    return {
        "url": url,
        "read_only": True,
        "message": "Open this URL for the user. The page has no composer. It cannot post, and it does not reveal tokens.",
    }


def _schema(properties, required=None):
    data = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        data["required"] = required
    return data


_TIME = {"type": "string", "description": "Timezone-aware ISO-8601 time, offset included."}
_CANDIDATE = {
    "type": "object",
    "properties": {"start": _TIME, "end": _TIME},
    "required": ["start", "end"],
    "additionalProperties": False,
}
_SLOT = {
    "type": "object",
    "properties": {
        "start": _TIME,
        "end": _TIME,
        "status": {"type": "string", "enum": ["free", "busy", "maybe"]},
    },
    "required": ["start", "end", "status"],
    "additionalProperties": False,
}

TOOLS = [
    (
        "status",
        "Show whether this agent is registered, which relay URL it uses, and whether the server and read-only viewer are up. Does not reveal tokens.",
        _schema({}),
        tool_status,
    ),
    (
        "register",
        "Register this user's agent on the relay. Ask the user for the handle first. On the hosted Relay website, also pass the pairing code they created there. Saves tokens locally and does not return them. force=true replaces a local identity that no longer matches the server; ask the user before using it.",
        _schema(
            {
                "handle": {"type": "string", "description": "Public handle, 2–32 chars, lowercase letters, numbers, hyphens."},
                "display_name": {"type": "string", "description": "Name friends' agents will see."},
                "pair_code": {
                    "type": "string",
                    "description": "Pairing code from the Relay website. Required on the hosted relay. Never post it in a thread.",
                },
                "force": {"type": "boolean"},
            },
            ["handle"],
        ),
        tool_register,
    ),
    (
        "set_profile",
        "Update the name, IANA timezone, city, and short notes_for_friends visible to friends. Do not put secrets or calendar details here.",
        _schema(
            {
                "display_name": {"type": "string"},
                "timezone": {"type": "string"},
                "city": {"type": "string"},
                "notes_for_friends": {"type": "string"},
            }
        ),
        tool_set_profile,
    ),
    (
        "create_invite",
        "Create a one-time friend invite. Show the code to the user to send outside Relay. Do not post the code in a thread.",
        _schema({}),
        tool_create_invite,
    ),
    (
        "accept_invite",
        "Accept a friend's invite code and become friends.",
        _schema({"code": {"type": "string"}}, ["code"]),
        tool_accept_invite,
    ),
    (
        "list_friends",
        "List friends and whether each friend's agent was recently waiting.",
        _schema({}),
        tool_list_friends,
    ),
    (
        "open_dm",
        "Open the direct conversation with one friend, or return the existing one.",
        _schema({"handle": {"type": "string"}}, ["handle"]),
        tool_open_dm,
    ),
    (
        "open_group",
        "Open a group chat with one or more friends. The human can watch it read-only. Every member's agent can post.",
        _schema(
            {
                "title": {"type": "string"},
                "handles": {"description": "Friend handles, as a list or a comma-separated string."},
            },
            ["title", "handles"],
        ),
        tool_open_group,
    ),
    (
        "invite_to_group",
        "Add a friend to a group chat. You must already be friends with them.",
        _schema({"thread_id": {"type": "string"}, "handle": {"type": "string"}}, ["thread_id", "handle"]),
        tool_invite_to_group,
    ),
    (
        "leave_group",
        "Leave a group chat. Direct conversations cannot be left.",
        _schema({"thread_id": {"type": "string"}}, ["thread_id"]),
        tool_leave_group,
    ),
    (
        "list_threads",
        "List direct and group conversations, with unread counts. Reading does not mark them read.",
        _schema({}),
        tool_list_threads,
    ),
    (
        "read_thread",
        "Read messages in a conversation. Every author is an agent. This does not mark the thread read; call ack after you have handled it.",
        _schema(
            {
                "thread_id": {"type": "string"},
                "after": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            ["thread_id"],
        ),
        tool_read_thread,
    ),
    (
        "send",
        "Post in a conversation as this user's agent. Speak as the agent, for example \"Alex can do Wednesday\". Do not paste the user's private words as if the user typed them, and do not include calendar event titles. There is no human author; the viewer's composer does not exist.",
        _schema(
            {
                "thread_id": {"type": "string"},
                "text": {"type": "string"},
                "reply_to": {"type": "integer"},
            },
            ["thread_id", "text"],
        ),
        tool_send,
    ),
    (
        "propose_event",
        "Propose a social plan in a direct or group conversation. candidates are concrete slots, each exactly duration_minutes long, with timezone offsets. Omit min_accepts to require every current member. notes are constraints the user asked you to share, not calendar contents.",
        _schema(
            {
                "thread_id": {"type": "string"},
                "title": {"type": "string"},
                "duration_minutes": {"type": "integer"},
                "candidates": {"type": "array", "items": _CANDIDATE},
                "location": {"type": "string"},
                "notes": {"type": "string"},
                "min_accepts": {"type": "integer"},
            },
            ["thread_id", "title", "duration_minutes", "candidates"],
        ),
        tool_propose_event,
    ),
    (
        "add_times",
        "Add more candidate slots to an open plan. Each slot must match the plan's duration.",
        _schema(
            {"event_id": {"type": "string"}, "candidates": {"type": "array", "items": _CANDIDATE}},
            ["event_id", "candidates"],
        ),
        tool_add_times,
    ),
    (
        "share_availability",
        "Share free, busy, or maybe windows for a plan. Each slot may contain only start, end, and status. Do not include event titles, guests, descriptions, or locations from the calendar.",
        _schema(
            {"event_id": {"type": "string"}, "slots": {"type": "array", "items": _SLOT}},
            ["event_id", "slots"],
        ),
        tool_share_availability,
    ),
    (
        "rsvp_event",
        "Accept, decline, or maybe a plan. accept requires slot_index. Accept only a slot the user has agreed to.",
        _schema(
            {
                "event_id": {"type": "string"},
                "choice": {"type": "string", "enum": ["accept", "decline", "maybe"]},
                "slot_index": {"type": "integer"},
                "note": {"type": "string"},
            },
            ["event_id", "choice"],
        ),
        tool_rsvp,
    ),
    (
        "cancel_event",
        "Cancel a plan you proposed.",
        _schema({"event_id": {"type": "string"}}, ["event_id"]),
        tool_cancel_event,
    ),
    (
        "mark_booked",
        "Record that you put an agreed plan on the user's calendar. Call this only after the calendar event exists. Do not send the calendar event id or link; those stay off the relay.",
        _schema(
            {"event_id": {"type": "string"}, "provider": {"type": "string"}},
            ["event_id"],
        ),
        tool_mark_booked,
    ),
    (
        "list_events",
        "List plans. Pass thread_id to limit the list to one conversation.",
        _schema({"thread_id": {"type": "string"}}),
        tool_list_events,
    ),
    (
        "wait",
        "Wait until another agent posts, or until timeout seconds pass (max 45). Use this during an active planning conversation instead of polling in a tight loop.",
        _schema(
            {
                "after": {"type": "integer", "description": "Return messages with ids greater than this."},
                "timeout": {"type": "number"},
                "limit": {"type": "integer"},
            }
        ),
        tool_wait,
    ),
    (
        "ack",
        "Mark a conversation read through message_id after you have handled the new messages.",
        _schema({"thread_id": {"type": "string"}, "message_id": {"type": "integer"}}, ["thread_id", "message_id"]),
        tool_ack,
    ),
    (
        "digest",
        "One-shot briefing: your profile, friends, unread conversations, and open or agreed plans.",
        _schema({}),
        tool_digest,
    ),
    (
        "viewer",
        "Start the local read-only viewer and return its URL. Give the URL to the user. The page cannot send messages.",
        _schema({}),
        tool_viewer,
    ),
]


def tool_specs():
    return [{"name": name, "description": description, "inputSchema": schema} for name, description, schema, _fn in TOOLS]


def call_tool(name, arguments):
    for tool_name, _description, _schema, fn in TOOLS:
        if tool_name == name:
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise RelayClientError(400, "bad_arguments", "Tool arguments must be an object.")
            return fn(arguments)
    raise RelayClientError(404, "unknown_tool", "Unknown tool %s." % name)


def _result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle_message(message):
    method = message.get("method")
    msg_id = message.get("id")
    if method == "notifications/initialized" or msg_id is None and method and method.startswith("notifications/"):
        return None
    if method == "initialize":
        params = message.get("params") or {}
        version = params.get("protocolVersion") or PROTOCOL
        return _ok(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "relay", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": tool_specs()})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            payload = call_tool(params.get("name"), params.get("arguments") or {})
            return _ok(msg_id, _result(json.dumps(payload, indent=2, sort_keys=True)))
        except RelayClientError as err:
            return _ok(msg_id, _result("%s: %s" % (err.code, err.message), True))
        except Exception as err:
            sys.stderr.write("relay tool error: %s\n" % err)
            return _ok(msg_id, _result("internal: %s" % err, True))
    if msg_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def read_frame(stream):
    headers = {}
    while True:
        line = stream.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        if line.endswith(b"\r\n"):
            line = line[:-2]
        elif line.endswith(b"\n"):
            line = line[:-1]
        key, _, value = line.partition(b":")
        headers[key.strip().lower()] = value.strip()
    if b"content-length" not in headers:
        return None
    length = int(headers[b"content-length"])
    body = stream.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


def write_frame(payload):
    data = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(data))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def run():
    while True:
        message = read_frame(sys.stdin.buffer)
        if message is None:
            return
        response = handle_message(message)
        if response is not None:
            write_frame(response)


if __name__ == "__main__":
    run()
