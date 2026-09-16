"""An MCP server over stdio. Protocol plumbing only; every behaviour is in `tools.py`.

Deliberately hand-written JSON-RPC rather than a framework: the whole point of this server is
that it is small enough to read in one sitting and prove nothing hides in it. It speaks
`initialize`, `tools/list` and `tools/call` and refuses everything else.

Transport is stdio. **No port, no HTTP, no public exposure, no tunnel.** A local surface that
binds nothing is outside the port registry rules entirely, and that is the correct shape for
v1's `local-attended` location class.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import tools                                                   # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER = {"name": "infinity-os-runtime", "version": "1.0.0"}


def _tool_list() -> list:
    out = []
    for name, t in sorted(tools.TOOLS.items()):
        desc = t["description"]
        if t.get("verb"):
            owner = f" (owned by {t['owner']})" if t.get("owner") else ""
            desc += f"\n\nWraps the verb `{t['verb']}`{owner} and adds no logic of its own."
        out.append({"name": name, "description": desc, "inputSchema": t["schema"]})
    return out


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")

    if method == "initialize":
        return _ok(mid, {"protocolVersion": PROTOCOL_VERSION,
                         "capabilities": {"tools": {}},
                         "serverInfo": SERVER})
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": _tool_list()})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result = tools.call(name, args)
        except Exception as exc:                                        # noqa: BLE001
            # A refusal is a tool result, not a protocol error: the model has to be able to read
            # WHY it was refused and do something else. A JSON-RPC error is invisible to it.
            return _ok(mid, {"isError": True,
                             "content": [{"type": "text",
                                          "text": f"{exc.__class__.__name__}: {exc}"}]})
        return _ok(mid, {"content": [{"type": "text",
                                      "text": json.dumps(result, indent=2, default=str)}]})

    return _err(mid, -32601, f"method not supported: {method}. This server speaks initialize, "
                             f"tools/list and tools/call, and nothing else.")


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(_err(None, -32700, f"parse error: {exc}"))
            continue
        try:
            out = handle(msg)
        except Exception:                                               # noqa: BLE001
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            out = _err(msg.get("id"), -32603, "internal error; see the server's stderr")
        if out is not None:
            _write(out)
    return 0


def _write(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
