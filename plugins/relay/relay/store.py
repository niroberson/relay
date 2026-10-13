"""SQLite store for agent identities, threads, and event plans.

Lock order: acquire `_cv` before `_lock`. Writers take both so a waiter that
has checked the inbox and is about to sleep cannot miss a new message.
Helpers assume the caller already holds `_lock` and must not call `_read`
or `_write`.
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from relay.errors import RelayError

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PROFILE_LIMITS = {"timezone": 64, "city": 80, "notes_for_friends": 500}
THREAD_PREFIX = "t_"
EVENT_PREFIX = "e_"


def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_handle(value):
    if not isinstance(value, str):
        raise RelayError(400, "bad_handle", "A handle must be text.")
    handle = value.strip().lower()
    if len(handle) < 2 or len(handle) > 32 or not _handle_ok(handle):
        raise RelayError(
            400,
            "bad_handle",
            "A handle is 2–32 characters: a letter, then lowercase letters, numbers, or hyphens, and it cannot end with a hyphen.",
        )
    return handle


def _handle_ok(handle):
    if not handle or handle[0] < "a" or handle[0] > "z":
        return False
    if handle[-1] == "-":
        return False
    for char in handle:
        if not (("a" <= char <= "z") or ("0" <= char <= "9") or char == "-"):
            return False
    return True


def clean_display(value, field):
    if not isinstance(value, str):
        raise RelayError(400, "bad_name", "The %s must be text." % field)
    text = " ".join(value.split())
    if not text or len(text) > 80:
        raise RelayError(400, "bad_name", "The %s must be 1–80 characters." % field)
    return text


def clean_text(value, limit, field):
    if not isinstance(value, str):
        raise RelayError(400, "bad_text", "The %s must be text." % field)
    text = value.replace("\x00", "").strip()
    if not text:
        raise RelayError(400, "bad_text", "The %s is empty." % field)
    if len(text) > limit:
        raise RelayError(400, "bad_text", "The %s must be at most %d characters." % (field, limit))
    return text


def clean_optional(value, limit, field):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RelayError(400, "bad_text", "The %s must be text." % field)
    text = " ".join(value.replace("\x00", "").split())
    if len(text) > limit:
        raise RelayError(400, "bad_text", "The %s must be at most %d characters." % (field, limit))
    return text


def parse_time(value):
    if not isinstance(value, str):
        raise RelayError(400, "bad_time", "Times must be timezone-aware ISO-8601 strings.")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise RelayError(400, "bad_time", "Could not parse time %r." % value)
    if parsed.tzinfo is None:
        raise RelayError(400, "bad_time", "Times must include a timezone offset.")
    return parsed


def label_range(start, end):
    def offset_label(moment):
        off = moment.utcoffset() or timedelta(0)
        total = int(off.total_seconds())
        sign = "+" if total >= 0 else "-"
        total = abs(total)
        hours, minutes = divmod(total // 60, 60)
        return "%s%02d:%02d" % (sign, hours, minutes)

    start_off = offset_label(start)
    end_off = offset_label(end)
    if start_off == end_off and start.date() == end.date():
        return "%s–%s %s" % (start.strftime("%Y-%m-%d %H:%M"), end.strftime("%H:%M"), start_off)
    return "%s %s – %s %s" % (
        start.strftime("%Y-%m-%d %H:%M"),
        start_off,
        end.strftime("%Y-%m-%d %H:%M"),
        end_off,
    )


def _window(start, end):
    if end <= start:
        raise RelayError(400, "bad_time", "A window's end must be after its start.")
    minutes = (end - start).total_seconds() / 60.0
    if minutes < 15 or minutes > 24 * 60:
        raise RelayError(400, "bad_time", "A window must last between 15 minutes and 24 hours.")
    return {
        "start": _iso(start),
        "end": _iso(end),
        "label": label_range(start, end),
    }


def parse_candidates(raw, duration_minutes):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 8:
        raise RelayError(400, "bad_times", "Offer between 1 and 8 candidate times.")
    windows = []
    for item in raw:
        if not isinstance(item, dict):
            raise RelayError(400, "bad_times", "Each candidate time must be an object with start and end.")
        extra = set(item) - {"start", "end"}
        if extra:
            raise RelayError(400, "bad_times", "A candidate time only has start and end.")
        window = _window(parse_time(item.get("start")), parse_time(item.get("end")))
        length = (
            datetime.fromisoformat(window["end"]) - datetime.fromisoformat(window["start"])
        ).total_seconds() / 60.0
        if abs(length - duration_minutes) > 1:
            raise RelayError(400, "bad_times", "Each candidate must be duration_minutes long.")
        windows.append(window)
    return windows


def parse_availability(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        raise RelayError(400, "bad_availability", "Share between 1 and 12 availability windows.")
    slots = []
    for item in raw:
        if not isinstance(item, dict):
            raise RelayError(400, "bad_availability", "Each availability window must be an object.")
        extra = set(item) - {"start", "end", "status"}
        if extra:
            raise RelayError(
                400,
                "private_fields",
                "Availability windows only allow start, end, and status. Leave calendar titles, guests, and descriptions out.",
            )
        status = item.get("status")
        if status not in ("free", "busy", "maybe"):
            raise RelayError(400, "bad_availability", "Status must be free, busy, or maybe.")
        window = _window(parse_time(item.get("start")), parse_time(item.get("end")))
        window["status"] = status
        slots.append(window)
    return slots


def clean_profile(updates):
    if not isinstance(updates, dict):
        raise RelayError(400, "bad_profile", "Profile must be an object.")
    extra = set(updates) - set(PROFILE_LIMITS)
    if extra:
        raise RelayError(
            400,
            "bad_profile",
            "Profile only accepts timezone, city, and notes_for_friends.",
        )
    cleaned = {}
    for key, limit in PROFILE_LIMITS.items():
        if key not in updates:
            continue
        value = updates[key]
        if not isinstance(value, str):
            raise RelayError(400, "bad_profile", "%s must be text." % key)
        text = " ".join(value.replace("\x00", "").split())
        if len(text) > limit:
            raise RelayError(400, "bad_profile", "%s must be at most %d characters." % (key, limit))
        if key == "timezone" and text:
            for char in text:
                ok = ("A" <= char <= "Z") or ("a" <= char <= "z") or char in "0123456789_/+-"
                if not ok:
                    raise RelayError(400, "bad_profile", "Timezone must be an IANA name such as America/Los_Angeles.")
        cleaned[key] = text
    return cleaned


def _older_than(iso_text, seconds, now):
    try:
        then = datetime.fromisoformat(iso_text)
    except (TypeError, ValueError):
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then.astimezone(timezone.utc)).total_seconds() >= seconds


def _id(prefix, nbytes):
    return prefix + secrets.token_hex(nbytes)


class Relay:
    def __init__(self, db_path, clock=None):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None
        self._lock = threading.Lock()
        self._cv = threading.Condition()
        self._generation = 0
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._setup()
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

    def close(self):
        with self._lock:
            self._conn.close()

    def now(self):
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def now_iso(self):
        return _iso(self.now())

    def _setup(self):
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=3000")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    handle TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    write_hash TEXT NOT NULL UNIQUE,
                    read_hash TEXT NOT NULL UNIQUE,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    waiting_until TEXT
                );
                CREATE TABLE IF NOT EXISTS invites (
                    code_hash TEXT PRIMARY KEY,
                    inviter_id TEXT NOT NULL REFERENCES agents(id),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    accepted_by TEXT REFERENCES agents(id),
                    accepted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS friendships (
                    a_id TEXT NOT NULL REFERENCES agents(id),
                    b_id TEXT NOT NULL REFERENCES agents(id),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (a_id, b_id),
                    CHECK (a_id < b_id)
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
                    title TEXT NOT NULL,
                    created_by TEXT NOT NULL REFERENCES agents(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS members (
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    joined_at TEXT NOT NULL,
                    last_read INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (thread_id, agent_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    author_id TEXT NOT NULL REFERENCES agents(id),
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    payload_json TEXT,
                    reply_to INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, id);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    proposer_id TEXT NOT NULL REFERENCES agents(id),
                    title TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    candidates_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agreed_slot INTEGER,
                    all_members INTEGER NOT NULL,
                    min_accepts INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    bookings_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS rsvps (
                    event_id TEXT NOT NULL REFERENCES events(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    choice TEXT NOT NULL,
                    slot_index INTEGER,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (event_id, agent_id)
                );
                CREATE TABLE IF NOT EXISTS availability (
                    event_id TEXT NOT NULL REFERENCES events(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    slots_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (event_id, agent_id)
                );
                """
            )

    def _write(self, fn, notify=True):
        with self._cv:
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    result = fn()
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
                if notify:
                    self._generation += 1
            if notify:
                self._cv.notify_all()
        return result

    def _read(self, fn):
        with self._lock:
            return fn()

    def register(self, handle, display_name):
        handle = normalize_handle(handle)
        display_name = clean_display(display_name or handle, "display name")
        agent_id = _id("a_", 8)
        write_token = secrets.token_urlsafe(32)
        read_token = secrets.token_urlsafe(32)
        created = self.now_iso()

        def op():
            try:
                self._conn.execute(
                    """INSERT INTO agents
                       (id, handle, display_name, write_hash, read_hash, profile_json, created_at, last_seen)
                       VALUES (?, ?, ?, ?, ?, '{}', ?, ?)""",
                    (
                        agent_id,
                        handle,
                        display_name,
                        _hash_token(write_token),
                        _hash_token(read_token),
                        created,
                        created,
                    ),
                )
            except sqlite3.IntegrityError:
                raise RelayError(409, "handle_taken", "The handle %s is already registered on this relay." % handle)

        self._write(op, notify=False)
        return {
            "agent_id": agent_id,
            "handle": handle,
            "display_name": display_name,
            "write_token": write_token,
            "read_token": read_token,
        }

    def authenticate(self, token):
        if not isinstance(token, str) or not token.strip():
            raise RelayError(401, "unauthorized", "Missing bearer token.")
        digest = _hash_token(token.strip())

        def op():
            row = self._conn.execute(
                "SELECT * FROM agents WHERE write_hash=? OR read_hash=?",
                (digest, digest),
            ).fetchone()
            if row is None:
                return None
            can_write = hmac.compare_digest(row["write_hash"], digest)
            if _older_than(row["last_seen"], 10, self.now()):
                self._conn.execute(
                    "UPDATE agents SET last_seen=? WHERE id=?",
                    (self.now_iso(), row["id"]),
                )
            return row["id"], can_write

        found = self._write(op, notify=False)
        if found is None:
            raise RelayError(401, "unauthorized", "Unknown token.")
        return found

    def me(self, agent_id):
        return self._read(lambda: self._me_view(self._agent(agent_id)))

    def update_me(self, agent_id, display_name=None, profile=None):
        def op():
            row = self._agent(agent_id)
            name = row["display_name"] if display_name is None else clean_display(display_name, "display name")
            current = json.loads(row["profile_json"] or "{}")
            if profile is not None:
                current.update(clean_profile(profile))
            self._conn.execute(
                "UPDATE agents SET display_name=?, profile_json=? WHERE id=?",
                (name, json.dumps(current), agent_id),
            )
            return self._me_view(self._agent(agent_id))

        return self._write(op, notify=False)

    def create_invite(self, agent_id):
        raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
        code = raw[:4] + "-" + raw[4:]
        expires = _iso(self.now() + timedelta(days=7))
        created = self.now_iso()

        def op():
            self._agent(agent_id)
            open_invites = self._conn.execute(
                """SELECT COUNT(*) AS n FROM invites
                   WHERE inviter_id=? AND accepted_by IS NULL AND expires_at>?""",
                (agent_id, self.now_iso()),
            ).fetchone()["n"]
            if open_invites >= 20:
                raise RelayError(429, "too_many_invites", "Too many unused invites are already open.")
            self._conn.execute(
                """INSERT INTO invites (code_hash, inviter_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (_hash_token(self._normalize_code(code)), agent_id, created, expires),
            )

        self._write(op, notify=False)
        return {"invite_code": code, "expires_at": expires}

    def accept_invite(self, agent_id, code):
        digest = _hash_token(self._normalize_code(code))

        def op():
            me = self._agent(agent_id)
            row = self._conn.execute("SELECT * FROM invites WHERE code_hash=?", (digest,)).fetchone()
            if row is None:
                raise RelayError(404, "bad_invite", "That invite code does not exist.")
            if row["accepted_by"]:
                raise RelayError(409, "invite_used", "That invite code was already used.")
            if row["expires_at"] <= self.now_iso():
                raise RelayError(410, "invite_expired", "That invite code has expired.")
            if row["inviter_id"] == agent_id:
                raise RelayError(400, "bad_invite", "You created this invite. Send it to a friend.")
            inviter = self._agent(row["inviter_id"])
            if not self._are_friends(agent_id, inviter["id"]):
                self._connect(agent_id, inviter["id"])
            self._conn.execute(
                "UPDATE invites SET accepted_by=?, accepted_at=? WHERE code_hash=?",
                (agent_id, self.now_iso(), digest),
            )
            return self._friend_view(inviter if inviter["id"] != me["id"] else me)

        friend = self._write(op, notify=False)
        # Return the inviter, which is what the accepter wants to see.
        return {"friend": friend}

    def friends(self, agent_id):
        def op():
            self._agent(agent_id)
            return [self._friend_view(row) for row in self._friend_rows(agent_id)]

        return self._read(op)

    def open_dm(self, agent_id, handle):
        handle = normalize_handle(handle)

        def op():
            me = self._agent(agent_id)
            if handle == me["handle"]:
                raise RelayError(400, "bad_dm", "Pick a friend to talk to.")
            friend = self._require_friend_handle(agent_id, handle)
            existing = self._find_dm(agent_id, friend["id"])
            if existing is not None:
                return self._thread_view(agent_id, existing)
            thread_id = _id(THREAD_PREFIX, 6)
            title = "%s and %s" % (me["display_name"], friend["display_name"])
            self._conn.execute(
                "INSERT INTO threads (id, kind, title, created_by, created_at) VALUES (?, 'dm', ?, ?, ?)",
                (thread_id, title, agent_id, self.now_iso()),
            )
            self._add_member(thread_id, agent_id, 0)
            self._add_member(thread_id, friend["id"], 0)
            return self._thread_view(agent_id, self._thread_row(thread_id))

        return self._write(op, notify=False)

    def open_group(self, agent_id, title, handles):
        title = clean_display(title, "group title")
        if not isinstance(handles, list) or not handles:
            raise RelayError(400, "bad_group", "Name at least one friend to start a group.")

        def op():
            me = self._agent(agent_id)
            seen = []
            people = []
            for handle in handles:
                normalized = normalize_handle(handle)
                if normalized == me["handle"] or normalized in seen:
                    continue
                seen.append(normalized)
                people.append(self._require_friend_handle(agent_id, normalized))
            if not people:
                raise RelayError(400, "bad_group", "A group needs at least one friend.")
            if len(people) > 25:
                raise RelayError(400, "bad_group", "Groups can include up to 25 friends.")
            thread_id = _id(THREAD_PREFIX, 6)
            self._conn.execute(
                "INSERT INTO threads (id, kind, title, created_by, created_at) VALUES (?, 'group', ?, ?, ?)",
                (thread_id, title, agent_id, self.now_iso()),
            )
            self._add_member(thread_id, agent_id, 0)
            for person in people:
                self._add_member(thread_id, person["id"], 0)
            names = ", ".join(person["display_name"] for person in people)
            message = self._insert_message(
                thread_id,
                agent_id,
                "system",
                "%s's agent opened the group %s with %s." % (me["display_name"], title, names),
                {"handles": [person["handle"] for person in people]},
            )
            self._ack(agent_id, thread_id, message["id"])
            return self._thread_view(agent_id, self._thread_row(thread_id))

        return self._write(op)

    def invite_member(self, agent_id, thread_id, handle):
        handle = normalize_handle(handle)

        def op():
            me = self._agent(agent_id)
            thread = self._require_member_thread(agent_id, thread_id)
            if thread["kind"] != "group":
                raise RelayError(400, "bad_group", "Only group chats can take new members.")
            friend = self._require_friend_handle(agent_id, handle)
            already = self._conn.execute(
                "SELECT 1 FROM members WHERE thread_id=? AND agent_id=?",
                (thread_id, friend["id"]),
            ).fetchone()
            if already:
                return self._thread_view(agent_id, thread)
            prior = self._max_message(thread_id)
            self._add_member(thread_id, friend["id"], prior)
            self._insert_message(
                thread_id,
                agent_id,
                "system",
                "%s's agent invited %s." % (me["display_name"], friend["display_name"]),
                {"handle": friend["handle"]},
            )
            self._recompute_open_events(thread_id, agent_id)
            return self._thread_view(agent_id, self._thread_row(thread_id))

        return self._write(op)

    def leave_group(self, agent_id, thread_id):
        def op():
            me = self._agent(agent_id)
            thread = self._require_member_thread(agent_id, thread_id)
            if thread["kind"] != "group":
                raise RelayError(400, "bad_group", "Direct conversations stay available to both agents.")
            self._insert_message(
                thread_id,
                agent_id,
                "system",
                "%s's agent left the group." % me["display_name"],
                None,
            )
            self._conn.execute(
                "DELETE FROM members WHERE thread_id=? AND agent_id=?",
                (thread_id, agent_id),
            )
            self._recompute_open_events(thread_id, agent_id)
            return {"left": thread_id}

        return self._write(op)

    def list_threads(self, agent_id):
        def op():
            self._agent(agent_id)
            rows = self._conn.execute(
                """SELECT t.* FROM threads t
                   JOIN members m ON m.thread_id=t.id
                   WHERE m.agent_id=?""",
                (agent_id,),
            ).fetchall()
            views = [self._thread_view(agent_id, row) for row in rows]
            views.sort(key=lambda item: item["last_message"]["id"] if item["last_message"] else 0, reverse=True)
            return views

        return self._read(op)

    def get_thread(self, agent_id, thread_id):
        return self._read(lambda: self._thread_view(agent_id, self._require_member_thread(agent_id, thread_id)))

    def messages(self, agent_id, thread_id, after=0, limit=50):
        after, limit = _cursor(after, limit)

        def op():
            self._require_member_thread(agent_id, thread_id)
            rows = self._conn.execute(
                "SELECT id FROM messages WHERE thread_id=? AND id>? ORDER BY id ASC LIMIT ?",
                (thread_id, after, limit),
            ).fetchall()
            found = [self._message_view(row["id"]) for row in rows]
            return {
                "thread": self._thread_view(agent_id, self._thread_row(thread_id)),
                "messages": found,
                "next_after": found[-1]["id"] if found else after,
            }

        return self._read(op)

    def say(self, agent_id, thread_id, text, reply_to=None):
        text = clean_text(text, 4000, "message")
        if reply_to is not None:
            reply_to = _positive_int(reply_to, "reply_to")

        def op():
            self._require_member_thread(agent_id, thread_id)
            if reply_to is not None:
                target = self._conn.execute(
                    "SELECT id FROM messages WHERE id=? AND thread_id=?",
                    (reply_to, thread_id),
                ).fetchone()
                if target is None:
                    raise RelayError(400, "bad_reply", "reply_to is not a message in this conversation.")
            message = self._insert_message(thread_id, agent_id, "say", text, None, reply_to)
            self._ack(agent_id, thread_id, message["id"])
            return message

        return self._write(op)

    def inbox(self, agent_id, after=0, limit=50):
        after, limit = _cursor(after, limit)
        return self._read(lambda: self._inbox_unlocked(agent_id, after, limit))

    def wait_for_messages(self, agent_id, after=0, timeout=25, limit=50):
        after, limit = _cursor(after, limit)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            raise RelayError(400, "bad_timeout", "timeout must be a number of seconds.")
        timeout = max(0.0, min(timeout, 45.0))
        deadline = self._monotonic() + timeout
        self._set_waiting(agent_id, True)
        try:
            while True:
                remaining = deadline - self._monotonic()
                with self._cv:
                    with self._lock:
                        page = self._inbox_unlocked(agent_id, after, limit)
                    if page["messages"] or remaining <= 0:
                        return page
                    self._cv.wait(timeout=remaining)
        finally:
            self._set_waiting(agent_id, False)

    def ack(self, agent_id, thread_id, message_id):
        message_id = _positive_int(message_id, "message_id")

        def op():
            self._require_member_thread(agent_id, thread_id)
            cap = self._max_message(thread_id)
            target = min(message_id, cap)
            self._ack(agent_id, thread_id, target)
            return {"thread_id": thread_id, "last_read": target}

        return self._write(op, notify=False)

    def propose_event(self, agent_id, thread_id, title, duration_minutes, candidates, location="", notes="", min_accepts=None):
        title = clean_display(title, "event title")
        duration_minutes = _duration(duration_minutes)
        windows = parse_candidates(candidates, duration_minutes)
        location = clean_optional(location, 200, "location")
        notes = clean_optional(notes, 500, "notes")

        def op():
            me = self._agent(agent_id)
            thread = self._require_member_thread(agent_id, thread_id)
            member_count = self._member_count(thread_id)
            all_members = min_accepts is None
            if all_members:
                needed = member_count
            else:
                needed = _positive_int(min_accepts, "min_accepts")
                if needed > member_count:
                    raise RelayError(400, "bad_quorum", "min_accepts cannot be larger than the group.")
                if needed < 1:
                    raise RelayError(400, "bad_quorum", "min_accepts must be at least 1.")
            event_id = _id(EVENT_PREFIX, 6)
            self._conn.execute(
                """INSERT INTO events
                   (id, thread_id, proposer_id, title, duration_minutes, location, notes,
                    candidates_json, status, agreed_slot, all_members, min_accepts, created_at, bookings_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?, ?, ?, '{}')""",
                (
                    event_id,
                    thread_id,
                    agent_id,
                    title,
                    duration_minutes,
                    location,
                    notes,
                    json.dumps(windows),
                    1 if all_members else 0,
                    needed,
                    self.now_iso(),
                ),
            )
            place = " at %s" % location if location else ""
            options = " ".join("Option %d: %s." % (index + 1, item["label"]) for index, item in enumerate(windows))
            where = "in %s" % ("the group" if thread["kind"] == "group" else "this chat")
            self._insert_message(
                thread_id,
                agent_id,
                "proposal",
                "%s's agent proposed %s (%d min)%s %s. %s" % (me["display_name"], title, duration_minutes, place, where, options),
                {"event_id": event_id},
            )
            return self._event_view(self._event_row(event_id))

        return self._write(op)

    def add_times(self, agent_id, event_id, candidates):
        def op():
            me = self._agent(agent_id)
            event = self._require_event_member(agent_id, event_id)
            if event["status"] != "open":
                raise RelayError(409, "event_closed", "Times can be added while a plan is still open.")
            current = json.loads(event["candidates_json"])
            extra = parse_candidates(candidates, event["duration_minutes"])
            if len(current) + len(extra) > 12:
                raise RelayError(400, "bad_times", "A plan can hold at most 12 candidate times.")
            current.extend(extra)
            self._conn.execute(
                "UPDATE events SET candidates_json=? WHERE id=?",
                (json.dumps(current), event_id),
            )
            labels = ", ".join(item["label"] for item in extra)
            self._insert_message(
                event["thread_id"],
                agent_id,
                "proposal",
                "%s's agent added times for %s: %s." % (me["display_name"], event["title"], labels),
                {"event_id": event_id},
            )
            return self._event_view(self._event_row(event_id))

        return self._write(op)

    def share_availability(self, agent_id, event_id, slots):
        cleaned = parse_availability(slots)

        def op():
            me = self._agent(agent_id)
            event = self._require_event_member(agent_id, event_id)
            if event["status"] == "cancelled":
                raise RelayError(409, "event_closed", "This plan was cancelled.")
            self._conn.execute(
                """INSERT INTO availability (event_id, agent_id, slots_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(event_id, agent_id) DO UPDATE SET slots_json=excluded.slots_json, updated_at=excluded.updated_at""",
                (event_id, agent_id, json.dumps(cleaned), self.now_iso()),
            )
            summary = ", ".join("%s %s" % (item["label"], item["status"]) for item in cleaned)
            self._insert_message(
                event["thread_id"],
                agent_id,
                "availability",
                "%s's agent shared availability for %s: %s." % (me["display_name"], event["title"], summary),
                {"event_id": event_id},
            )
            return self._event_view(self._event_row(event_id))

        return self._write(op)

    def rsvp(self, agent_id, event_id, choice, slot_index=None, note=""):
        if choice not in ("accept", "decline", "maybe"):
            raise RelayError(400, "bad_rsvp", "choice must be accept, decline, or maybe.")
        note = clean_optional(note, 300, "note")
        if choice == "accept":
            slot_index = _non_negative_int(slot_index, "slot_index")
        elif slot_index is not None:
            slot_index = _non_negative_int(slot_index, "slot_index")

        def op():
            me = self._agent(agent_id)
            event = self._require_event_member(agent_id, event_id)
            if event["status"] == "cancelled":
                raise RelayError(409, "event_closed", "This plan was cancelled.")
            candidates = json.loads(event["candidates_json"])
            if choice == "accept" and slot_index >= len(candidates):
                raise RelayError(400, "bad_rsvp", "slot_index does not match a candidate time.")
            if slot_index is not None and slot_index >= len(candidates):
                raise RelayError(400, "bad_rsvp", "slot_index does not match a candidate time.")
            self._conn.execute(
                """INSERT INTO rsvps (event_id, agent_id, choice, slot_index, note, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(event_id, agent_id) DO UPDATE SET
                     choice=excluded.choice, slot_index=excluded.slot_index,
                     note=excluded.note, updated_at=excluded.updated_at""",
                (event_id, agent_id, choice, slot_index, note, self.now_iso()),
            )
            if choice == "accept":
                sentence = "%s's agent accepted %s at %s." % (
                    me["display_name"],
                    event["title"],
                    candidates[slot_index]["label"],
                )
            elif choice == "decline":
                sentence = "%s's agent declined %s." % (me["display_name"], event["title"])
            else:
                sentence = "%s's agent is a maybe for %s." % (me["display_name"], event["title"])
            if note:
                sentence = "%s Note: %s" % (sentence, note)
            self._insert_message(
                event["thread_id"],
                agent_id,
                "rsvp",
                sentence,
                {"event_id": event_id, "choice": choice, "slot_index": slot_index},
            )
            self._recompute(event_id, agent_id)
            return self._event_view(self._event_row(event_id))

        return self._write(op)

    def cancel_event(self, agent_id, event_id):
        def op():
            me = self._agent(agent_id)
            event = self._require_event_member(agent_id, event_id)
            if event["proposer_id"] != agent_id:
                raise RelayError(403, "forbidden", "Only the proposing agent can cancel this plan.")
            if event["status"] == "cancelled":
                return self._event_view(event)
            self._conn.execute(
                "UPDATE events SET status='cancelled' WHERE id=?",
                (event_id,),
            )
            self._insert_message(
                event["thread_id"],
                agent_id,
                "system",
                "%s's agent cancelled %s." % (me["display_name"], event["title"]),
                {"event_id": event_id, "status": "cancelled"},
            )
            return self._event_view(self._event_row(event_id))

        return self._write(op)

    def mark_booked(self, agent_id, event_id, provider="google"):
        if not isinstance(provider, str) or not provider.strip():
            provider = "google"
        provider = "".join(ch for ch in provider.lower() if "a" <= ch <= "z")[:32] or "google"

        def op():
            me = self._agent(agent_id)
            event = self._require_event_member(agent_id, event_id)
            if event["status"] != "agreed":
                raise RelayError(409, "not_agreed", "Book the calendar only after the plan is agreed.")
            bookings = json.loads(event["bookings_json"] or "{}")
            already = me["handle"] in bookings
            bookings[me["handle"]] = {"provider": provider, "booked_at": self.now_iso()}
            self._conn.execute(
                "UPDATE events SET bookings_json=? WHERE id=?",
                (json.dumps(bookings), event_id),
            )
            if not already:
                self._insert_message(
                    event["thread_id"],
                    agent_id,
                    "system",
                    "%s's agent put %s on the calendar." % (me["display_name"], event["title"]),
                    {"event_id": event_id, "provider": provider},
                )
            return self._event_view(self._event_row(event_id))

        return self._write(op)

    def list_events(self, agent_id, thread_id=None):
        def op():
            self._agent(agent_id)
            if thread_id:
                self._require_member_thread(agent_id, thread_id)
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE thread_id=? ORDER BY created_at",
                    (thread_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT e.* FROM events e
                       JOIN members m ON m.thread_id=e.thread_id
                       WHERE m.agent_id=?
                       ORDER BY e.created_at""",
                    (agent_id,),
                ).fetchall()
            return [self._event_view(row) for row in rows]

        return self._read(op)

    def get_event(self, agent_id, event_id):
        return self._read(lambda: self._event_view(self._require_event_member(agent_id, event_id)))

    def digest(self, agent_id):
        def op():
            me = self._me_view(self._agent(agent_id))
            friend_rows = self._friend_rows(agent_id)
            threads = []
            rows = self._conn.execute(
                """SELECT t.* FROM threads t
                   JOIN members m ON m.thread_id=t.id
                   WHERE m.agent_id=?""",
                (agent_id,),
            ).fetchall()
            for row in rows:
                threads.append(self._thread_view(agent_id, row))
            threads.sort(key=lambda item: item["last_message"]["id"] if item["last_message"] else 0, reverse=True)
            events = []
            for thread in threads:
                for event in self.list_events_unlocked(thread["id"]):
                    if event["status"] in ("open", "agreed"):
                        events.append(event)
            return {
                "you": me,
                "friends": [self._friend_view(row) for row in friend_rows],
                "threads": threads,
                "unread_threads": [thread for thread in threads if thread["unread"] > 0],
                "events": events,
            }

        # list_events_unlocked is called inside _read, so it must not lock.
        return self._read(op)

    def list_events_unlocked(self, thread_id):
        rows = self._conn.execute(
            "SELECT * FROM events WHERE thread_id=? ORDER BY created_at",
            (thread_id,),
        ).fetchall()
        return [self._event_view(row) for row in rows]

    def _monotonic(self):
        # time.monotonic is patched in tests via this seam only when set.
        return _monotonic_now()

    def _set_waiting(self, agent_id, waiting):
        def op():
            until = _iso(self.now() + timedelta(seconds=60)) if waiting else None
            self._conn.execute(
                "UPDATE agents SET waiting_until=?, last_seen=? WHERE id=?",
                (until, self.now_iso(), agent_id),
            )

        self._write(op, notify=False)

    def _normalize_code(self, code):
        if not isinstance(code, str):
            raise RelayError(400, "bad_invite", "The invite code must be text.")
        normalized = "".join(ch for ch in code.upper() if ch.isalnum())
        if len(normalized) < 6:
            raise RelayError(400, "bad_invite", "That invite code is not complete.")
        return normalized

    def _agent(self, agent_id):
        row = self._conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise RelayError(401, "unauthorized", "Unknown agent.")
        return row

    def _me_view(self, row):
        profile = json.loads(row["profile_json"] or "{}")
        return {
            "agent_id": row["id"],
            "handle": row["handle"],
            "display_name": row["display_name"],
            "author_kind": "agent",
            "timezone": profile.get("timezone") or "",
            "city": profile.get("city") or "",
            "notes_for_friends": profile.get("notes_for_friends") or "",
            "created_at": row["created_at"],
        }

    def _friend_view(self, row):
        profile = json.loads(row["profile_json"] or "{}")
        waiting = bool(row["waiting_until"] and row["waiting_until"] > self.now_iso())
        return {
            "handle": row["handle"],
            "display_name": row["display_name"],
            "author_kind": "agent",
            "timezone": profile.get("timezone") or "",
            "city": profile.get("city") or "",
            "notes_for_friends": profile.get("notes_for_friends") or "",
            "last_seen": row["last_seen"],
            "waiting": waiting,
            "status": "active",
            "you_approved": True,
            "they_approved": True,
        }

    def _friend_rows(self, agent_id):
        return self._conn.execute(
            """SELECT a.* FROM agents a
               JOIN friendships f ON (a.id=f.a_id OR a.id=f.b_id)
               WHERE (f.a_id=? OR f.b_id=?) AND a.id!=?
               ORDER BY a.handle""",
            (agent_id, agent_id, agent_id),
        ).fetchall()

    def _are_friends(self, left, right):
        first, second = sorted((left, right))
        return (
            self._conn.execute(
                "SELECT 1 FROM friendships WHERE a_id=? AND b_id=?",
                (first, second),
            ).fetchone()
            is not None
        )

    def _connect(self, left, right):
        first, second = sorted((left, right))
        self._conn.execute(
            "INSERT OR IGNORE INTO friendships (a_id, b_id, created_at) VALUES (?, ?, ?)",
            (first, second, self.now_iso()),
        )

    def _require_friend_handle(self, agent_id, handle):
        row = self._conn.execute("SELECT * FROM agents WHERE handle=?", (handle,)).fetchone()
        if row is None or not self._are_friends(agent_id, row["id"]):
            raise RelayError(
                404,
                "not_a_friend",
                "No friend with the handle %s. They need to accept an invite first." % handle,
            )
        return row

    def _thread_row(self, thread_id):
        if not isinstance(thread_id, str) or not thread_id.startswith(THREAD_PREFIX):
            raise RelayError(404, "not_found", "No conversation with that id.")
        row = self._conn.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
        if row is None:
            raise RelayError(404, "not_found", "No conversation with that id.")
        return row

    def _require_member_thread(self, agent_id, thread_id):
        if not isinstance(thread_id, str) or not thread_id.startswith(THREAD_PREFIX):
            raise RelayError(404, "not_found", "No conversation with that id.")
        row = self._conn.execute(
            """SELECT t.* FROM threads t
               JOIN members m ON m.thread_id=t.id
               WHERE t.id=? AND m.agent_id=?""",
            (thread_id, agent_id),
        ).fetchone()
        if row is None:
            raise RelayError(404, "not_found", "No conversation with that id.")
        return row

    def _add_member(self, thread_id, agent_id, last_read):
        self._conn.execute(
            "INSERT INTO members (thread_id, agent_id, joined_at, last_read) VALUES (?, ?, ?, ?)",
            (thread_id, agent_id, self.now_iso(), last_read),
        )

    def _member_count(self, thread_id):
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM members WHERE thread_id=?",
            (thread_id,),
        ).fetchone()["n"]

    def _max_message(self, thread_id):
        row = self._conn.execute(
            "SELECT MAX(id) AS n FROM messages WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        return row["n"] or 0

    def _ack(self, agent_id, thread_id, message_id):
        self._conn.execute(
            """UPDATE members SET last_read=?
               WHERE thread_id=? AND agent_id=? AND last_read<?""",
            (message_id, thread_id, agent_id, message_id),
        )

    def _find_dm(self, left, right):
        return self._conn.execute(
            """SELECT t.* FROM threads t
               JOIN members m1 ON m1.thread_id=t.id AND m1.agent_id=?
               JOIN members m2 ON m2.thread_id=t.id AND m2.agent_id=?
               WHERE t.kind='dm'""",
            (left, right),
        ).fetchone()

    def _insert_message(self, thread_id, author_id, kind, text, payload, reply_to=None):
        cur = self._conn.execute(
            """INSERT INTO messages (thread_id, author_id, kind, text, payload_json, reply_to, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                author_id,
                kind,
                text,
                json.dumps(payload) if payload is not None else None,
                reply_to,
                self.now_iso(),
            ),
        )
        return self._message_view(cur.lastrowid)

    def _message_view(self, message_id):
        row = self._conn.execute(
            """SELECT m.*, a.handle AS handle, a.display_name AS display_name
               FROM messages m JOIN agents a ON a.id=m.author_id
               WHERE m.id=?""",
            (message_id,),
        ).fetchone()
        payload = json.loads(row["payload_json"]) if row["payload_json"] else None
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "author_handle": row["handle"],
            "author_display_name": row["display_name"],
            "author_kind": "agent",
            "kind": row["kind"],
            "text": row["text"],
            "payload": payload,
            "reply_to": row["reply_to"],
            "created_at": row["created_at"],
        }

    def _thread_view(self, agent_id, thread):
        members = self._conn.execute(
            """SELECT a.handle, a.display_name FROM members m
               JOIN agents a ON a.id=m.agent_id
               WHERE m.thread_id=?
               ORDER BY m.joined_at, a.handle""",
            (thread["id"],),
        ).fetchall()
        me = self._agent(agent_id)
        membership = self._conn.execute(
            "SELECT last_read FROM members WHERE thread_id=? AND agent_id=?",
            (thread["id"], agent_id),
        ).fetchone()
        last_read = membership["last_read"] if membership else 0
        unread = self._conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE thread_id=? AND id>? AND author_id!=?",
            (thread["id"], last_read, agent_id),
        ).fetchone()["n"]
        last = self._conn.execute(
            """SELECT m.id, m.text, m.created_at, m.kind, a.handle AS handle
               FROM messages m JOIN agents a ON a.id=m.author_id
               WHERE m.thread_id=? ORDER BY m.id DESC LIMIT 1""",
            (thread["id"],),
        ).fetchone()
        title = thread["title"]
        if thread["kind"] == "dm":
            others = [member for member in members if member["handle"] != me["handle"]]
            if others:
                title = others[0]["display_name"]
        return {
            "id": thread["id"],
            "kind": thread["kind"],
            "title": title,
            "members": [
                {"handle": member["handle"], "display_name": member["display_name"], "author_kind": "agent"}
                for member in members
            ],
            "unread": unread,
            "last_message": None
            if last is None
            else {
                "id": last["id"],
                "text": last["text"],
                "kind": last["kind"],
                "author_handle": last["handle"],
                "author_kind": "agent",
                "created_at": last["created_at"],
            },
            "created_at": thread["created_at"],
        }

    def _inbox_unlocked(self, agent_id, after, limit):
        self._agent(agent_id)
        rows = self._conn.execute(
            """SELECT m.id FROM messages m
               JOIN members mem ON mem.thread_id=m.thread_id
               WHERE mem.agent_id=? AND m.id>? AND m.author_id!=?
               ORDER BY m.id ASC LIMIT ?""",
            (agent_id, after, agent_id, limit),
        ).fetchall()
        found = [self._message_view(row["id"]) for row in rows]
        waiting = []
        for row in self._friend_rows(agent_id):
            view = self._friend_view(row)
            if view["waiting"]:
                waiting.append(view["handle"])
        return {
            "messages": found,
            "next_after": found[-1]["id"] if found else after,
            "friends_waiting": waiting,
        }

    def _event_row(self, event_id):
        if not isinstance(event_id, str) or not event_id.startswith(EVENT_PREFIX):
            raise RelayError(404, "not_found", "No plan with that id.")
        row = self._conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise RelayError(404, "not_found", "No plan with that id.")
        return row

    def _require_event_member(self, agent_id, event_id):
        row = self._event_row(event_id)
        member = self._conn.execute(
            "SELECT 1 FROM members WHERE thread_id=? AND agent_id=?",
            (row["thread_id"], agent_id),
        ).fetchone()
        if member is None:
            raise RelayError(404, "not_found", "No plan with that id.")
        return row

    def _event_view(self, row):
        candidates = json.loads(row["candidates_json"])
        proposer = self._agent(row["proposer_id"])
        rsvps = self._conn.execute(
            """SELECT r.*, a.handle FROM rsvps r
               JOIN agents a ON a.id=r.agent_id
               WHERE r.event_id=? ORDER BY a.handle""",
            (row["id"],),
        ).fetchall()
        availability = self._conn.execute(
            """SELECT v.*, a.handle FROM availability v
               JOIN agents a ON a.id=v.agent_id
               WHERE v.event_id=? ORDER BY a.handle""",
            (row["id"],),
        ).fetchall()
        bookings = json.loads(row["bookings_json"] or "{}")
        agreed = None
        if row["agreed_slot"] is not None and 0 <= row["agreed_slot"] < len(candidates):
            agreed = dict(candidates[row["agreed_slot"]])
            agreed["index"] = row["agreed_slot"]
        return {
            "id": row["id"],
            "thread_id": row["thread_id"],
            "title": row["title"],
            "duration_minutes": row["duration_minutes"],
            "location": row["location"],
            "notes": row["notes"],
            "candidates": candidates,
            "status": row["status"],
            "agreed": agreed,
            "all_members": bool(row["all_members"]),
            "min_accepts": row["min_accepts"],
            "proposer_handle": proposer["handle"],
            "rsvps": [
                {
                    "handle": item["handle"],
                    "author_kind": "agent",
                    "choice": item["choice"],
                    "slot_index": item["slot_index"],
                    "note": item["note"],
                }
                for item in rsvps
            ],
            "availability": [
                {"handle": item["handle"], "author_kind": "agent", "slots": json.loads(item["slots_json"])}
                for item in availability
            ],
            "bookings": [
                {"handle": handle, "author_kind": "agent", "provider": info.get("provider", ""), "booked_at": info.get("booked_at", "")}
                for handle, info in sorted(bookings.items())
            ],
            "created_at": row["created_at"],
        }

    def _recompute_open_events(self, thread_id, actor_id):
        rows = self._conn.execute(
            "SELECT id FROM events WHERE thread_id=? AND status!='cancelled'",
            (thread_id,),
        ).fetchall()
        for row in rows:
            self._recompute(row["id"], actor_id)

    def _recompute(self, event_id, actor_id):
        event = self._event_row(event_id)
        if event["status"] == "cancelled":
            return
        candidates = json.loads(event["candidates_json"])
        member_ids = [
            row["agent_id"]
            for row in self._conn.execute(
                "SELECT agent_id FROM members WHERE thread_id=?",
                (event["thread_id"],),
            ).fetchall()
        ]
        rsvps = self._conn.execute("SELECT * FROM rsvps WHERE event_id=?", (event_id,)).fetchall()
        by_agent = {row["agent_id"]: row for row in rsvps}
        agreed_slot = None
        for index in range(len(candidates)):
            accepts = [
                member_id
                for member_id in member_ids
                if member_id in by_agent
                and by_agent[member_id]["choice"] == "accept"
                and by_agent[member_id]["slot_index"] == index
            ]
            if event["all_members"]:
                if member_ids and len(accepts) == len(member_ids):
                    agreed_slot = index
                    break
            elif len(accepts) >= event["min_accepts"]:
                agreed_slot = index
                break
        status = "agreed" if agreed_slot is not None else "open"
        previous = event["status"]
        previous_slot = event["agreed_slot"]
        self._conn.execute(
            "UPDATE events SET status=?, agreed_slot=? WHERE id=?",
            (status, agreed_slot, event_id),
        )
        if status == previous and agreed_slot == previous_slot:
            return
        if status == "agreed":
            text = "%s is agreed for %s." % (event["title"], candidates[agreed_slot]["label"])
        elif previous == "agreed":
            text = "%s is open again. The earlier agreement no longer holds." % event["title"]
        else:
            return
        self._insert_message(
            event["thread_id"],
            actor_id,
            "system",
            text,
            {"event_id": event_id, "status": status},
        )


def _cursor(after, limit):
    after = 0 if after in (None, "") else _non_negative_int(after, "after")
    limit = 50 if limit in (None, "") else _positive_int(limit, "limit")
    if limit > 200:
        limit = 200
    return after, limit


def _positive_int(value, field):
    number = _non_negative_int(value, field)
    if number < 1:
        raise RelayError(400, "bad_number", "%s must be at least 1." % field)
    return number


def _non_negative_int(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RelayError(400, "bad_number", "%s must be an integer." % field)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise RelayError(400, "bad_number", "%s must be an integer." % field)
    if number < 0:
        raise RelayError(400, "bad_number", "%s must be zero or more." % field)
    return number


def _duration(value):
    number = _positive_int(value, "duration_minutes")
    if number > 24 * 60:
        raise RelayError(400, "bad_times", "duration_minutes must be between 15 and 1440.")
    if number < 15:
        raise RelayError(400, "bad_times", "duration_minutes must be between 15 and 1440.")
    return number


def _iso(moment):
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _monotonic_now():
    import time

    return time.monotonic()
