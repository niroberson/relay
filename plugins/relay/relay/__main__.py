"""Command line for the relay server, the read-only viewer, and the MCP server."""

import argparse
import json
import sys

from relay.runtime import describe_status


HELP = """\
Relay lets two or more Grok agents talk, and lets each human watch.

  python3 -m relay serve     Start the shared server (default 127.0.0.1:8788)
  python3 -m relay viewer    Open the local read-only transcript
  python3 -m relay mcp       Speak MCP on stdin/stdout (Grok launches this)
  python3 -m relay status    Show registration without printing tokens

Friends on different machines share one server. Set RELAY_URL to that server
before registering. Set RELAY_JOIN_KEY on the server and on each agent when
the server is reachable beyond localhost. Keep a bare server on a private
network such as Tailscale or an SSH tunnel.
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(HELP)
        return 0
    command = argv[0]
    if command == "serve":
        return _serve(argv[1:])
    if command == "viewer":
        return _viewer(argv[1:])
    if command == "mcp":
        from relay.mcp_server import run

        run()
        return 0
    if command == "status":
        sys.stdout.write(json.dumps(describe_status(), indent=2) + "\n")
        return 0
    sys.stderr.write("Unknown command %s.\n%s" % (command, HELP))
    return 2


def _serve(argv):
    parser = argparse.ArgumentParser(prog="relay serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--data", default=None)
    args = parser.parse_args(argv)
    from pathlib import Path

    from relay.http_server import serve
    from relay.identity import server_dir
    from relay.store import Relay

    data = Path(args.data) if args.data else server_dir()
    data.mkdir(parents=True, mode=0o700, exist_ok=True)
    relay = Relay(str(data / "relay.db"))
    serve(relay, args.host, args.port)
    return 0


def _viewer(argv):
    parser = argparse.ArgumentParser(prog="relay viewer")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args(argv)
    from relay.viewer import serve_viewer

    return serve_viewer(args.port)


if __name__ == "__main__":
    sys.exit(main())
