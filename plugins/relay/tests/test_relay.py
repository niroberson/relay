"""Behavior tests for agent chat, plans, and the read-only human view."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from relay.http_server import RelayHTTPServer  # noqa: E402
from relay.identity import load_identity, save_identity  # noqa: E402
from relay.mcp_server import call_tool, handle_message, read_frame  # noqa: E402
from relay.store import Relay  # noqa: E402
from relay.viewer import HTML, ReadOnlyViewer  # noqa: E402


def iso(hour, day=14, offset_hours=-7):
    zone = timezone(timedelta(hours=offset_hours))
    start = datetime(2026, 4, day, hour, 0, tzinfo=zone)
    end = start + timedelta(minutes=90)
    return {"start": start.isoformat(), "end": end.isoformat()}


def request(method, url, body=None, token=None, timeout=5):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
            return res.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {"message": raw.decode("utf-8", "replace")}
        return err.code, payload


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = {"now": datetime(2026, 4, 1, tzinfo=timezone.utc)}
        self.relay = Relay(os.path.join(self.tmp, "relay.db"), clock=lambda: self.clock["now"])
        self.alex = self.relay.register("alex", "Alex")
        self.sam = self.relay.register("sam", "Sam")
        self.jo = self.relay.register("jo", "Jo")

    def tearDown(self):
        self.relay.close()
        shutil.rmtree(self.tmp)

    def _friend(self, left, right):
        invite = self.relay.create_invite(left["agent_id"])
        self.relay.accept_invite(right["agent_id"], invite["invite_code"])

    def test_invite_is_single_use_and_expires(self):
        invite = self.relay.create_invite(self.alex["agent_id"])
        self.relay.accept_invite(self.sam["agent_id"], invite["invite_code"].lower())
        with self.assertRaises(Exception) as caught:
            self.relay.accept_invite(self.jo["agent_id"], invite["invite_code"])
        self.assertEqual(caught.exception.code, "invite_used")
        fresh = self.relay.create_invite(self.alex["agent_id"])
        self.clock["now"] += timedelta(days=8)
        with self.assertRaises(Exception) as expired:
            self.relay.accept_invite(self.jo["agent_id"], fresh["invite_code"])
        self.assertEqual(expired.exception.code, "invite_expired")

    def test_direct_and_group_chat_with_a_quorum(self):
        self._friend(self.alex, self.sam)
        self._friend(self.alex, self.jo)
        first = self.relay.open_dm(self.alex["agent_id"], "sam")
        again = self.relay.open_dm(self.sam["agent_id"], "alex")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["kind"], "dm")
        with self.assertRaises(Exception):
            self.relay.open_dm(self.sam["agent_id"], "jo")

        group = self.relay.open_group(self.alex["agent_id"], "Dinner crew", ["sam", "jo", "alex"])
        self.assertEqual(group["kind"], "group")
        self.assertEqual(sorted(member["handle"] for member in group["members"]), ["alex", "jo", "sam"])
        self.relay.say(self.sam["agent_id"], group["id"], "Sam's agent: Thursday looks good.")
        with self.assertRaises(Exception) as missing:
            self.relay.messages(self.jo["agent_id"], first["id"])
        self.assertEqual(missing.exception.status, 404)

        plan = self.relay.propose_event(
            self.alex["agent_id"],
            group["id"],
            "Dinner",
            90,
            [iso(19, 14), iso(19, 15)],
            "Cafe Presse",
            "A quiet table",
            min_accepts=2,
        )
        self.assertEqual(plan["status"], "open")
        with self.assertRaises(Exception) as leak:
            self.relay.share_availability(
                self.sam["agent_id"],
                plan["id"],
                [{"start": iso(19, 14)["start"], "end": iso(19, 14)["end"], "status": "free", "title": "dentist"}],
            )
        self.assertEqual(leak.exception.code, "private_fields")
        self.relay.share_availability(
            self.sam["agent_id"],
            plan["id"],
            [
                {"start": iso(19, 14)["start"], "end": iso(19, 14)["end"], "status": "free"},
                {"start": iso(19, 15)["start"], "end": iso(19, 15)["end"], "status": "busy"},
            ],
        )
        self.relay.rsvp(self.alex["agent_id"], plan["id"], "accept", 0)
        agreed = self.relay.rsvp(self.sam["agent_id"], plan["id"], "accept", 0)
        self.assertEqual(agreed["status"], "agreed")
        self.assertEqual(agreed["agreed"]["index"], 0)
        booked = self.relay.mark_booked(self.alex["agent_id"], plan["id"])
        self.assertEqual(booked["bookings"][0]["handle"], "alex")
        self.assertNotIn("event_id", booked["bookings"][0])
        reopened = self.relay.rsvp(self.sam["agent_id"], plan["id"], "decline")
        self.assertEqual(reopened["status"], "open")
        transcript = self.relay.messages(self.jo["agent_id"], group["id"])
        self.assertTrue(all(message["author_kind"] == "agent" for message in transcript["messages"]))
        self.assertTrue(any(message["kind"] == "availability" for message in transcript["messages"]))
        self.assertNotIn("dentist", json.dumps(transcript))

    def test_everyone_must_accept_unless_a_quorum_is_set(self):
        self._friend(self.alex, self.sam)
        self._friend(self.alex, self.jo)
        self._friend(self.sam, self.jo)
        group = self.relay.open_group(self.alex["agent_id"], "Walk", ["sam", "jo"])
        plan = self.relay.propose_event(
            self.alex["agent_id"],
            group["id"],
            "Walk",
            90,
            [iso(10, 18)],
        )
        self.relay.rsvp(self.alex["agent_id"], plan["id"], "accept", 0)
        still = self.relay.rsvp(self.sam["agent_id"], plan["id"], "accept", 0)
        self.assertEqual(still["status"], "open")
        with self.assertRaises(Exception) as early:
            self.relay.mark_booked(self.sam["agent_id"], plan["id"])
        self.assertEqual(early.exception.code, "not_agreed")
        done = self.relay.rsvp(self.jo["agent_id"], plan["id"], "accept", 0)
        self.assertEqual(done["status"], "agreed")

    def test_wait_wakes_when_the_other_agent_posts(self):
        self._friend(self.alex, self.sam)
        thread = self.relay.open_dm(self.alex["agent_id"], "sam")
        cursor = 0
        for _ in range(8):
            found = {}

            def wait(after=cursor):
                found["page"] = self.relay.wait_for_messages(self.sam["agent_id"], after, timeout=2)

            worker = threading.Thread(target=wait)
            worker.start()
            time.sleep(0.05)
            posted = self.relay.say(self.alex["agent_id"], thread["id"], "Alex can do Thursday.")
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(found["page"]["messages"][0]["id"], posted["id"])
            cursor = posted["id"]

    def test_naive_times_and_profile_are_rejected(self):
        self._friend(self.alex, self.sam)
        thread = self.relay.open_dm(self.alex["agent_id"], "sam")
        with self.assertRaises(Exception) as bad_time:
            self.relay.propose_event(
                self.alex["agent_id"],
                thread["id"],
                "Coffee",
                90,
                [{"start": "2026-04-14T19:00:00", "end": "2026-04-14T20:30:00"}],
            )
        self.assertEqual(bad_time.exception.code, "bad_time")
        with self.assertRaises(Exception) as bad_profile:
            self.relay.update_me(self.alex["agent_id"], profile={"calendar": "secret"})
        self.assertEqual(bad_profile.exception.code, "bad_profile")
        updated = self.relay.update_me(
            self.alex["agent_id"],
            profile={"timezone": "America/Los_Angeles", "city": "Seattle", "notes_for_friends": "Vegetarian"},
        )
        self.assertEqual(updated["notes_for_friends"], "Vegetarian")
        friend = self.relay.friends(self.sam["agent_id"])[0]
        self.assertEqual(friend["notes_for_friends"], "Vegetarian")

    def test_leave_group_removes_access(self):
        self._friend(self.alex, self.sam)
        group = self.relay.open_group(self.alex["agent_id"], "Crew", ["sam"])
        self.relay.leave_group(self.sam["agent_id"], group["id"])
        with self.assertRaises(Exception) as missing:
            self.relay.messages(self.sam["agent_id"], group["id"])
        self.assertEqual(missing.exception.status, 404)
        with self.assertRaises(Exception) as dm:
            self.relay.leave_group(self.alex["agent_id"], self.relay.open_dm(self.alex["agent_id"], "sam")["id"])
        self.assertEqual(dm.exception.code, "bad_group")


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.relay = Relay(os.path.join(self.tmp, "relay.db"))
        self.server = RelayHTTPServer(("127.0.0.1", 0), self.relay)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.url = "http://127.0.0.1:%s" % self.server.server_address[1]
        self.alex = request(
            "POST", self.url + "/v1/agents", {"handle": "alex", "display_name": "Alex"}
        )[1]
        self.sam = request(
            "POST", self.url + "/v1/agents", {"handle": "sam", "display_name": "Sam"}
        )[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.relay.close()
        shutil.rmtree(self.tmp)

    def test_read_token_can_watch_and_cannot_post(self):
        code = request("POST", self.url + "/v1/invites", {}, self.alex["write_token"])[1]["invite_code"]
        request("POST", self.url + "/v1/invites/accept", {"code": code}, self.sam["write_token"])
        thread = request(
            "POST", self.url + "/v1/threads", {"kind": "dm", "handle": "sam"}, self.alex["write_token"]
        )[1]
        status, posted = request(
            "POST",
            self.url + "/v1/threads/%s/messages" % thread["id"],
            {"text": "Alex's agent checking Thursday.", "kind": "system"},
            self.alex["write_token"],
        )
        self.assertEqual(status, 201)
        self.assertEqual(posted["kind"], "say")
        self.assertEqual(posted["author_kind"], "agent")
        status, watched = request(
            "GET",
            self.url + "/v1/threads/%s/messages" % thread["id"],
            token=self.sam["read_token"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(watched["messages"][0]["text"], "Alex's agent checking Thursday.")
        for method, path, body in (
            ("POST", "/v1/threads/%s/messages" % thread["id"], {"text": "hello from the human"}),
            ("POST", "/v1/invites", {}),
            ("POST", "/v1/events", {"thread_id": thread["id"], "title": "x", "duration_minutes": 90, "candidates": [iso(19)]}),
            ("PATCH", "/v1/me", {"display_name": "Hacked"}),
        ):
            status, payload = request(method, self.url + path, body, self.sam["read_token"])
            self.assertEqual(status, 403, payload)
            self.assertEqual(payload["error"], "read_only")
        status, stranger = request(
            "GET", self.url + "/v1/threads/%s/messages" % thread["id"], token=self.alex["read_token"]
        )
        self.assertEqual(status, 200)
        outsider = request("POST", self.url + "/v1/agents", {"handle": "outsider", "display_name": "Out"})[1]
        status, missing = request(
            "GET",
            self.url + "/v1/threads/%s" % thread["id"],
            token=outsider["read_token"],
        )
        self.assertEqual(status, 404)

    def test_join_key_gates_registration(self):
        os.environ["RELAY_JOIN_KEY"] = "secret-key"
        try:
            status, _payload = request(
                "POST", self.url + "/v1/agents", {"handle": "new", "display_name": "New"}
            )
            self.assertEqual(status, 403)
            status, created = request(
                "POST",
                self.url + "/v1/agents",
                {"handle": "new", "display_name": "New", "join_key": "secret-key"},
            )
            self.assertEqual(status, 201)
            self.assertIn("write_token", created)
        finally:
            del os.environ["RELAY_JOIN_KEY"]


class ViewerTests(unittest.TestCase):
    def test_page_has_no_composer(self):
        self.assertNotIn("innerHTML", HTML)
        self.assertNotIn("<form", HTML)
        self.assertNotIn("<textarea", HTML)
        self.assertNotIn("<input", HTML)
        self.assertIn("You cannot type here", HTML)

    def test_proxy_rejects_writes_and_hides_tokens(self):
        tmp = tempfile.mkdtemp()
        relay = Relay(os.path.join(tmp, "relay.db"))
        api = RelayHTTPServer(("127.0.0.1", 0), relay)
        api_thread = threading.Thread(target=api.serve_forever)
        api_thread.daemon = True
        api_thread.start()
        api_url = "http://127.0.0.1:%s" % api.server_address[1]
        created = request("POST", api_url + "/v1/agents", {"handle": "alex", "display_name": "Alex"})[1]
        viewer = ReadOnlyViewer(("127.0.0.1", 0), api_url, created["read_token"])
        viewer_thread = threading.Thread(target=viewer.serve_forever)
        viewer_thread.daemon = True
        viewer_thread.start()
        view = "http://127.0.0.1:%s" % viewer.server_address[1]
        try:
            status, _payload = request("GET", view + "/healthz")
            self.assertEqual(status, 200)
            page = urllib.request.urlopen(view + "/", timeout=5)
            body = page.read().decode("utf-8")
            self.assertNotIn(created["write_token"], body)
            self.assertNotIn(created["read_token"], body)
            status, me = request("GET", view + "/api/me")
            self.assertEqual(status, 200)
            self.assertEqual(me["handle"], "alex")
            self.assertNotIn("write_token", me)
            status, denied = request(
                "POST",
                view + "/api/threads",
                {"kind": "dm", "handle": "sam"},
                token=created["write_token"],
            )
            self.assertEqual(status, 405)
            self.assertEqual(denied["error"], "read_only")
            status, missing = request("GET", view + "/api/invites")
            self.assertEqual(status, 404)
        finally:
            viewer.shutdown()
            viewer.server_close()
            api.shutdown()
            api.server_close()
            relay.close()
            shutil.rmtree(tmp)


class McpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.relay = Relay(os.path.join(self.tmp, "server", "relay.db"))
        self.server = RelayHTTPServer(("127.0.0.1", 0), self.relay)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.url = "http://127.0.0.1:%s" % self.server.server_address[1]
        self.old = {key: os.environ.get(key) for key in ("RELAY_HOME", "RELAY_URL", "RELAY_AUTOSTART", "RELAY_JOIN_KEY")}
        os.environ["RELAY_HOME"] = self.tmp
        os.environ["RELAY_URL"] = self.url
        os.environ["RELAY_AUTOSTART"] = "0"
        os.environ.pop("RELAY_JOIN_KEY", None)

    def tearDown(self):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.server.shutdown()
        self.server.server_close()
        self.relay.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_hides_tokens_and_agents_can_plan(self):
        registered = call_tool("register", {"handle": "alex", "display_name": "Alex"})
        self.assertNotIn("write_token", registered)
        self.assertNotIn("read_token", json.dumps(registered))
        saved = load_identity()
        self.assertTrue(saved["write_token"])
        os.environ["RELAY_HOME"] = os.path.join(self.tmp, "sam")
        call_tool("register", {"handle": "sam", "display_name": "Sam"})
        sam = load_identity()
        os.environ["RELAY_HOME"] = self.tmp
        invite = call_tool("create_invite", {})
        os.environ["RELAY_HOME"] = os.path.join(self.tmp, "sam")
        save_identity(sam)
        call_tool("accept_invite", {"code": invite["invite_code"]})
        os.environ["RELAY_HOME"] = self.tmp
        save_identity(saved)
        thread = call_tool("open_dm", {"handle": "sam"})
        call_tool("send", {"thread_id": thread["id"], "text": "Alex can do Wednesday after 7."})
        plan = call_tool(
            "propose_event",
            {
                "thread_id": thread["id"],
                "title": "Dinner",
                "duration_minutes": 90,
                "candidates": [iso(19)],
                "location": "Cafe Presse",
            },
        )
        call_tool("rsvp_event", {"event_id": plan["id"], "choice": "accept", "slot_index": 0})
        os.environ["RELAY_HOME"] = os.path.join(self.tmp, "sam")
        save_identity(sam)
        digest = call_tool("digest", {})
        self.assertEqual(digest["unread_threads"][0]["id"], thread["id"])
        agreed = call_tool("rsvp_event", {"event_id": plan["id"], "choice": "accept", "slot_index": 0})
        self.assertEqual(agreed["status"], "agreed")
        call_tool("mark_booked", {"event_id": plan["id"], "provider": "google"})
        page = call_tool("read_thread", {"thread_id": thread["id"]})
        self.assertTrue(all(message["author_kind"] == "agent" for message in page["messages"]))
        from relay.runtime import ensure_viewer

        viewer_url = ensure_viewer()
        status, health = request("GET", viewer_url + "/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(health["read_only"])
        html = urllib.request.urlopen(viewer_url + "/", timeout=5).read().decode("utf-8")
        self.assertNotIn(sam["read_token"], html)
        self.assertNotIn(sam["write_token"], html)
        viewer_pid = json.loads((Path(os.environ["RELAY_HOME"]) / "viewer.json").read_text(encoding="utf-8"))["pid"]
        os.kill(viewer_pid, signal.SIGTERM)

    def test_stdio_roundtrip(self):
        env = os.environ.copy()
        env["RELAY_HOME"] = self.tmp
        env["RELAY_URL"] = self.url
        env["RELAY_AUTOSTART"] = "0"
        env["PYTHONPATH"] = str(PLUGIN_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-m", "relay", "mcp"],
            cwd=str(PLUGIN_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        blobs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
        ]
        payload = b"".join(_frame(item) for item in blobs)
        out, err = proc.communicate(payload, timeout=10)
        self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace"))
        messages = list(_frames(out))
        self.assertEqual(messages[0]["id"], 1)
        self.assertEqual(messages[0]["result"]["serverInfo"]["name"], "relay")
        self.assertEqual(messages[1]["id"], 2)
        body = json.loads(messages[1]["result"]["content"][0]["text"])
        self.assertFalse(body["registered"])
        self.assertIn("send", [tool["name"] for tool in handle_message(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
        )["result"]["tools"]])


class AutostartTests(unittest.TestCase):
    def test_local_server_starts_when_missing(self):
        tmp = tempfile.mkdtemp()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        old = {key: os.environ.get(key) for key in ("RELAY_HOME", "RELAY_URL", "RELAY_AUTOSTART")}
        os.environ["RELAY_HOME"] = tmp
        os.environ["RELAY_URL"] = "http://127.0.0.1:%s" % port
        os.environ["RELAY_AUTOSTART"] = "1"
        pid = None
        try:
            from relay.runtime import ensure_relay

            ensure_relay(os.environ["RELAY_URL"])
            status, payload = request("GET", os.environ["RELAY_URL"] + "/health")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            pid = int(Path(tmp, "server.pid").read_text(encoding="utf-8"))
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if pid:
                os.kill(pid, signal.SIGTERM)
            shutil.rmtree(tmp, ignore_errors=True)


def _frame(payload):
    data = json.dumps(payload).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n" % len(data) + data


def _frames(raw):
    import io

    stream = io.BytesIO(raw)
    while True:
        message = read_frame(stream)
        if message is None:
            break
        yield message


if __name__ == "__main__":
    unittest.main()
