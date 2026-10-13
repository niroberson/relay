"""Local agent identity. Tokens stay in this file and never go back to the model."""

import json
import os
from pathlib import Path


def relay_dir():
    override = os.environ.get("RELAY_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".grok" / "relay"


def identity_path():
    return relay_dir() / "identity.json"


def server_dir():
    return relay_dir() / "server"


def load_identity():
    path = identity_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    needed = ("relay_url", "handle", "display_name", "agent_id", "write_token", "read_token")
    if not all(isinstance(data.get(key), str) and data.get(key) for key in needed):
        return None
    return data


def save_identity(data):
    path = identity_path()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    payload = json.dumps(data, indent=2) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def public_identity(data):
    if not data:
        return {"registered": False}
    return {
        "registered": True,
        "relay_url": data["relay_url"],
        "handle": data["handle"],
        "display_name": data["display_name"],
        "agent_id": data["agent_id"],
    }
