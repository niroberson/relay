"""Start the shared server and the local viewer when a session needs them."""

import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

from relay.client import server_healthy
from relay.errors import RelayClientError
from relay.identity import identity_path, load_identity, relay_dir, server_dir

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DEFAULT_URL = "http://127.0.0.1:8788"


def plugin_root():
    return Path(__file__).resolve().parent.parent


def resolve_relay_url(identity=None):
    env = os.environ.get("RELAY_URL", "").strip()
    if env:
        return env.rstrip("/")
    if identity and identity.get("relay_url"):
        return identity["relay_url"].rstrip("/")
    return DEFAULT_URL


def split_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RelayClientError(400, "bad_url", "RELAY_URL must be an http(s) URL.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, port


def ensure_relay(url):
    if server_healthy(url):
        return url
    scheme, host, port = split_url(url)
    autostart = os.environ.get("RELAY_AUTOSTART", "1") != "0"
    if scheme != "http" or host not in LOOPBACK or not autostart:
        raise RelayClientError(
            503,
            "server_down",
            "Relay is not reachable at %s. Start it with `python3 -m relay serve` on a machine both friends can reach, then set RELAY_URL."
            % url,
        )
    _spawn_server(host, port)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if server_healthy(url):
            return url
        time.sleep(0.05)
    raise RelayClientError(503, "server_down", "The local relay did not start on %s." % url)


def _spawn_server(host, port):
    import fcntl

    root = relay_dir()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    data = server_dir()
    data.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = root / "server.lock"
    log_path = root / "server.log"
    with open(lock_path, "a", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        if server_healthy("http://%s:%s" % (host, port)):
            return
        pid = _spawn(
            [
                sys.executable,
                "-m",
                "relay",
                "serve",
                "--host",
                host,
                "--port",
                str(port),
                "--data",
                str(data),
            ],
            log_path,
        )
        (root / "server.pid").write_text(str(pid) + "\n", encoding="utf-8")


def viewer_state_path():
    return relay_dir() / "viewer.json"


def read_viewer_url():
    try:
        data = json.loads(viewer_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url = data.get("url")
    if not isinstance(url, str):
        return None
    if _viewer_up(url):
        return url
    return None


def _viewer_up(url):
    import urllib.request

    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=0.4) as res:
            return res.status == 200
    except Exception:
        return False


def ensure_viewer():
    identity = load_identity()
    if not identity:
        raise RelayClientError(400, "not_registered", "Register this agent before opening the viewer.")
    existing = read_viewer_url()
    if existing:
        return existing
    import fcntl

    root = relay_dir()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = root / "viewer.lock"
    with open(lock_path, "a", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        existing = read_viewer_url()
        if existing:
            return existing
        port = _free_viewer_port()
        url = "http://127.0.0.1:%s" % port
        pid = _spawn([sys.executable, "-m", "relay", "viewer", "--port", str(port)], root / "viewer.log")
        viewer_state_path().write_text(
            json.dumps({"pid": pid, "port": port, "url": url}) + "\n",
            encoding="utf-8",
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _viewer_up(url):
            return url
        time.sleep(0.05)
    raise RelayClientError(503, "viewer_down", "The read-only viewer did not start.")


def _spawn(argv, log_path):
    """Start a detached process. posix_spawn avoids a Popen handle we would have to leak."""
    root = str(plugin_root())
    env = os.environ.copy()
    previous = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not previous else root + os.pathsep + previous
    log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    devnull = os.open(os.devnull, os.O_RDWR)
    try:
        return os.posix_spawn(
            argv[0],
            argv,
            env,
            file_actions=[
                (os.POSIX_SPAWN_DUP2, devnull, 0),
                (os.POSIX_SPAWN_DUP2, log_fd, 1),
                (os.POSIX_SPAWN_DUP2, log_fd, 2),
                (os.POSIX_SPAWN_CLOSE, devnull),
                (os.POSIX_SPAWN_CLOSE, log_fd),
            ],
            setsid=True,
        )
    finally:
        os.close(log_fd)
        os.close(devnull)


def _free_viewer_port():
    import socket

    preferred = int(os.environ.get("RELAY_VIEWER_PORT", "8790") or "8790")
    for port in range(preferred, preferred + 20):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            sock.close()
        return port
    raise RelayClientError(503, "viewer_down", "No free local port for the viewer.")


def describe_status():
    identity = load_identity()
    url = resolve_relay_url(identity)
    return {
        "registered": bool(identity),
        "handle": identity["handle"] if identity else None,
        "display_name": identity["display_name"] if identity else None,
        "relay_url": url,
        "server_up": server_healthy(url),
        "viewer_url": read_viewer_url(),
        "identity_file": str(identity_path()),
    }
